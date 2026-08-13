from __future__ import annotations

import hashlib
import hmac
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
from .report_delivery import advance_schedule
from .external_sources import SOURCES, ExternalSourceState

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
            migrations_exist = connection.execute("SELECT to_regclass('app.schema_migrations') IS NOT NULL AS exists").fetchone()["exists"]
            applied = set()
            if migrations_exist:
                applied = {row["version"] for row in connection.execute("SELECT version FROM app.schema_migrations").fetchall()}
            if "002_production" not in applied:
                connection.execute((ROOT / "infra/postgres/002_production.sql").read_text())
            if "003_phase4_tenant_isolation" not in applied:
                connection.execute((ROOT / "infra/postgres/003_phase4_tenant_isolation.sql").read_text())
            if "004_phase5_reporting_oauth" not in applied:
                connection.execute((ROOT / "infra/postgres/004_phase5_reporting_oauth.sql").read_text())
            if "005_retention_offboarding" not in applied:
                connection.execute((ROOT / "infra/postgres/005_retention_offboarding.sql").read_text())
            if "006_external_sources" not in applied:
                connection.execute((ROOT / "infra/postgres/006_external_sources.sql").read_text())
            if "007_external_sync_provenance" not in applied:
                connection.execute((ROOT / "infra/postgres/007_external_sync_provenance.sql").read_text())
            if "008_source_connection_management" not in applied:
                connection.execute((ROOT / "infra/postgres/008_source_connection_management.sql").read_text())
        return {"status": "ok", "migration": "008_source_connection_management"}

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

    def active_sync_targets(self) -> list[dict]:
        with self.connection() as connection:
            rows=connection.execute("""
                SELECT a.id assignment_id,p.public_id website_id,c.credential_type
                  FROM app.website_analytics_assignments a
                  JOIN app.websites w ON w.id=a.website_id
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=w.id
                  JOIN app.analytics_connections c ON c.id=a.analytics_connection_id
                 WHERE a.status='approved' AND a.effective_to IS NULL AND c.status='approved' AND c.disabled_at IS NULL
                 ORDER BY a.id
            """).fetchall()
        return [{"assignmentId":str(row["assignment_id"]),"websiteId":row["website_id"],"credentialType":row["credential_type"]} for row in rows]

    def sync_target(self, assignment_id: str) -> dict:
        with self.connection() as connection:
            row=connection.execute("""
                SELECT a.id assignment_id,a.reporting_scope,a.analytics_connection_id,c.credential_type,c.status connection_status,
                       w.id website_uuid,p.public_id website_id,w.canonical_domain,w.healthcare_eligibility,
                       company_public.public_id company_id,co.name company,gp.external_property_id property_id,
                       gs.external_stream_id stream_id,gp.timezone property_timezone
                  FROM app.website_analytics_assignments a
                  JOIN app.analytics_connections c ON c.id=a.analytics_connection_id
                  JOIN app.websites w ON w.id=a.website_id JOIN app.companies co ON co.id=w.company_id
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=w.id
                  JOIN app.resource_identifiers company_public ON company_public.resource_type='company' AND company_public.resource_id=co.id
                  JOIN app.ga_properties gp ON gp.id=a.ga_property_id
                  LEFT JOIN app.ga_data_streams gs ON gs.id=a.ga_stream_id
                 WHERE a.id=%s::uuid AND a.status='approved' AND a.effective_to IS NULL AND c.status='approved' AND c.disabled_at IS NULL
            """,(assignment_id,)).fetchone()
        if not row: raise PermissionError("sync_assignment_not_approved")
        measurement_id=(row["reporting_scope"] or {}).get("measurementId","")
        return {**row,"assignment_id":str(row["assignment_id"]),"analytics_connection_id":str(row["analytics_connection_id"]),"website_uuid":str(row["website_uuid"]),"measurement_id":measurement_id}

    def internal_oauth_credential(self, connection_id: str) -> dict:
        with self.connection() as connection:
            row=connection.execute("""
                SELECT o.organization_id,o.encrypted_refresh_token,o.granted_scopes
                  FROM app.oauth_credentials o JOIN app.analytics_connections c ON c.id=o.analytics_connection_id
                 WHERE o.analytics_connection_id=%s::uuid AND o.revoked_at IS NULL AND c.status='approved' AND c.disabled_at IS NULL
            """,(connection_id,)).fetchone()
        if not row: raise PermissionError("oauth_connection_not_approved")
        return {"organization_id":str(row["organization_id"]),"encrypted_refresh_token":row["encrypted_refresh_token"],"granted_scopes":row["granted_scopes"]}

    def authorize_context(self, email: str, requested_organization_id: str | None = None) -> TenantContext:
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT o.id organization_id,u.id user_id,u.email,m.role
                  FROM app.users u
                  JOIN app.memberships m ON m.user_id=u.id
                  JOIN app.organizations o ON o.id=m.organization_id
                 WHERE lower(u.email)=lower(%s)
                   AND (%s::text IS NULL OR o.id::text=%s::text)
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

    def list_goals(self, context: TenantContext, website_id: str) -> list[dict]:
        with self.tenant_connection(context) as connection:
            rows = connection.execute("""
                SELECT g.id,m.slug metric,g.target_numeric,g.effective_from,g.effective_to,g.created_at,
                       v.version,v.aggregation
                  FROM app.client_goals g
                  JOIN app.metric_definition_versions v ON v.id=g.metric_definition_version_id
                  JOIN app.metric_definitions m ON m.id=v.metric_definition_id
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=g.website_id
                 WHERE p.public_id=%s AND v.approval_status='approved'
                 ORDER BY g.effective_from DESC,m.slug
            """, (website_id,)).fetchall()
        return [{"id":str(row["id"]),"metric":row["metric"],"metricVersion":row["version"],
                 "aggregation":row["aggregation"],"target":float(row["target_numeric"]),
                 "effectiveFrom":row["effective_from"].isoformat(),
                 "effectiveTo":row["effective_to"].isoformat() if row["effective_to"] else None,
                 "createdAt":row["created_at"].isoformat()} for row in rows]

    def list_goal_metrics(self, context: TenantContext, website_id: str) -> list[dict]:
        if not self.website_authorized(context, website_id):
            raise PermissionError("website_not_authorized")
        with self.tenant_connection(context) as connection:
            rows = connection.execute("""
                SELECT DISTINCT ON (m.slug) m.slug,v.version,v.aggregation
                  FROM app.metric_definitions m
                  JOIN app.metric_definition_versions v ON v.metric_definition_id=m.id
                 WHERE v.approval_status='approved'
                 ORDER BY m.slug,v.version DESC
            """).fetchall()
        return [{"metric":row["slug"],"version":row["version"],"aggregation":row["aggregation"]} for row in rows]

    def create_goal(self, context: TenantContext, website_id: str, metric: str, target: float,
                    effective_from: date, effective_to: date | None) -> dict:
        context.require_role(frozenset({"agency_owner", "agency_admin", "client_admin"}))
        with self.tenant_connection(context) as connection:
            row = connection.execute("""
                INSERT INTO app.client_goals(website_id,metric_definition_version_id,target_numeric,effective_from,effective_to)
                SELECT p.resource_id,v.id,%s,%s,%s
                  FROM app.resource_identifiers p
                  JOIN LATERAL (
                    SELECT v.id FROM app.metric_definition_versions v
                    JOIN app.metric_definitions m ON m.id=v.metric_definition_id
                    WHERE m.slug=%s AND v.approval_status='approved'
                    ORDER BY v.version DESC LIMIT 1
                  ) v ON true
                 WHERE p.resource_type='website' AND p.public_id=%s
                RETURNING id,metric_definition_version_id,target_numeric,effective_from,effective_to,created_at
            """, (target,effective_from,effective_to,metric,website_id)).fetchone()
            if not row: raise PermissionError("website_or_approved_metric_not_authorized")
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s,%s,'goal.created','client_goal',%s,%s)", (context.organization_id,context.user_id,row["id"],json.dumps({"websiteId":website_id,"metric":metric})))
        return {"id":str(row["id"]),"metric":metric,"target":float(row["target_numeric"]),
                "effectiveFrom":row["effective_from"].isoformat(),
                "effectiveTo":row["effective_to"].isoformat() if row["effective_to"] else None,
                "createdAt":row["created_at"].isoformat()}

    def list_recurring_reports(self, context: TenantContext, website_id: str) -> list[dict]:
        with self.tenant_connection(context) as connection:
            rows = connection.execute("""
                SELECT r.id,r.name,r.period_key,r.cadence,r.timezone,r.recipient_secret_reference,
                       r.enabled,r.next_run_at,r.created_at,r.disabled_at
                  FROM app.recurring_reports r
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=r.website_id
                 WHERE p.public_id=%s ORDER BY r.created_at DESC
            """, (website_id,)).fetchall()
        return [{"id":str(row["id"]),"name":row["name"],"period":row["period_key"],"cadence":row["cadence"],
                 "timezone":row["timezone"],"recipientReference":row["recipient_secret_reference"],
                 "enabled":row["enabled"],"nextRunAt":row["next_run_at"].isoformat(),
                 "createdAt":row["created_at"].isoformat(),"disabledAt":row["disabled_at"].isoformat() if row["disabled_at"] else None} for row in rows]

    def create_recurring_report(self, context: TenantContext, website_id: str, name: str, period: str,
                                cadence: str, timezone_name: str, recipient_reference: str,
                                next_run_at: datetime) -> dict:
        context.require_role(frozenset({"agency_owner", "agency_admin", "client_admin"}))
        with self.tenant_connection(context) as connection:
            row = connection.execute("""
                INSERT INTO app.recurring_reports(website_id,name,period_key,cadence,timezone,recipient_secret_reference,enabled,next_run_at,created_by)
                SELECT p.resource_id,%s,%s,%s,%s,%s,true,%s,%s::uuid FROM app.resource_identifiers p
                 WHERE p.resource_type='website' AND p.public_id=%s
                RETURNING id,name,period_key,cadence,timezone,recipient_secret_reference,enabled,next_run_at,created_at
            """, (name,period,cadence,timezone_name,recipient_reference,next_run_at,context.user_id,website_id)).fetchone()
            if not row: raise PermissionError("website_not_authorized")
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s,%s,'recurring_report.created','recurring_report',%s,%s)", (context.organization_id,context.user_id,row["id"],json.dumps({"websiteId":website_id,"cadence":cadence,"period":period})))
        return {"id":str(row["id"]),"name":row["name"],"period":row["period_key"],"cadence":row["cadence"],
                "timezone":row["timezone"],"recipientReference":row["recipient_secret_reference"],
                "enabled":row["enabled"],"nextRunAt":row["next_run_at"].isoformat(),"createdAt":row["created_at"].isoformat()}

    def disable_recurring_report(self, context: TenantContext, website_id: str, report_id: str) -> bool:
        context.require_role(frozenset({"agency_owner", "agency_admin", "client_admin"}))
        with self.tenant_connection(context) as connection:
            row = connection.execute("""
                UPDATE app.recurring_reports r SET enabled=false,disabled_at=now()
                 FROM app.resource_identifiers p
                 WHERE r.id=%s::uuid AND p.resource_type='website' AND p.public_id=%s AND p.resource_id=r.website_id
                RETURNING r.id
            """, (report_id,website_id)).fetchone()
            if row:
                connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s,%s,'recurring_report.disabled','recurring_report',%s,%s)", (context.organization_id,context.user_id,row["id"],json.dumps({"websiteId":website_id})))
        return bool(row)

    def due_recurring_reports(self, limit: int = 20) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT r.id,r.name,r.period_key,r.cadence,r.timezone,r.recipient_secret_reference,r.next_run_at,
                       p.public_id website_id,c.name company,c.organization_id,r.created_by
                  FROM app.recurring_reports r
                  JOIN app.websites w ON w.id=r.website_id
                  JOIN app.companies c ON c.id=w.company_id
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=w.id
                 WHERE r.enabled AND r.next_run_at<=now()
                 ORDER BY r.next_run_at LIMIT %s FOR UPDATE OF r SKIP LOCKED
            """, (limit,)).fetchall()
        return [{**row,"id":str(row["id"]),"organization_id":str(row["organization_id"]),"created_by":str(row["created_by"]),"next_run_at":row["next_run_at"]} for row in rows]

    def begin_report_delivery(self, report: dict) -> str | None:
        with self.connection() as connection:
            row = connection.execute("""
                INSERT INTO analytics.report_deliveries(recurring_report_id,website_id,scheduled_for,status)
                SELECT r.id,r.website_id,%s,'generating' FROM app.recurring_reports r WHERE r.id=%s
                ON CONFLICT(recurring_report_id,scheduled_for) DO UPDATE
                  SET status='generating',error_code=NULL,attempt_count=analytics.report_deliveries.attempt_count+1,completed_at=NULL
                  WHERE analytics.report_deliveries.status IN ('failed','blocked_configuration')
                    AND analytics.report_deliveries.completed_at<=now()-interval '15 minutes'
                RETURNING id
            """, (report["next_run_at"],report["id"])).fetchone()
        return str(row["id"]) if row else None

    def finish_report_delivery(self, report: dict, delivery_id: str, report_hash: str,
                               provider_message_id: str | None = None, error_code: str | None = None) -> None:
        status = "sent" if provider_message_id else ("blocked_configuration" if error_code and "configured" in error_code else "failed")
        next_run = advance_schedule(report["next_run_at"], report["cadence"])
        with self.connection() as connection:
            connection.execute("""
                UPDATE analytics.report_deliveries SET status=%s,provider_message_id=%s,report_hash=%s,error_code=%s,completed_at=now()
                 WHERE id=%s::uuid
            """, (status,provider_message_id,report_hash,error_code,delivery_id))
            if status == "sent":
                connection.execute("UPDATE app.recurring_reports SET next_run_at=%s WHERE id=%s", (next_run,report["id"]))

    def create_oauth_state(self, context: TenantContext, state_hash: str, verifier_ciphertext: bytes,
                           scopes: list[str], redirect_uri: str, expires_at: datetime) -> None:
        context.require_role(frozenset({"agency_owner", "agency_admin", "client_admin"}))
        with self.tenant_connection(context) as connection:
            connection.execute("""
                INSERT INTO app.oauth_authorization_states(organization_id,actor_user_id,state_hash,pkce_verifier_ciphertext,requested_scopes,redirect_uri,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
            """, (context.organization_id,context.user_id,state_hash,verifier_ciphertext,scopes,redirect_uri,expires_at))

    def context_for_oauth_callback(self, organization_id: str, user_id: str) -> TenantContext:
        with self.connection() as connection:
            row=connection.execute("""
                SELECT o.id organization_id,u.id user_id,u.email,m.role
                  FROM app.organizations o JOIN app.memberships m ON m.organization_id=o.id
                  JOIN app.users u ON u.id=m.user_id
                 WHERE o.id=%s::uuid AND u.id=%s::uuid
            """,(organization_id,user_id)).fetchone()
        if not row: raise PermissionError("oauth_membership_not_found")
        return TenantContext(str(row["organization_id"]),str(row["user_id"]),row["email"],row["role"])

    def consume_oauth_state(self, context: TenantContext, state_hash: str) -> dict:
        with self.tenant_connection(context) as connection:
            row=connection.execute("""
                UPDATE app.oauth_authorization_states SET consumed_at=now()
                 WHERE state_hash=%s AND actor_user_id=%s::uuid AND consumed_at IS NULL AND expires_at>now()
                RETURNING pkce_verifier_ciphertext,requested_scopes,redirect_uri
            """,(state_hash,context.user_id)).fetchone()
        if not row: raise PermissionError("invalid_or_consumed_oauth_state")
        return row

    def save_oauth_connection(self, context: TenantContext, encrypted_refresh_token: bytes,
                              scopes: list[str], provider_subject: str | None = None) -> dict:
        context.require_role(frozenset({"agency_owner", "agency_admin", "client_admin"}))
        connection_id=uuid.uuid4()
        with self.tenant_connection(context) as connection:
            connection.execute("""
                INSERT INTO app.analytics_connections(id,organization_id,credential_type,credential_reference,status)
                VALUES(%s,%s,'oauth','postgres-kms-envelope:v1','pending_approval')
            """,(connection_id,context.organization_id))
            connection.execute("""
                INSERT INTO app.oauth_credentials(analytics_connection_id,organization_id,encrypted_refresh_token,granted_scopes,token_endpoint,provider_subject,connected_by)
                VALUES(%s,%s,%s,%s,'https://oauth2.googleapis.com/token',%s,%s)
            """,(connection_id,context.organization_id,encrypted_refresh_token,scopes,provider_subject,context.user_id))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s,%s,'oauth.connected','analytics_connection',%s,%s)",(context.organization_id,context.user_id,connection_id,json.dumps({"scopes":scopes,"status":"pending_approval"})))
        return {"connectionId":str(connection_id),"status":"pending_approval","credentialType":"oauth","scopes":scopes}

    def list_oauth_connections(self, context: TenantContext) -> list[dict]:
        with self.tenant_connection(context) as connection:
            rows=connection.execute("""
                SELECT c.id,c.status,c.created_at,c.disabled_at,o.granted_scopes,o.connected_at,o.revoked_at,o.last_validated_at
                  FROM app.analytics_connections c JOIN app.oauth_credentials o ON o.analytics_connection_id=c.id
                 ORDER BY c.created_at DESC
            """).fetchall()
        return [{"connectionId":str(row["id"]),"status":row["status"],"credentialType":"oauth",
                 "scopes":row["granted_scopes"],"connectedAt":row["connected_at"].isoformat(),
                 "revokedAt":row["revoked_at"].isoformat() if row["revoked_at"] else None,
                 "lastValidatedAt":row["last_validated_at"].isoformat() if row["last_validated_at"] else None} for row in rows]

    def oauth_refresh_ciphertext(self, context: TenantContext, connection_id: str) -> bytes:
        with self.tenant_connection(context) as connection:
            row=connection.execute("SELECT encrypted_refresh_token FROM app.oauth_credentials WHERE analytics_connection_id=%s::uuid AND revoked_at IS NULL",(connection_id,)).fetchone()
        if not row: raise PermissionError("oauth_connection_not_authorized")
        return row["encrypted_refresh_token"]

    def revoke_oauth_connection(self, context: TenantContext, connection_id: str, delete_token: bool = False) -> bool:
        context.require_role(frozenset({"agency_owner", "agency_admin", "client_admin"}))
        with self.tenant_connection(context) as connection:
            row=connection.execute("""
                UPDATE app.analytics_connections SET status='revoked',disabled_at=now()
                 WHERE id=%s::uuid AND credential_type='oauth' RETURNING id
            """,(connection_id,)).fetchone()
            if not row: return False
            if delete_token:
                connection.execute("DELETE FROM app.oauth_credentials WHERE analytics_connection_id=%s::uuid",(connection_id,))
            else:
                connection.execute("UPDATE app.oauth_credentials SET revoked_at=now() WHERE analytics_connection_id=%s::uuid",(connection_id,))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s,%s,%s,'analytics_connection',%s,%s)",(context.organization_id,context.user_id,"oauth.offboarded" if delete_token else "oauth.revoked",connection_id,json.dumps({"tokenDeleted":delete_token})))
        return True

    def retention_policy(self, context: TenantContext) -> dict:
        with self.tenant_connection(context) as connection:
            row=connection.execute("SELECT aggregate_days,operations_days,audit_days,deletion_grace_days,updated_at FROM app.data_retention_policies WHERE organization_id=%s::uuid",(context.organization_id,)).fetchone()
        if not row: raise PermissionError("retention_policy_not_found")
        return {"aggregateDays":row["aggregate_days"],"operationsDays":row["operations_days"],"auditDays":row["audit_days"],"deletionGraceDays":row["deletion_grace_days"],"updatedAt":row["updated_at"].isoformat()}

    def deletion_preview(self, context: TenantContext, website_id: str) -> dict:
        context.require_role(frozenset({"agency_owner","agency_admin","client_admin"}))
        with self.tenant_connection(context) as connection:
            row=connection.execute("""
                SELECT p.resource_id website_id,c.id company_id,
                  (SELECT count(*) FROM app.website_analytics_assignments a WHERE a.website_id=p.resource_id) assignments,
                  (SELECT count(*) FROM analytics.report_executions e JOIN app.website_analytics_assignments a ON a.id=e.assignment_id WHERE a.website_id=p.resource_id) executions,
                  (SELECT count(*) FROM app.annotations x WHERE x.website_id=p.resource_id) annotations,
                  (SELECT count(*) FROM app.client_goals x WHERE x.website_id=p.resource_id) goals,
                  (SELECT count(*) FROM app.recurring_reports x WHERE x.website_id=p.resource_id) recurring_reports
                FROM app.resource_identifiers p JOIN app.websites w ON w.id=p.resource_id JOIN app.companies c ON c.id=w.company_id
                WHERE p.resource_type='website' AND p.public_id=%s
            """,(website_id,)).fetchone()
        if not row: raise PermissionError("website_not_authorized")
        return {"websiteId":website_id,"assignments":row["assignments"],"reportExecutions":row["executions"],"annotations":row["annotations"],"goals":row["goals"],"recurringReports":row["recurring_reports"],"deletesWebsite":True,"deletesExclusiveConnectionCredentials":True,"clientMustRemoveServiceAccountFromGa4":True}

    def request_offboarding(self, context: TenantContext, website_id: str, confirmation: str) -> dict:
        context.require_role(frozenset({"agency_owner","agency_admin"}))
        if not hmac.compare_digest(confirmation,website_id): raise PermissionError("offboarding_confirmation_mismatch")
        preview=self.deletion_preview(context,website_id); policy=self.retention_policy(context)
        with self.tenant_connection(context) as connection:
            row=connection.execute("""
                INSERT INTO app.deletion_requests(organization_id,website_id,requested_by,status,execute_after,confirmation_public_id,preview_json)
                SELECT %s::uuid,p.resource_id,%s::uuid,'scheduled',now()+(%s||' days')::interval,%s,%s
                  FROM app.resource_identifiers p WHERE p.resource_type='website' AND p.public_id=%s
                RETURNING id,execute_after
            """,(context.organization_id,context.user_id,policy["deletionGraceDays"],website_id,json.dumps(preview),website_id)).fetchone()
            if not row: raise PermissionError("website_not_authorized")
            connection.execute("UPDATE app.recurring_reports SET enabled=false,disabled_at=now() WHERE website_id IN (SELECT resource_id FROM app.resource_identifiers WHERE resource_type='website' AND public_id=%s)",(website_id,))
            connection.execute("UPDATE app.website_analytics_assignments SET status='disabled',effective_to=current_date WHERE website_id IN (SELECT resource_id FROM app.resource_identifiers WHERE resource_type='website' AND public_id=%s)",(website_id,))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s,%s,'website.offboarding_scheduled','website',NULL,%s)",(context.organization_id,context.user_id,json.dumps({"websiteId":website_id,"deletionRequestId":str(row["id"]),"executeAfter":row["execute_after"].isoformat()})))
        return {"deletionRequestId":str(row["id"]),"websiteId":website_id,"status":"scheduled","executeAfter":row["execute_after"].isoformat(),"preview":preview,"revocableDuringGracePeriod":True}

    def cancel_offboarding(self, context: TenantContext, request_id: str) -> bool:
        context.require_role(frozenset({"agency_owner","agency_admin"}))
        with self.tenant_connection(context) as connection:
            row=connection.execute("UPDATE app.deletion_requests SET status='cancelled',cancelled_at=now() WHERE id=%s::uuid AND status='scheduled' RETURNING id",(request_id,)).fetchone()
            if row: connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s,%s,'website.offboarding_cancelled','deletion_request',%s,'{}')",(context.organization_id,context.user_id,row["id"]))
        return bool(row)

    def purge_retention(self) -> dict:
        with self.connection() as connection:
            aggregate_counts={}
            for table in ("daily_property_metrics","daily_channel_metrics","daily_page_metrics","daily_event_metrics","period_metric_snapshots","daily_canonical_metrics"):
                aggregate_counts[table]=connection.execute(sql.SQL("""
                    DELETE FROM analytics.{} f USING analytics.report_executions e,app.website_analytics_assignments a,app.websites w,app.companies c,app.data_retention_policies p
                     WHERE f.report_execution_id=e.id AND e.assignment_id=a.id AND a.website_id=w.id AND w.company_id=c.id AND p.organization_id=c.organization_id
                       AND e.requested_end_date<current_date-p.aggregate_days RETURNING 1
                """).format(sql.Identifier(table))).rowcount
            aggregate_counts["reportSnapshots"]=connection.execute("""
                DELETE FROM analytics.report_snapshots s USING analytics.report_executions e,app.website_analytics_assignments a,app.websites w,app.companies c,app.data_retention_policies p
                 WHERE s.execution_id=e.id AND e.assignment_id=a.id AND a.website_id=w.id AND w.company_id=c.id AND p.organization_id=c.organization_id
                   AND e.requested_end_date<current_date-p.aggregate_days RETURNING s.id
            """).rowcount
            aggregate_counts["reportExecutions"]=connection.execute("""
                DELETE FROM analytics.report_executions e USING app.website_analytics_assignments a,app.websites w,app.companies c,app.data_retention_policies p
                 WHERE e.assignment_id=a.id AND a.website_id=w.id AND w.company_id=c.id AND p.organization_id=c.organization_id
                   AND e.requested_end_date<current_date-p.aggregate_days RETURNING e.id
            """).rowcount
            aggregate_counts["googleAds"]=connection.execute("DELETE FROM analytics.google_ads_daily d USING app.websites w,app.companies c,app.data_retention_policies p WHERE d.website_id=w.id AND w.company_id=c.id AND p.organization_id=c.organization_id AND d.metric_date<current_date-p.aggregate_days RETURNING 1").rowcount
            aggregate_counts["searchConsole"]=connection.execute("DELETE FROM analytics.search_console_daily d USING app.websites w,app.companies c,app.data_retention_policies p WHERE d.website_id=w.id AND w.company_id=c.id AND p.organization_id=c.organization_id AND d.metric_date<current_date-p.aggregate_days RETURNING 1").rowcount
            aggregate_counts["firstPartyOutcomes"]=connection.execute("DELETE FROM analytics.first_party_outcomes d USING app.websites w,app.companies c,app.data_retention_policies p WHERE d.website_id=w.id AND w.company_id=c.id AND p.organization_id=c.organization_id AND d.outcome_date<current_date-p.aggregate_days RETURNING 1").rowcount
            aggregate_counts["sourceSyncExecutions"]=connection.execute("DELETE FROM analytics.source_sync_executions e USING app.websites w,app.companies c,app.data_retention_policies p WHERE e.website_id=w.id AND w.company_id=c.id AND p.organization_id=c.organization_id AND e.requested_end_date<current_date-p.operations_days RETURNING 1").rowcount
            operations=connection.execute("""
                DELETE FROM analytics.report_deliveries d USING app.recurring_reports r,app.websites w,app.companies c,app.data_retention_policies p
                 WHERE d.recurring_report_id=r.id AND r.website_id=w.id AND w.company_id=c.id AND p.organization_id=c.organization_id
                   AND d.created_at<now()-(p.operations_days||' days')::interval RETURNING d.id
            """).rowcount
            sync_jobs=connection.execute("""
                DELETE FROM analytics.sync_jobs j USING app.website_analytics_assignments a,app.websites w,app.companies c,app.data_retention_policies p
                 WHERE j.assignment_id=a.id AND a.website_id=w.id AND w.company_id=c.id AND p.organization_id=c.organization_id
                   AND coalesce(j.completed_at,j.scheduled_for,j.requested_end_date::timestamptz)<now()-(p.operations_days||' days')::interval
                   AND NOT EXISTS(SELECT 1 FROM analytics.report_executions e WHERE e.sync_job_id=j.id) RETURNING j.id
            """).rowcount
            states=connection.execute("DELETE FROM app.oauth_authorization_states WHERE expires_at<now()-interval '1 day' RETURNING id").rowcount
            audits=connection.execute("""
                DELETE FROM audit.events e USING app.data_retention_policies p
                 WHERE e.organization_id=p.organization_id AND e.created_at<now()-(p.audit_days||' days')::interval RETURNING e.id
            """).rowcount
        return {"expiredAggregates":aggregate_counts,"expiredSyncJobs":sync_jobs,"expiredReportDeliveries":operations,"expiredOauthStates":states,"expiredAuditEvents":audits}

    def execute_due_deletions(self, limit: int = 5) -> list[dict]:
        results=[]
        with self.connection() as connection:
            requests=connection.execute("SELECT id,organization_id,website_id,confirmation_public_id FROM app.deletion_requests WHERE status='scheduled' AND execute_after<=now() ORDER BY execute_after LIMIT %s FOR UPDATE SKIP LOCKED",(limit,)).fetchall()
            for request in requests:
                website_id=request["website_id"]
                connection.execute("UPDATE app.deletion_requests SET status='executing' WHERE id=%s",(request["id"],))
                company=connection.execute("SELECT company_id FROM app.websites WHERE id=%s",(website_id,)).fetchone()
                assignments=[row["id"] for row in connection.execute("SELECT id FROM app.website_analytics_assignments WHERE website_id=%s",(website_id,)).fetchall()]
                connections=[row["analytics_connection_id"] for row in connection.execute("""
                    SELECT DISTINCT a.analytics_connection_id FROM app.website_analytics_assignments a
                     WHERE a.website_id=%s AND NOT EXISTS (
                       SELECT 1 FROM app.website_analytics_assignments other WHERE other.analytics_connection_id=a.analytics_connection_id AND other.website_id<>%s
                     )
                """,(website_id,website_id)).fetchall()]
                counts={}
                counts["reportDeliveries"]=connection.execute("DELETE FROM analytics.report_deliveries WHERE website_id=%s RETURNING id",(website_id,)).rowcount
                counts["recurringReports"]=connection.execute("DELETE FROM app.recurring_reports WHERE website_id=%s RETURNING id",(website_id,)).rowcount
                counts["googleAds"]=connection.execute("DELETE FROM analytics.google_ads_daily WHERE website_id=%s RETURNING 1",(website_id,)).rowcount
                counts["searchConsole"]=connection.execute("DELETE FROM analytics.search_console_daily WHERE website_id=%s RETURNING 1",(website_id,)).rowcount
                counts["firstPartyOutcomes"]=connection.execute("DELETE FROM analytics.first_party_outcomes WHERE website_id=%s RETURNING 1",(website_id,)).rowcount
                counts["sourceSyncExecutions"]=connection.execute("DELETE FROM analytics.source_sync_executions WHERE website_id=%s RETURNING 1",(website_id,)).rowcount
                counts["sourceConnections"]=connection.execute("DELETE FROM app.source_connections WHERE website_id=%s RETURNING id",(website_id,)).rowcount
                for table in ("daily_property_metrics","daily_channel_metrics","daily_page_metrics","daily_event_metrics","period_metric_snapshots","daily_canonical_metrics","report_snapshots","operator_alerts","measurement_health_checks","data_quality_status"):
                    counts[table]=connection.execute(sql.SQL("DELETE FROM analytics.{} WHERE assignment_id=ANY(%s) RETURNING 1").format(sql.Identifier(table)),(assignments,)).rowcount if assignments else 0
                counts["reportExecutions"]=connection.execute("DELETE FROM analytics.report_executions WHERE assignment_id=ANY(%s) RETURNING id",(assignments,)).rowcount if assignments else 0
                counts["syncJobs"]=connection.execute("DELETE FROM analytics.sync_jobs WHERE assignment_id=ANY(%s) RETURNING id",(assignments,)).rowcount if assignments else 0
                counts["syncRuns"]=connection.execute("DELETE FROM analytics.sync_runs WHERE assignment_id=ANY(%s) RETURNING id",(assignments,)).rowcount if assignments else 0
                counts["annotations"]=connection.execute("DELETE FROM app.annotations WHERE website_id=%s RETURNING id",(website_id,)).rowcount
                counts["goals"]=connection.execute("DELETE FROM app.client_goals WHERE website_id=%s RETURNING id",(website_id,)).rowcount
                counts["eventMappings"]=connection.execute("DELETE FROM app.event_mappings WHERE website_id=%s RETURNING id",(website_id,)).rowcount
                counts["contractAssignments"]=connection.execute("DELETE FROM app.website_measurement_contract_assignments WHERE website_id=%s RETURNING website_id",(website_id,)).rowcount
                counts["analyticsAssignments"]=connection.execute("DELETE FROM app.website_analytics_assignments WHERE website_id=%s RETURNING id",(website_id,)).rowcount
                connection.execute("DELETE FROM app.resource_identifiers WHERE resource_type='website' AND resource_id=%s",(website_id,))
                counts["website"]=connection.execute("DELETE FROM app.websites WHERE id=%s RETURNING id",(website_id,)).rowcount
                for connection_id in connections:
                    property_ids=[row["id"] for row in connection.execute("SELECT id FROM app.ga_properties WHERE analytics_connection_id=%s",(connection_id,)).fetchall()]
                    if property_ids: connection.execute("DELETE FROM app.ga_data_streams WHERE ga_property_id=ANY(%s)",(property_ids,))
                    connection.execute("DELETE FROM app.ga_properties WHERE analytics_connection_id=%s",(connection_id,))
                    connection.execute("DELETE FROM app.oauth_credentials WHERE analytics_connection_id=%s",(connection_id,))
                    connection.execute("DELETE FROM app.analytics_connections WHERE id=%s",(connection_id,))
                if company:
                    remaining=connection.execute("SELECT count(*) count FROM app.websites WHERE company_id=%s",(company["company_id"],)).fetchone()["count"]
                    if not remaining:
                        connection.execute("DELETE FROM app.resource_identifiers WHERE resource_type='company' AND resource_id=%s",(company["company_id"],))
                        counts["company"]=connection.execute("DELETE FROM app.companies WHERE id=%s RETURNING id",(company["company_id"],)).rowcount
                connection.execute("UPDATE app.deletion_requests SET status='completed',result_json=%s,completed_at=now() WHERE id=%s",(json.dumps(counts),request["id"]))
                connection.execute("INSERT INTO audit.events(organization_id,action,target_type,target_id,detail_json) VALUES(%s,'website.deletion_completed','deletion_request',%s,%s)",(request["organization_id"],request["id"],json.dumps({"websiteId":request["confirmation_public_id"],"counts":counts})))
                results.append({"deletionRequestId":str(request["id"]),"websiteId":request["confirmation_public_id"],"status":"completed","counts":counts})
        return results

    def external_source_status(self, context: TenantContext, website_id: str) -> list[dict]:
        with self.tenant_connection(context) as connection:
            rows=connection.execute("""
                SELECT c.source_type,c.approval_status,c.last_validated_at,c.disabled_at,
                       GREATEST((SELECT max(source_sync_at) FROM analytics.google_ads_daily x WHERE x.source_connection_id=c.id),
                                (SELECT max(source_sync_at) FROM analytics.search_console_daily x WHERE x.source_connection_id=c.id),
                                (SELECT max(source_sync_at) FROM analytics.first_party_outcomes x WHERE x.source_connection_id=c.id)) last_sync_at
                  FROM app.source_connections c JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=c.website_id
                 WHERE p.public_id=%s
            """,(website_id,)).fetchall()
        by_source={row["source_type"]:row for row in rows}
        result=[]
        for source in SOURCES:
            row=by_source.get(source)
            if not row:
                state=ExternalSourceState(source,"not_configured",None,None,None,"approved_source_account_and_credential_required")
            elif row["disabled_at"]:
                state=ExternalSourceState(source,"disabled",row["approval_status"],row["last_validated_at"],row["last_sync_at"],"connection_disabled")
            elif row["approval_status"]!="approved":
                state=ExternalSourceState(source,"pending_approval",row["approval_status"],row["last_validated_at"],row["last_sync_at"],"source_governance_approval_required")
            else:
                state=ExternalSourceState(source,"active" if row["last_sync_at"] else "approved_awaiting_first_sync",row["approval_status"],row["last_validated_at"],row["last_sync_at"],None)
            result.append(state.as_dict())
        return result

    def register_source_connection(self, context: TenantContext, website_id: str, source_type: str,
                                   credential_reference: str, external_account_id: str | None, configuration: dict) -> dict:
        context.require_role(frozenset({"agency_owner","agency_admin"}))
        allowed_configuration={
            "google_ads":{"loginCustomerId"},
            "search_console":{"siteUrl","privacyApprovedQueries"},
            "call_tracking":{"provider","identityPolicyReference"},
            "crm_booking":{"provider","identityPolicyReference"},
        }
        if source_type not in allowed_configuration or set(configuration)-allowed_configuration[source_type]: raise PermissionError("unapproved_source_configuration")
        with self.tenant_connection(context) as connection:
            row=connection.execute("""
                INSERT INTO app.source_connections(organization_id,website_id,source_type,credential_secret_reference,external_account_id,configuration_json,approval_status,disabled_at)
                SELECT %s::uuid,p.resource_id,%s,%s,%s,%s,'pending_approval',NULL FROM app.resource_identifiers p
                 WHERE p.resource_type='website' AND p.public_id=%s
                ON CONFLICT(website_id,source_type) DO UPDATE SET credential_secret_reference=excluded.credential_secret_reference,
                  external_account_id=excluded.external_account_id,configuration_json=excluded.configuration_json,
                  approval_status='pending_approval',last_validated_at=NULL,disabled_at=NULL
                RETURNING id,source_type,external_account_id,configuration_json,approval_status,created_at
            """,(context.organization_id,source_type,credential_reference,external_account_id,json.dumps(configuration),website_id)).fetchone()
            if not row: raise PermissionError("website_not_authorized")
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'source_connection.registered','source_connection',%s,%s)",(context.organization_id,context.user_id,row["id"],json.dumps({"websiteId":website_id,"sourceType":source_type,"approvalStatus":"pending_approval"})))
        return {"connectionId":str(row["id"]),"sourceType":row["source_type"],"externalAccountId":row["external_account_id"],"configuration":row["configuration_json"],"approvalStatus":row["approval_status"],"createdAt":row["created_at"].isoformat()}

    def disable_source_connection(self, context: TenantContext, website_id: str, source_type: str) -> bool:
        context.require_role(frozenset({"agency_owner","agency_admin"}))
        with self.tenant_connection(context) as connection:
            row=connection.execute("""
                UPDATE app.source_connections c SET approval_status='disabled',disabled_at=now()
                  FROM app.resource_identifiers p WHERE p.resource_type='website' AND p.public_id=%s AND p.resource_id=c.website_id AND c.source_type=%s
                RETURNING c.id
            """,(website_id,source_type)).fetchone()
            if row: connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'source_connection.disabled','source_connection',%s,%s)",(context.organization_id,context.user_id,row["id"],json.dumps({"websiteId":website_id,"sourceType":source_type})))
        return bool(row)

    def business_outcomes(self, context: TenantContext, website_id: str, start_date: date, end_date: date) -> dict:
        with self.tenant_connection(context) as connection:
            outcomes=connection.execute("""
                SELECT outcome_type,count(*) count,coalesce(sum(revenue_minor_units),0) revenue_minor_units,
                       min(currency_code) FILTER(WHERE currency_code IS NOT NULL) currency_code
                  FROM analytics.first_party_outcomes o JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=o.website_id
                 WHERE p.public_id=%s AND outcome_date BETWEEN %s AND %s GROUP BY outcome_type
            """,(website_id,start_date,end_date)).fetchall()
            ads=connection.execute("""
                SELECT coalesce(sum(cost_micros),0) cost_micros,coalesce(sum(clicks),0) clicks,min(currency_code) currency_code
                  FROM analytics.google_ads_daily a JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=a.website_id
                 WHERE p.public_id=%s AND metric_date BETWEEN %s AND %s
            """,(website_id,start_date,end_date)).fetchone()
            revenue_channels=connection.execute("""
                SELECT coalesce(attribution_json->>'channel','Unattributed') channel,sum(revenue_minor_units) revenue_minor_units
                  FROM analytics.first_party_outcomes o JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=o.website_id
                 WHERE p.public_id=%s AND outcome_type='revenue' AND outcome_date BETWEEN %s AND %s GROUP BY 1 ORDER BY 2 DESC
            """,(website_id,start_date,end_date)).fetchall()
        counts={row["outcome_type"]:row["count"] for row in outcomes}
        qualified=counts.get("qualified_lead",0); appointments=counts.get("booked_appointment",0); customers=counts.get("customer",0)
        cost=float(ads["cost_micros"])/1_000_000
        return {"websiteId":website_id,"startDate":start_date.isoformat(),"endDate":end_date.isoformat(),
                "outcomes":counts,"cost":cost,"clicks":ads["clicks"],"currency":ads["currency_code"],
                "costPerQualifiedLead":cost/qualified if qualified else None,
                "leadToAppointmentRate":appointments/qualified if qualified else None,
                "appointmentToCustomerRate":customers/appointments if appointments else None,
                "revenueMinorUnits":sum(row["revenue_minor_units"] for row in outcomes if row["outcome_type"]=="revenue"),
                "revenueByChannel":[{"channel":row["channel"],"revenueMinorUnits":row["revenue_minor_units"]} for row in revenue_channels],
                "sourceFamilies":["google_ads","approved_first_party"],
                "caveats":["GA4 intent events are not treated as confirmed business outcomes.","Identity matching occurs only in approved first-party systems; prohibited identifiers are not sent to GA4.","Null KPIs mean the required approved source data is unavailable, not zero."]}

    def list_memberships(self, context: TenantContext) -> list[dict]:
        context.require_role(frozenset({"agency_owner","agency_admin","agency_analyst"}))
        with self.tenant_connection(context) as connection:
            rows=connection.execute("SELECT u.id,u.email,m.role FROM app.memberships m JOIN app.users u ON u.id=m.user_id ORDER BY u.email").fetchall()
        return [{"userId":str(row["id"]),"email":row["email"],"role":row["role"]} for row in rows]

    def upsert_membership(self, context: TenantContext, email: str, role: str) -> dict:
        context.require_role(frozenset({"agency_owner","agency_admin"}))
        if context.role=="agency_admin" and role=="agency_owner": raise PermissionError("cannot_grant_agency_owner")
        with self.connection() as connection:
            user=connection.execute("INSERT INTO app.users(email) VALUES(%s) ON CONFLICT(email) DO UPDATE SET email=excluded.email RETURNING id,email",(email,)).fetchone()
            existing=connection.execute("SELECT role FROM app.memberships WHERE organization_id=%s::uuid AND user_id=%s",(context.organization_id,user["id"])).fetchone()
            if existing and existing["role"]=="agency_owner" and context.role!="agency_owner": raise PermissionError("cannot_modify_agency_owner")
            connection.execute("INSERT INTO app.memberships(organization_id,user_id,role) VALUES(%s::uuid,%s,%s) ON CONFLICT(organization_id,user_id) DO UPDATE SET role=excluded.role",(context.organization_id,user["id"],role))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'membership.upserted','user',%s,%s)",(context.organization_id,context.user_id,user["id"],json.dumps({"email":email,"role":role})))
        return {"userId":str(user["id"]),"email":user["email"],"role":role}

    def remove_membership(self, context: TenantContext, user_id: str) -> bool:
        context.require_role(frozenset({"agency_owner","agency_admin"}))
        if user_id==context.user_id: raise PermissionError("cannot_remove_current_membership")
        with self.connection() as connection:
            existing=connection.execute("SELECT role FROM app.memberships WHERE organization_id=%s::uuid AND user_id=%s::uuid",(context.organization_id,user_id)).fetchone()
            if not existing: return False
            if existing["role"]=="agency_owner" and context.role!="agency_owner": raise PermissionError("cannot_remove_agency_owner")
            connection.execute("DELETE FROM app.memberships WHERE organization_id=%s::uuid AND user_id=%s::uuid",(context.organization_id,user_id))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'membership.removed','user',%s::uuid,'{}')",(context.organization_id,context.user_id,user_id))
        return True

    def begin_external_sync(self, connection_id: str, source_type: str, start_date: date, end_date: date, request_hash: str) -> dict:
        with self.connection() as connection:
            source=connection.execute("""
                SELECT c.id,c.website_id,c.source_type,p.public_id website_public_id
                  FROM app.source_connections c JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=c.website_id
                 WHERE c.id=%s::uuid AND c.source_type=%s AND c.approval_status='approved' AND c.disabled_at IS NULL
            """,(connection_id,source_type)).fetchone()
            if not source: raise PermissionError("external_source_not_approved")
            existing=connection.execute("SELECT id,status,row_count,response_hash,reconciliation_json FROM analytics.source_sync_executions WHERE source_connection_id=%s::uuid AND requested_start_date=%s AND requested_end_date=%s AND request_hash=%s",(connection_id,start_date,end_date,request_hash)).fetchone()
            if existing and existing["status"]=="succeeded":
                return {"executionId":str(existing["id"]),"websiteId":source["website_public_id"],"idempotentReplay":True,"rowCount":existing["row_count"],"responseHash":existing["response_hash"],"reconciliation":existing["reconciliation_json"]}
            row=connection.execute("""
                INSERT INTO analytics.source_sync_executions(source_connection_id,website_id,requested_start_date,requested_end_date,request_hash,status)
                VALUES(%s::uuid,%s,%s,%s,%s,'running')
                ON CONFLICT(source_connection_id,requested_start_date,requested_end_date,request_hash)
                DO UPDATE SET status='running',error_code=NULL,started_at=now(),completed_at=NULL
                RETURNING id
            """,(connection_id,source["website_id"],start_date,end_date,request_hash)).fetchone()
        return {"executionId":str(row["id"]),"websiteId":source["website_public_id"],"websiteUuid":str(source["website_id"]),"idempotentReplay":False}

    def complete_external_sync(self, execution: dict, connection_id: str, source_type: str, rows: list[dict], response_hash: str, reconciliation: dict) -> dict:
        synced_at=datetime.now(timezone.utc)
        with self.connection() as connection:
            execution_row=connection.execute("SELECT website_id,requested_start_date,requested_end_date FROM analytics.source_sync_executions WHERE id=%s::uuid AND status='running' FOR UPDATE",(execution["executionId"],)).fetchone()
            if not execution_row: raise RuntimeError("external_sync_not_running")
            start_date,end_date=execution_row["requested_start_date"],execution_row["requested_end_date"]
            website_uuid=execution_row["website_id"]
            if source_type=="google_ads":
                connection.execute("DELETE FROM analytics.google_ads_daily WHERE source_connection_id=%s::uuid AND metric_date BETWEEN %s AND %s",(connection_id,start_date,end_date))
                connection.cursor().executemany("""
                    INSERT INTO analytics.google_ads_daily(source_connection_id,website_id,metric_date,campaign_id,campaign_name,ad_group_id,cost_micros,clicks,impressions,currency_code,source_sync_at)
                    VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,[(connection_id,website_uuid,row["date"],row["campaignId"],row["campaignName"],row["adGroupId"],row["costMicros"],row["clicks"],row["impressions"],row["currency"],synced_at) for row in rows])
            elif source_type=="search_console":
                connection.execute("DELETE FROM analytics.search_console_daily WHERE source_connection_id=%s::uuid AND metric_date BETWEEN %s AND %s",(connection_id,start_date,end_date))
                connection.cursor().executemany("""
                    INSERT INTO analytics.search_console_daily(source_connection_id,website_id,metric_date,query_hash,query_text,page_path,clicks,impressions,ctr,position,privacy_approved,source_sync_at)
                    VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,[(connection_id,website_uuid,row["date"],row["queryHash"],row["queryText"],row["pagePath"],row["clicks"],row["impressions"],row["ctr"],row["position"],row["privacyApproved"],synced_at) for row in rows])
            elif source_type in {"call_tracking","crm_booking"}:
                connection.cursor().executemany("""
                    INSERT INTO analytics.first_party_outcomes(source_connection_id,website_id,source_record_hash,subject_key,outcome_type,outcome_date,revenue_minor_units,currency_code,attribution_json,identity_policy_reference,source_sync_at)
                    VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(source_connection_id,source_record_hash,outcome_type) DO UPDATE SET subject_key=excluded.subject_key,outcome_date=excluded.outcome_date,revenue_minor_units=excluded.revenue_minor_units,currency_code=excluded.currency_code,attribution_json=excluded.attribution_json,identity_policy_reference=excluded.identity_policy_reference,source_sync_at=excluded.source_sync_at
                """,[(connection_id,website_uuid,row["sourceRecordHash"],row["subjectKey"],row["outcomeType"],row["outcomeDate"],row["revenueMinorUnits"],row["currency"],json.dumps(row["attribution"]),row["identityPolicyReference"],synced_at) for row in rows])
            else: raise ValueError("unsupported_external_source")
            connection.execute("UPDATE analytics.source_sync_executions SET status='succeeded',response_hash=%s,row_count=%s,reconciliation_json=%s,completed_at=now() WHERE id=%s::uuid",(response_hash,len(rows),json.dumps(reconciliation),execution["executionId"]))
            connection.execute("UPDATE app.source_connections SET last_validated_at=coalesce(last_validated_at,now()) WHERE id=%s::uuid",(connection_id,))
        return {"executionId":execution["executionId"],"websiteId":execution["websiteId"],"source":source_type,"status":"succeeded","rowCount":len(rows),"responseHash":response_hash,"reconciliation":reconciliation}

    def fail_external_sync(self, execution_id: str, error_code: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE analytics.source_sync_executions SET status='failed',error_code=%s,completed_at=now() WHERE id=%s::uuid",(error_code[:120],execution_id))

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
