from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .credentials import GA4Admin
from .ga4 import EVENT_FIELDS, GA4Reporter
from .storage import Database, canonical_hash, stable_id

ROOT = Path(__file__).resolve().parents[1]


class SyncEngine:
    def __init__(self, database: Database, reporter: GA4Reporter, admin: GA4Admin | None = None, site=None):
        self.database = database
        self.reporter = reporter
        self.admin = admin
        self.site = site

    def run(self, period: str, scheduled_for: str | None = None, final_attempt: bool = False, assignment_id=None) -> dict:
        scheduled_for = scheduled_for or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        identifiers = self.database.identifiers()
        assignment_id = assignment_id or identifiers["first_assignment"]
        report_version_id = identifiers["report_bundle_v1"]
        sync_run_id = stable_id(f"sync-run:{assignment_id}:{scheduled_for}:{period}")
        execution_key = canonical_hash({"assignment":str(assignment_id),"report":"website_reporting_bundle_v1","period":period,"scheduledFor":scheduled_for})
        job_id = stable_id(f"sync-job:{execution_key}")
        with self.database.connection() as connection:
            connection.execute("""
                INSERT INTO analytics.sync_runs(id,assignment_id,status,trigger)
                VALUES(%s,%s,'running',%s)
                ON CONFLICT(id) DO UPDATE SET status='running',completed_at=NULL,error_code=NULL,error_detail=NULL
            """,(sync_run_id,assignment_id,"scheduled" if scheduled_for else "manual"))
            connection.execute("""
                INSERT INTO analytics.sync_jobs(
                  id,sync_run_id,assignment_id,report_definition_version_id,requested_start_date,
                  requested_end_date,idempotency_key,status,period_key,scheduled_for,payload_json
                ) VALUES(%s,%s,%s,%s,current_date,current_date,%s,'queued',%s,%s,%s)
                ON CONFLICT(idempotency_key) DO NOTHING
            """, (job_id,sync_run_id,assignment_id,report_version_id,execution_key,period,scheduled_for,json.dumps({"period":period,"scheduledFor":scheduled_for,"assignmentId":str(assignment_id)})))
            job = connection.execute("SELECT id,status,attempt_count FROM analytics.sync_jobs WHERE idempotency_key=%s",(execution_key,)).fetchone()
            if job["status"] == "succeeded":
                return {"status":"succeeded","idempotentReplay":True,"jobId":str(job["id"]),"period":period}
            connection.execute("UPDATE analytics.sync_jobs SET status='running',attempt_count=attempt_count+1,error_code=NULL,error_detail=NULL WHERE id=%s",(job["id"],))
        try:
            compatibility = self._validate_compatibility()
            if not compatibility["compatible"]:
                raise RuntimeError("ga4_report_definition_incompatible")
            if self.admin and self.site and period == "7d":
                health = self.admin.configuration_health(self.site)
                self.database.record_measurement_health(assignment_id,health)
                if any(item["state"] == "error" for item in health["checks"]):
                    raise RuntimeError("ga4_admin_configuration_invalid")
            bundle = self.reporter.bundle(period)
            bundle["compatibility"] = compatibility
            result = self._persist(job["id"], sync_run_id, assignment_id, report_version_id, execution_key, scheduled_for, bundle)
            self.database.set_alert(assignment_id,"sync_failed","high",{"period":period,"state":"healthy"},False)
            self.database.set_alert(assignment_id,"tracking_stale","medium",{"period":period,"empty":bundle["quality"]["empty"]},bundle["quality"]["empty"])
            return result
        except Exception as error:
            with self.database.connection() as connection:
                connection.execute("UPDATE analytics.sync_jobs SET status='failed',completed_at=now(),error_code=%s,error_detail=%s,next_attempt_at=CASE WHEN %s THEN NULL ELSE now()+interval '15 minutes' END,dead_lettered_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s",(type(error).__name__,str(error)[:1000],final_attempt,final_attempt,job["id"]))
                connection.execute("UPDATE analytics.sync_runs SET status='failed',completed_at=now(),error_code=%s,error_detail=%s WHERE id=%s",(type(error).__name__,str(error)[:1000],sync_run_id))
            self.database.set_alert(assignment_id,"sync_failed","critical" if final_attempt else "high",{"period":period,"errorCode":type(error).__name__,"deadLettered":final_attempt},True)
            raise

    def _validate_compatibility(self):
        definitions=json.loads((ROOT / "measurement/reports/v1.json").read_text())["reports"]
        checks=[]
        for definition in definitions:
            result=self.reporter.check_compatibility(definition["dimensions"],definition["metrics"])
            checks.append({"report":definition["slug"],**result})
        return {"compatible":all(item["compatible"] for item in checks),"checks":checks}

    def _persist(self, job_id, sync_run_id, assignment_id, report_version_id, execution_key, scheduled_for, bundle):
        start, end = bundle["dateRange"]["start"], bundle["dateRange"]["end"]
        request_hash = canonical_hash({"period":bundle["period"],"dateRange":bundle["dateRange"],"report":"website_reporting_bundle_v1"})
        response_hash = canonical_hash(bundle)
        execution_id = stable_id(f"report-execution:{execution_key}")
        provenance = bundle["provenance"]
        timezone_name = next((item["property_timezone"] for item in provenance if item.get("property_timezone")),None)
        currency = next((item["currency_code"] for item in provenance if item.get("currency_code")),None)
        empty_reasons = [item["empty_reason"] for item in provenance if item.get("empty_reason")]
        quotas = [item["property_quota"] for item in provenance if item.get("property_quota")]
        restrictions = [item["schema_restriction"] for item in provenance if item.get("schema_restriction")]
        sampling = [item["sampling_metadata"] for item in provenance if item.get("sampling_metadata")]
        source_requests = [item["request"] for item in provenance]
        source_metadata = [{key:item.get(key) for key in ("row_count","property_timezone","currency_code","data_loss_from_other_row","empty_reason","schema_restriction","subject_to_thresholding","sampling_metadata","property_quota","date_range")} for item in provenance]
        with self.database.connection() as connection:
            connection.execute("""
                INSERT INTO analytics.report_executions(
                  id,assignment_id,report_definition_version_id,sync_job_id,requested_start_date,
                  requested_end_date,request_hash,response_hash,property_time_zone,currency_code,
                  empty_reason,subject_to_thresholding,data_loss_from_other_row,sampling_metadata_json,
                  schema_restrictions_json,property_quota_json,source_requests_json,source_metadata_json,
                  status,completed_at,execution_key
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'succeeded',now(),%s)
                ON CONFLICT(execution_key) DO UPDATE SET response_hash=excluded.response_hash,
                  completed_at=excluded.completed_at,status='succeeded',empty_reason=excluded.empty_reason,
                  property_quota_json=excluded.property_quota_json
            """, (execution_id,assignment_id,report_version_id,job_id,start,end,request_hash,response_hash,timezone_name,currency,";".join(empty_reasons) or None,any(item["subject_to_thresholding"] for item in provenance),any(item["data_loss_from_other_row"] for item in provenance),json.dumps(sampling),json.dumps(restrictions),json.dumps(quotas),json.dumps(source_requests),json.dumps(source_metadata),execution_key))
            for view_slug, payload in bundle["views"].items():
                clean_payload = json.loads(json.dumps(payload))
                connection.execute("""
                    INSERT INTO analytics.report_snapshots(
                      execution_id,assignment_id,view_slug,period_key,freshness,quality_status,payload
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(execution_id,view_slug,period_key) DO UPDATE SET
                      freshness=excluded.freshness,quality_status=excluded.quality_status,payload=excluded.payload,created_at=now()
                """,(execution_id,assignment_id,view_slug,bundle["period"],bundle["quality"]["freshness"],bundle["quality"]["status"],json.dumps(clean_payload)))
            self._persist_period_metrics(connection,execution_id,assignment_id,bundle)
            self._persist_daily_facts(connection,execution_id,assignment_id,bundle)
            connection.execute("""
                INSERT INTO analytics.data_quality_status(assignment_id,freshness,status,details_json,checked_at,last_successful_sync_at)
                VALUES(%s,%s,%s,%s,now(),now())
                ON CONFLICT(assignment_id) DO UPDATE SET freshness=excluded.freshness,status=excluded.status,
                  details_json=excluded.details_json,checked_at=now(),last_successful_sync_at=now()
            """,(assignment_id,bundle["quality"]["freshness"],bundle["quality"]["status"],json.dumps(bundle["quality"])))
            connection.execute("UPDATE analytics.sync_jobs SET status='succeeded',completed_at=now(),requested_start_date=%s,requested_end_date=%s,error_code=NULL,error_detail=NULL WHERE id=%s",(start,end,job_id))
            connection.execute("UPDATE analytics.sync_runs SET status='succeeded',completed_at=now() WHERE id=%s",(sync_run_id,))
        return {"status":"succeeded","idempotentReplay":False,"jobId":str(job_id),"reportExecutionId":str(execution_id),"period":bundle["period"],"dateRange":bundle["dateRange"],"quality":bundle["quality"]}

    @staticmethod
    def _persist_period_metrics(connection, execution_id, assignment_id, bundle):
        for item in bundle["views"]["overview"]["metrics"]:
            metric_version_id = stable_id(f"metric:{item['metric']}:v1")
            value = item["value"]
            numerator = denominator = None
            if item["metric"] == "appointment_request_rate":
                values = {entry["metric"]:entry["value"] for entry in bundle["views"]["overview"]["metrics"]}
                numerator,denominator = values["appointment_requests"],values["sessions"]
            connection.execute("""
                INSERT INTO analytics.period_metric_snapshots(
                  report_execution_id,assignment_id,metric_definition_version_id,period_key,
                  start_date,end_date,value_numeric,numerator_numeric,denominator_numeric,freshness
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(report_execution_id,metric_definition_version_id,period_key) DO UPDATE SET
                  value_numeric=excluded.value_numeric,numerator_numeric=excluded.numerator_numeric,
                  denominator_numeric=excluded.denominator_numeric,freshness=excluded.freshness
            """,(execution_id,assignment_id,metric_version_id,bundle["period"],bundle["dateRange"]["start"],bundle["dateRange"]["end"],value,numerator,denominator,bundle["quality"]["freshness"]))

    @staticmethod
    def _persist_daily_facts(connection, execution_id, assignment_id, bundle):
        for row in bundle["daily"]["property"]["rows"]:
            metric_date = datetime.strptime(row["dimensions"][0],"%Y%m%d").date()
            for slug,index in (("active_users",0),("sessions",1)):
                connection.execute("""
                    INSERT INTO analytics.daily_property_metrics(
                      report_execution_id,assignment_id,metric_definition_version_id,metric_date,value_numeric
                    ) VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(report_execution_id,metric_definition_version_id,metric_date) DO UPDATE SET value_numeric=excluded.value_numeric
                """,(execution_id,assignment_id,stable_id(f"metric:{slug}:v1"),metric_date,int(row["metrics"][index] or 0)))
        for row in bundle["daily"]["events"]["rows"]:
            metric_date = datetime.strptime(row["dimensions"][0],"%Y%m%d").date()
            connection.execute("""
                INSERT INTO analytics.daily_event_metrics(report_execution_id,assignment_id,event_name,metric_date,event_count)
                VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(report_execution_id,event_name,metric_date) DO UPDATE SET event_count=excluded.event_count
            """,(execution_id,assignment_id,row["dimensions"][1],metric_date,int(row["metrics"][0] or 0)))
        for row in bundle["daily"]["channels"]["rows"]:
            metric_date = datetime.strptime(row["dimensions"][0],"%Y%m%d").date()
            connection.execute("""
                INSERT INTO analytics.daily_channel_metrics(report_execution_id,assignment_id,channel,metric_date,metrics_json)
                VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(report_execution_id,channel,metric_date) DO UPDATE SET metrics_json=excluded.metrics_json
            """,(execution_id,assignment_id,row["dimensions"][1] or "(not set)",metric_date,json.dumps({"sessions":int(row["metrics"][0] or 0),"activeUsers":int(row["metrics"][1] or 0)})))
        for row in bundle["daily"]["pages"]["rows"]:
            metric_date = datetime.strptime(row["dimensions"][0],"%Y%m%d").date()
            from .ga4 import safe_landing_page
            connection.execute("""
                INSERT INTO analytics.daily_page_metrics(report_execution_id,assignment_id,landing_page,metric_date,metrics_json)
                VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(report_execution_id,landing_page,metric_date) DO UPDATE SET metrics_json=excluded.metrics_json
            """,(execution_id,assignment_id,safe_landing_page(row["dimensions"][1]),metric_date,json.dumps({"sessions":int(row["metrics"][0] or 0),"activeUsers":int(row["metrics"][1] or 0),"eventCount":int(row["metrics"][2] or 0)})))
        approved=connection.execute("""
            SELECT em.id mapping_id,ed.event_name,em.metric_definition_version_id,
                   concat('mapping:',em.id::text,':',em.effective_from::text) semantic_version_id
              FROM app.event_mappings em JOIN app.event_definitions ed ON ed.id=em.event_definition_id
             WHERE em.website_id=(SELECT website_id FROM app.website_analytics_assignments WHERE id=%s)
               AND em.approval_status='approved'
        """,(assignment_id,)).fetchall()
        event_values={(datetime.strptime(row["dimensions"][0],"%Y%m%d").date(),row["dimensions"][1]):int(row["metrics"][0] or 0) for row in bundle["daily"]["events"]["rows"]}
        for mapping in approved:
            for (metric_date,event_name),value in event_values.items():
                if event_name == mapping["event_name"]:
                    connection.execute("""
                        INSERT INTO analytics.daily_canonical_metrics(report_execution_id,assignment_id,event_mapping_version_id,metric_definition_version_id,semantic_version_id,metric_date,value_numeric)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(report_execution_id,event_mapping_version_id,metric_definition_version_id,metric_date) DO UPDATE SET value_numeric=excluded.value_numeric
                    """,(execution_id,assignment_id,mapping["mapping_id"],mapping["metric_definition_version_id"],mapping["semantic_version_id"],metric_date,value))
