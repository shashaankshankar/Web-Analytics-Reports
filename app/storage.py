from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import Settings, Site
from .auth import TenantContext

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = uuid.UUID("e43717fb-78e8-4b90-bf04-472c24f885fb")


def stable_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


def canonical_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class Database:
    def __init__(self, settings: Settings, pool=None):
        self.settings = settings
        self._pool = pool

    @property
    def configured(self) -> bool:
        return self.settings.database_enabled

    def _connection_string(self) -> str:
        if self.settings.database_url:
            return self.settings.database_url
        return make_conninfo(dbname=self.settings.db_name,user=self.settings.db_user,password=self.settings.db_password,host=self.settings.db_socket)

    @property
    def pool(self):
        if not self.configured:
            raise RuntimeError("database_not_configured")
        if self._pool is None:
            self._pool = ConnectionPool(
                self._connection_string(), min_size=1, max_size=5,
                kwargs={"row_factory": dict_row, "connect_timeout": 10},
                open=True,
            )
        return self._pool

    @contextmanager
    def connection(self):
        with self.pool.connection() as connection:
            yield connection

    def close(self):
        if self._pool is not None:
            self._pool.close()

    def health(self) -> dict:
        if not self.configured:
            return {"status": "disabled", "reason": "database_not_configured"}
        try:
            with self.connection() as connection:
                row = connection.execute(
                    "SELECT current_database() database, now() checked_at, "
                    "to_regclass('analytics.report_snapshots') IS NOT NULL migrated"
                ).fetchone()
            return {"status": "ok" if row["migrated"] else "migration_required", **row}
        except Exception as error:
            return {"status": "error", "reason": type(error).__name__}

    def migrate(self):
        with self.connection() as connection:
            exists = connection.execute(
                "SELECT to_regclass('app.organizations') IS NOT NULL AS exists"
            ).fetchone()["exists"]
            if not exists:
                connection.execute((ROOT / "infra/postgres/001_core.sql").read_text())
            connection.execute((ROOT / "infra/postgres/002_production.sql").read_text())
            connection.execute((ROOT / "infra/postgres/003_phase4_tenant_isolation.sql").read_text())
        return {"status": "ok", "migration": "003_phase4_tenant_isolation"}

    def seed_first_site(self, site: Site) -> dict:
        ids = {
            "organization": stable_id("organization:agency"),
            "company": stable_id(f"company:{site.company_id}"),
            "website": stable_id(f"website:{site.site_id}"),
            "connection": stable_id("connection:ga4:adc"),
            "property": stable_id(f"ga4-property:{site.property_id}"),
            "stream": stable_id(f"ga4-stream:{site.stream_id}"),
            "assignment": stable_id(f"assignment:{site.site_id}:{site.property_id}:{site.stream_id}"),
            "contract": stable_id("contract:local_service_v1"),
            "contract_version": stable_id("contract:local_service_v1:v1"),
            "contract_assignment": stable_id(f"contract-assignment:{site.site_id}:v1"),
            "report": stable_id("report:website_reporting_bundle"),
            "report_version": stable_id("report:website_reporting_bundle:v1"),
            "operator_user": stable_id(f"user:{self.settings.operator_email}"),
        }
        contract = json.loads((ROOT / "measurement/contracts/local_service_v1/contract.json").read_text())
        events = json.loads((ROOT / "measurement/contracts/local_service_v1/events.json").read_text())["events"]
        mappings_document = json.loads((ROOT / "measurement/contracts/local_service_v1/mappings.json").read_text())
        mappings = mappings_document["mappings"]
        reports = json.loads((ROOT / "measurement/reports/v1.json").read_text())
        metric_aggregation = {
            "generated_leads": "SUM", "appointment_requests": "SUM", "form_intent": "SUM",
            "technical_submissions": "SUM", "phone_intent": "SUM", "email_intent": "SUM",
            "cta_engagement": "SUM", "active_users": "WINDOWED_UNIQUE", "sessions": "SNAPSHOT",
            "appointment_request_rate": "RATIO",
        }
        with self.connection() as connection:
            connection.execute("INSERT INTO app.organizations(id,name) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (ids["organization"], "Agency"))
            connection.execute("INSERT INTO app.users(id,email) VALUES(%s,%s) ON CONFLICT(email) DO UPDATE SET email=excluded.email", (ids["operator_user"], self.settings.operator_email))
            connection.execute("INSERT INTO app.memberships(organization_id,user_id,role) VALUES(%s,%s,'agency_owner') ON CONFLICT(organization_id,user_id) DO UPDATE SET role=excluded.role", (ids["organization"], ids["operator_user"]))
            connection.execute("INSERT INTO app.companies(id,organization_id,name) VALUES(%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (ids["company"], ids["organization"], site.company))
            connection.execute("INSERT INTO app.websites(id,company_id,canonical_domain,healthcare_eligibility) VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET canonical_domain=excluded.canonical_domain,healthcare_eligibility=excluded.healthcare_eligibility", (ids["website"], ids["company"], site.canonical_domain, "requires_review" if site.governance_status != "approved" else "approved"))
            connection.execute("INSERT INTO app.resource_identifiers(organization_id,resource_type,resource_id,public_id) VALUES(%s,'company',%s,%s),(%s,'website',%s,%s) ON CONFLICT(organization_id,resource_type,public_id) DO UPDATE SET resource_id=excluded.resource_id", (ids["organization"],ids["company"],site.company_id,ids["organization"],ids["website"],site.site_id))
            connection.execute("INSERT INTO app.analytics_connections(id,organization_id,credential_type,credential_reference,status) VALUES(%s,%s,'service_account','application_default_credentials','approved') ON CONFLICT(id) DO UPDATE SET status='approved'", (ids["connection"], ids["organization"]))
            connection.execute("INSERT INTO app.ga_properties(id,analytics_connection_id,external_property_id,display_name,timezone) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET timezone=excluded.timezone", (ids["property"], ids["connection"], site.property_id, site.company, site.property_timezone))
            connection.execute("INSERT INTO app.ga_data_streams(id,ga_property_id,external_stream_id,display_name,stream_type) VALUES(%s,%s,%s,%s,'WEB_DATA_STREAM') ON CONFLICT(id) DO NOTHING", (ids["stream"], ids["property"], site.stream_id, site.canonical_domain))
            assignment = connection.execute("""
                INSERT INTO app.website_analytics_assignments(id,website_id,analytics_connection_id,ga_property_id,ga_stream_id,reporting_scope,effective_from,status)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'approved')
                ON CONFLICT(website_id,effective_from) WHERE effective_to IS NULL AND status='approved' DO UPDATE SET
                  analytics_connection_id=excluded.analytics_connection_id,
                  ga_property_id=excluded.ga_property_id,ga_stream_id=excluded.ga_stream_id,
                  reporting_scope=excluded.reporting_scope,status='approved'
                RETURNING id
            """, (ids["assignment"], ids["website"], ids["connection"], ids["property"], ids["stream"], json.dumps({"propertyId":site.property_id,"streamId":site.stream_id,"onePropertyPerWebsite":True}), date(2026,8,12))).fetchone()
            ids["assignment"] = assignment["id"]
            connection.execute("INSERT INTO app.measurement_contracts(id,slug,industry) VALUES(%s,'local_service_v1','healthcare') ON CONFLICT(id) DO NOTHING", (ids["contract"],))
            contract_approval = "approved" if contract.get("status") == "approved" else "pending_approval"
            connection.execute("INSERT INTO app.measurement_contract_versions(id,contract_id,version,definition_json,approval_status) VALUES(%s,%s,1,%s,%s) ON CONFLICT(id) DO UPDATE SET definition_json=excluded.definition_json,approval_status=excluded.approval_status", (ids["contract_version"], ids["contract"], json.dumps(contract),contract_approval))
            connection.execute("INSERT INTO app.website_measurement_contract_assignments(website_id,measurement_contract_version_id,effective_from,approval_status) VALUES(%s,%s,%s,%s) ON CONFLICT(website_id,effective_from) DO UPDATE SET measurement_contract_version_id=excluded.measurement_contract_version_id,approval_status=excluded.approval_status", (ids["website"], ids["contract_version"], date(2026,8,12),contract_approval))
            for event in events:
                event_id = stable_id(f"event:local_service_v1:v1:{event['name']}")
                connection.execute("INSERT INTO app.event_definitions(id,contract_version_id,event_name,definition_json) VALUES(%s,%s,%s,%s) ON CONFLICT(contract_version_id,event_name) DO UPDATE SET definition_json=excluded.definition_json", (event_id, ids["contract_version"], event["name"], json.dumps(event)))
            for slug, aggregation in metric_aggregation.items():
                metric_id = stable_id(f"metric:{slug}")
                version_id = stable_id(f"metric:{slug}:v1")
                connection.execute("INSERT INTO app.metric_definitions(id,slug) VALUES(%s,%s) ON CONFLICT(slug) DO NOTHING", (metric_id, slug))
                connection.execute("INSERT INTO app.metric_definition_versions(id,metric_definition_id,version,aggregation,definition_json,approval_status) VALUES(%s,%s,1,%s,%s,'approved') ON CONFLICT(metric_definition_id,version) DO UPDATE SET definition_json=excluded.definition_json,aggregation=excluded.aggregation", (version_id, metric_id, aggregation, json.dumps({"slug":slug,"aggregation":aggregation,"source":"ga4_reporting_api"})))
            metric_aliases = {"leads":"generated_leads"}
            mapping_approval = "approved" if mappings_document.get("status") == "approved" else "pending_approval"
            for mapping in mappings:
                slug = metric_aliases.get(mapping["metric"],mapping["metric"])
                mapping_id = stable_id(f"mapping:{site.site_id}:{mapping['source_event']}:{slug}:v1")
                connection.execute("""
                    INSERT INTO app.event_mappings(id,website_id,event_definition_id,metric_definition_version_id,effective_from,approval_status)
                    VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET approval_status=excluded.approval_status
                """,(mapping_id,ids["website"],stable_id(f"event:local_service_v1:v1:{mapping['source_event']}"),stable_id(f"metric:{slug}:v1"),date(2026,8,12),mapping_approval))
            connection.execute("INSERT INTO app.report_definitions(id,slug) VALUES(%s,'website_reporting_bundle') ON CONFLICT(slug) DO NOTHING", (ids["report"],))
            connection.execute("INSERT INTO app.report_definition_versions(id,report_definition_id,version,definition_json,storage_mode,approval_status) VALUES(%s,%s,1,%s,'ALL_RETURNED_ROWS','approved') ON CONFLICT(report_definition_id,version) DO UPDATE SET definition_json=excluded.definition_json", (ids["report_version"], ids["report"], json.dumps(reports)))
            connection.execute("INSERT INTO app.platform_identifiers(key,value_uuid,value_text) VALUES('first_assignment',%s,%s),('first_company',%s,%s),('first_website',%s,%s),('report_bundle_v1',%s,'website_reporting_bundle_v1') ON CONFLICT(key) DO UPDATE SET value_uuid=excluded.value_uuid,value_text=excluded.value_text", (ids["assignment"], site.site_id, ids["company"], site.company_id, ids["website"], site.site_id, ids["report_version"]))
        return {key: str(value) for key, value in ids.items()}

    def identifiers(self) -> dict:
        with self.connection() as connection:
            rows = connection.execute("SELECT key,value_uuid,value_text FROM app.platform_identifiers").fetchall()
        return {row["key"]: row["value_uuid"] or row["value_text"] for row in rows}

    def authorize_context(self, email: str, requested_organization_id: str | None = None) -> TenantContext:
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT o.id organization_id,u.id user_id,u.email,m.role
                  FROM app.users u
                  JOIN app.memberships m ON m.user_id=u.id
                  JOIN app.organizations o ON o.id=m.organization_id
                 WHERE lower(u.email)=lower(%s)
                   AND (%s IS NULL OR o.id::text=%s)
                 ORDER BY o.id
            """, (email, requested_organization_id, requested_organization_id)).fetchall()
        if not rows:
            raise PermissionError("membership_not_found")
        if len(rows) > 1 and requested_organization_id is None:
            raise PermissionError("organization_context_required")
        row = rows[0]
        return TenantContext(str(row["organization_id"]), str(row["user_id"]), row["email"], row["role"])

    @contextmanager
    def tenant_connection(self, context: TenantContext):
        with self.pool.connection() as connection:
            with connection.transaction():
                connection.execute("SET LOCAL ROLE measurement_tenant")
                connection.execute("SELECT set_config('app.organization_id',%s,true),set_config('app.user_id',%s,true),set_config('app.role',%s,true)", (context.organization_id, context.user_id, context.role))
                yield connection

    def company_authorized(self, context: TenantContext, company_id: str) -> bool:
        with self.tenant_connection(context) as connection:
            row = connection.execute("""
                SELECT EXISTS(
                  SELECT 1 FROM app.companies c
                  JOIN app.resource_identifiers p ON p.resource_type='company' AND p.resource_id=c.id
                  WHERE p.public_id=%s
                ) allowed
            """, (company_id,)).fetchone()
        return bool(row["allowed"])

    def website_authorized(self, context: TenantContext, website_id: str) -> bool:
        with self.tenant_connection(context) as connection:
            row = connection.execute("""
                SELECT EXISTS(
                  SELECT 1 FROM app.websites w
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=w.id
                  WHERE p.public_id=%s
                ) allowed
            """, (website_id,)).fetchone()
        return bool(row["allowed"])

    def latest_snapshot(self, context: TenantContext, website_id: str, view: str, period: str) -> dict | None:
        with self.tenant_connection(context) as connection:
            row = connection.execute(
                """SELECT s.payload,s.created_at,s.execution_id,s.freshness,s.quality_status
                     FROM analytics.current_report_snapshots s
                     JOIN app.website_analytics_assignments a ON a.id=s.assignment_id
                     JOIN app.websites w ON w.id=a.website_id
                     JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=w.id
                    WHERE p.public_id=%s AND s.view_slug=%s AND s.period_key=%s""",
                (website_id, view, period),
            ).fetchone()
        if not row:
            return None
        payload = row["payload"]
        payload["storedAt"] = row["created_at"].isoformat()
        payload["reportExecutionId"] = str(row["execution_id"])
        payload["freshness"] = row["freshness"]
        payload["qualityStatus"] = row["quality_status"]
        return payload

    def sync_status(self, context: TenantContext | None = None, website_id: str | None = None) -> dict:
        connection_manager = self.tenant_connection(context) if context else self.connection()
        with connection_manager as connection:
            assignment_filter = "" if not website_id else "AND assignment_id IN (SELECT a.id FROM app.website_analytics_assignments a JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=a.website_id WHERE p.public_id=%s)"
            parameters = () if not website_id else (website_id,)
            summary = connection.execute("""
                SELECT max(completed_at) FILTER (WHERE status='succeeded') last_successful_sync,
                       count(*) FILTER (WHERE status='queued') queued_jobs,
                       count(*) FILTER (WHERE status='failed') failed_jobs
                  FROM analytics.sync_jobs WHERE true """ + assignment_filter, parameters).fetchone()
            quality = connection.execute("SELECT freshness,status,details_json,checked_at FROM analytics.data_quality_status WHERE true " + assignment_filter + " ORDER BY checked_at DESC LIMIT 1", parameters).fetchone()
            errors = connection.execute("SELECT error_code,count(*) count FROM analytics.sync_jobs WHERE status='failed' " + assignment_filter + " GROUP BY error_code ORDER BY count(*) DESC", parameters).fetchall()
            alerts = connection.execute("SELECT alert_key,severity,detail_json,opened_at FROM analytics.operator_alerts WHERE state='open' " + assignment_filter + " ORDER BY opened_at DESC", parameters).fetchall()
            current_job = connection.execute("SELECT id,status,period_key,attempt_count,scheduled_for FROM analytics.sync_jobs WHERE status IN ('queued','running') " + assignment_filter + " ORDER BY scheduled_for LIMIT 1", parameters).fetchone()
            execution = connection.execute("SELECT requested_end_date,property_quota_json,subject_to_thresholding,data_loss_from_other_row FROM analytics.report_executions WHERE status='succeeded' " + assignment_filter + " ORDER BY completed_at DESC LIMIT 1", parameters).fetchone()
        return {
            "status": quality["status"] if quality else "never_synced",
            "lastSuccessfulSync": summary["last_successful_sync"].isoformat() if summary["last_successful_sync"] else None,
            "queuedJobs": summary["queued_jobs"], "failedJobs": summary["failed_jobs"],
            "freshness": quality["freshness"] if quality else None,
            "quality": quality["details_json"] if quality else {},
            "checkedAt": quality["checked_at"].isoformat() if quality else None,
            "errorCodes":[{"code":row["error_code"],"count":row["count"]} for row in errors],
            "alerts":[{"key":row["alert_key"],"severity":row["severity"],"detail":row["detail_json"],"openedAt":row["opened_at"].isoformat()} for row in alerts],
            "lastCompleteDate":execution["requested_end_date"].isoformat() if execution else None,
            "currentJob":{"id":str(current_job["id"]),"status":current_job["status"],"period":current_job["period_key"],"attemptCount":current_job["attempt_count"],"scheduledFor":current_job["scheduled_for"].isoformat()} if current_job else None,
            "reconciliationState":quality["freshness"] if quality else None,
            "quota":execution["property_quota_json"] if execution else [],
            "thresholding":execution["subject_to_thresholding"] if execution else False,
            "dimensionDataLoss":execution["data_loss_from_other_row"] if execution else False,
        }

    def portfolio_summary(self, context: TenantContext, period: str) -> dict:
        context.require_role(frozenset({"agency_owner", "agency_admin", "agency_analyst"}))
        with self.tenant_connection(context) as connection:
            rows = connection.execute("""
                SELECT p.public_id website_id,c.name company,w.canonical_domain,
                       w.healthcare_eligibility,a.id assignment_id,
                       mc.slug contract_slug,mcv.version contract_version,mca.approval_status contract_status
                  FROM app.websites w
                  JOIN app.companies c ON c.id=w.company_id
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=w.id
                  LEFT JOIN app.website_analytics_assignments a ON a.website_id=w.id AND a.effective_to IS NULL AND a.status='approved'
                  LEFT JOIN app.website_measurement_contract_assignments mca ON mca.website_id=w.id AND mca.effective_to IS NULL
                  LEFT JOIN app.measurement_contract_versions mcv ON mcv.id=mca.measurement_contract_version_id
                  LEFT JOIN app.measurement_contracts mc ON mc.id=mcv.contract_id
                 ORDER BY c.name,w.canonical_domain
            """).fetchall()
        websites = []
        contract_versions = set()
        for row in rows:
            sync = self.sync_status(context, row["website_id"])
            overview = self.latest_snapshot(context, row["website_id"], "overview", period) or {"metrics": []}
            primary = next((metric for metric in overview.get("metrics", []) if metric.get("metric") == "generated_leads"), None)
            contract_key = f"{row['contract_slug']}@{row['contract_version']}" if row["contract_slug"] else None
            if contract_key: contract_versions.add(contract_key)
            action_required = bool(sync.get("failedJobs") or sync.get("alerts") or row["contract_status"] != "approved")
            websites.append({
                "websiteId": row["website_id"], "company": row["company"], "canonicalDomain": row["canonical_domain"],
                "approvedPrimaryOutcome": primary, "periodChange": None if not primary else primary.get("value", 0) - (primary.get("previousValue") or 0),
                "measurementHealth": row["healthcare_eligibility"], "contract": contract_key,
                "lastCompleteDate": sync.get("lastCompleteDate"), "syncHealth": sync.get("status"),
                "alerts": sync.get("alerts", []), "actionRequired": action_required,
            })
        return {
            "organizationId": context.organization_id,
            "period": period,
            "measurementCompatibility": "compatible" if len(contract_versions) <= 1 else "incompatible_contract_versions",
            "websites": websites,
        }

    def list_annotations(self, context: TenantContext, website_id: str) -> list[dict]:
        with self.tenant_connection(context) as connection:
            rows = connection.execute("""
                SELECT a.id,a.annotation_date,a.annotation_type,a.note,a.created_at
                  FROM app.annotations a
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=a.website_id
                 WHERE p.public_id=%s ORDER BY a.annotation_date DESC,a.created_at DESC
            """, (website_id,)).fetchall()
        return [{"id":str(row["id"]),"date":row["annotation_date"].isoformat(),"type":row["annotation_type"],"note":row["note"],"createdAt":row["created_at"].isoformat()} for row in rows]

    def create_annotation(self, context: TenantContext, website_id: str, annotation_date: date, annotation_type: str, note: str) -> dict:
        context.require_role(frozenset({"agency_owner", "agency_admin", "client_admin"}))
        with self.tenant_connection(context) as connection:
            row = connection.execute("""
                INSERT INTO app.annotations(website_id,annotation_date,annotation_type,note)
                SELECT p.resource_id,%s,%s,%s FROM app.resource_identifiers p
                 WHERE p.resource_type='website' AND p.public_id=%s
                RETURNING id,annotation_date,annotation_type,note,created_at
            """, (annotation_date,annotation_type,note,website_id)).fetchone()
            if not row: raise PermissionError("website_not_authorized")
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s,%s,'annotation.created','annotation',%s,%s)", (context.organization_id,context.user_id,row["id"],json.dumps({"websiteId":website_id,"type":annotation_type})))
        return {"id":str(row["id"]),"date":row["annotation_date"].isoformat(),"type":row["annotation_type"],"note":row["note"],"createdAt":row["created_at"].isoformat()}

    def record_measurement_health(self, assignment_id, health: dict):
        with self.connection() as connection:
            connection.execute("INSERT INTO analytics.measurement_health_checks(assignment_id,status,details_json) VALUES(%s,%s,%s)",(assignment_id,health["status"],json.dumps(health)))

    def latest_measurement_health(self, context: TenantContext, website_id: str) -> dict | None:
        with self.tenant_connection(context) as connection:
            row=connection.execute("""
                SELECT h.status,h.details_json,h.checked_at
                  FROM analytics.measurement_health_checks h
                  JOIN app.website_analytics_assignments a ON a.id=h.assignment_id
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=a.website_id
                 WHERE p.public_id=%s ORDER BY h.checked_at DESC LIMIT 1
            """,(website_id,)).fetchone()
        return {"status":row["status"],"details":row["details_json"],"checkedAt":row["checked_at"].isoformat()} if row else None

    def set_alert(self, assignment_id, alert_key: str, severity: str, detail: dict, active: bool = True):
        with self.connection() as connection:
            if active:
                connection.execute("DELETE FROM analytics.operator_alerts WHERE assignment_id=%s AND alert_key=%s AND state='resolved'",(assignment_id,alert_key))
                connection.execute("""
                    INSERT INTO analytics.operator_alerts(assignment_id,alert_key,severity,state,detail_json)
                    VALUES(%s,%s,%s,'open',%s)
                    ON CONFLICT(assignment_id,alert_key,state) DO UPDATE SET
                      severity=excluded.severity,detail_json=excluded.detail_json,
                      opened_at=CASE WHEN analytics.operator_alerts.state='resolved' THEN now() ELSE analytics.operator_alerts.opened_at END,
                      resolved_at=NULL
                """,(assignment_id,alert_key,severity,json.dumps(detail)))
            else:
                connection.execute("DELETE FROM analytics.operator_alerts WHERE assignment_id=%s AND alert_key=%s AND state='resolved'",(assignment_id,alert_key))
                connection.execute("""
                    UPDATE analytics.operator_alerts SET state='resolved',resolved_at=now(),detail_json=%s
                     WHERE assignment_id=%s AND alert_key=%s AND state='open'
                """,(json.dumps(detail),assignment_id,alert_key))

    def replay_payload(self, job_id: str) -> dict:
        with self.connection() as connection:
            row=connection.execute("""
                UPDATE analytics.sync_jobs SET replay_count=replay_count+1,status='queued',
                       dead_lettered_at=NULL,next_attempt_at=now(),error_code=NULL,error_detail=NULL
                 WHERE id=%s AND status='failed'
             RETURNING id,period_key,scheduled_for
            """,(job_id,)).fetchone()
        if not row:
            raise ValueError("failed_sync_job_not_found")
        return {"jobId":str(row["id"]),"period":row["period_key"],"scheduledFor":row["scheduled_for"].isoformat()}
