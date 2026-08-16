from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
CONFIRMED_OUTCOME_TYPES = (
    "generated_lead",
    "qualified_lead",
    "booked_appointment",
    "customer",
    "call_answered",
    "call_qualified",
    "revenue",
)
OUTCOME_SOURCE_TYPES = {
    "generated_lead": frozenset({"call_tracking", "crm_booking"}),
    "qualified_lead": frozenset({"crm_booking"}),
    "booked_appointment": frozenset({"crm_booking"}),
    "customer": frozenset({"crm_booking"}),
    "call_answered": frozenset({"call_tracking"}),
    "call_qualified": frozenset({"call_tracking"}),
    "revenue": frozenset({"crm_booking"}),
}
ONBOARDING_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$")
PUBLIC_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
PINNED_SECRET_REFERENCE = re.compile(r"^projects/[A-Za-z0-9._:-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$")
GA4_OAUTH_REFERENCE = re.compile(r"^oauth_connection:[0-9a-fA-F-]{36}$")
SOURCE_TYPES = frozenset({"google_ads", "search_console", "call_tracking", "crm_booking"})
ONBOARDING_CONNECTION_KINDS = frozenset({"ga4", *SOURCE_TYPES})
AGENCY_ADMIN_ROLES = frozenset({"agency_owner", "agency_admin"})
CLIENT_MEMBERSHIP_ROLES = frozenset({"client_admin", "client_viewer"})
MIGRATION_ORDER = (
    "002_production",
    "003_phase4_tenant_isolation",
    "004_phase5_reporting_oauth",
    "005_retention_offboarding",
    "006_external_sources",
    "007_external_sync_provenance",
    "008_source_connection_management",
    "009_oauth_assignment_management",
    "010_onboarding_workflows",
    "011_fact_provenance",
)


def validate_onboarding_identifier(value: str, *, key: str = "identifier") -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid_{key}")
    normalized = value.strip()
    if not PUBLIC_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"invalid_{key}")
    return normalized


def validate_onboarding_idempotency_key(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_onboarding_idempotency_key")
    normalized = value.strip()
    if not ONBOARDING_IDENTIFIER.fullmatch(normalized):
        raise ValueError("invalid_onboarding_idempotency_key")
    return normalized


def normalize_canonical_domain(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_canonical_domain")
    raw = value.strip().lower()
    if not raw or len(raw) > 253 or any(character.isspace() for character in raw):
        raise ValueError("invalid_canonical_domain")
    if raw.endswith("."):
        raw = raw[:-1]
    if not raw or "://" in raw or "/" in raw or "?" in raw or "#" in raw or "@" in raw:
        raise ValueError("invalid_canonical_domain")
    try:
        parsed = urlsplit(f"//{raw}")
        hostname = parsed.hostname
    except ValueError as error:
        raise ValueError("invalid_canonical_domain") from error
    if hostname != raw or parsed.port is not None or not hostname or "." not in hostname:
        raise ValueError("invalid_canonical_domain")
    labels = hostname.split(".")
    if len(labels[-1]) < 2 or any(
        not label or len(label) > 63 or label[0] == "-" or label[-1] == "-" or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        raise ValueError("invalid_canonical_domain")
    return hostname


def validate_workflow_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError("invalid_onboarding_workflow_id") from error


def stable_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


def canonical_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def data_stale_alert(last_successful_sync: datetime | None, now: datetime | None = None, threshold_hours: int = 36) -> dict | None:
    now = now or datetime.now(timezone.utc)
    if last_successful_sync is not None and last_successful_sync >= now - timedelta(hours=threshold_hours):
        return None
    age_hours = None if last_successful_sync is None else round((now - last_successful_sync).total_seconds() / 3600, 1)
    return {
        "key":"data_stale","severity":"critical",
        "detail":{"lastSuccessfulSync":last_successful_sync.isoformat() if last_successful_sync else None,"ageHours":age_hours,"thresholdHours":threshold_hours},
        "openedAt":last_successful_sync.isoformat() if last_successful_sync else None,
    }


def contract_is_outdated(contract_key: str | None, assignment_status: str | None,
                         assigned_version: int | None, latest_approved_version: int | None) -> bool:
    return bool(
        not contract_key or assignment_status != "approved" or
        (latest_approved_version is not None and (assigned_version or 0) < latest_approved_version)
    )


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
            # Serialize migration attempts within this database and fail closed
            # when an existing schema has lost its ledger. Reconstructing an
            # unknown migration state by replaying old DDL is not safe.
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('measurement_platform_schema_migrations', 0))")
            exists = connection.execute(
                "SELECT to_regclass('app.organizations') IS NOT NULL AS exists"
            ).fetchone()["exists"]
            if not exists:
                connection.execute((ROOT / "infra/postgres/001_core.sql").read_text())
            migrations_exist = connection.execute("SELECT to_regclass('app.schema_migrations') IS NOT NULL AS exists").fetchone()["exists"]
            applied = set()
            if migrations_exist:
                applied = {row["version"] for row in connection.execute("SELECT version FROM app.schema_migrations").fetchall()}
            if exists and not migrations_exist:
                raise RuntimeError("migration_ledger_missing")
            if exists and migrations_exist and not applied:
                raise RuntimeError("migration_ledger_empty")

            gap_seen = False
            for version in MIGRATION_ORDER:
                if version in applied:
                    if gap_seen:
                        raise RuntimeError("migration_ledger_out_of_order")
                    continue
                gap_seen = True
                connection.execute((ROOT / "infra/postgres" / f"{version}.sql").read_text())
                applied.add(version)
        return {"status": "ok", "migration": MIGRATION_ORDER[-1]}

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
        visible_alerts = [{"key":row["alert_key"],"severity":row["severity"],"detail":row["detail_json"],"openedAt":row["opened_at"].isoformat()} for row in alerts]
        last_successful_sync = summary["last_successful_sync"]
        temporal_alert = data_stale_alert(last_successful_sync) if website_id else None
        if temporal_alert and not any(item["key"] == "data_stale" for item in visible_alerts):
            visible_alerts.append(temporal_alert)
        return {
            "status": quality["status"] if quality else "never_synced",
            "lastSuccessfulSync": last_successful_sync.isoformat() if last_successful_sync else None,
            "queuedJobs": summary["queued_jobs"], "failedJobs": summary["failed_jobs"],
            "freshness": quality["freshness"] if quality else None,
            "quality": quality["details_json"] if quality else {},
            "checkedAt": quality["checked_at"].isoformat() if quality else None,
            "errorCodes":[{"code":row["error_code"],"count":row["count"]} for row in errors],
            "alerts":visible_alerts,
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
                       mc.slug contract_slug,mcv.version contract_version,mca.approval_status contract_status,
                       latest_contract.latest_approved_version
                  FROM app.websites w
                  JOIN app.companies c ON c.id=w.company_id
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=w.id
                  LEFT JOIN app.website_analytics_assignments a ON a.website_id=w.id AND a.effective_to IS NULL AND a.status='approved'
                  LEFT JOIN app.website_measurement_contract_assignments mca ON mca.website_id=w.id AND mca.effective_to IS NULL
                  LEFT JOIN app.measurement_contract_versions mcv ON mcv.id=mca.measurement_contract_version_id
                  LEFT JOIN app.measurement_contracts mc ON mc.id=mcv.contract_id
                  LEFT JOIN LATERAL (
                    SELECT max(candidate.version) latest_approved_version
                      FROM app.measurement_contract_versions candidate
                     WHERE candidate.contract_id=mcv.contract_id AND candidate.approval_status='approved'
                  ) latest_contract ON true
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
            contract_outdated = contract_is_outdated(contract_key,row["contract_status"],row["contract_version"],row["latest_approved_version"])
            if contract_outdated and not any(item["key"] == "contract_outdated" for item in sync["alerts"]):
                sync["alerts"].append({
                    "key":"contract_outdated","severity":"high",
                    "detail":{"assignedContract":contract_key,"assignmentStatus":row["contract_status"],"latestApprovedVersion":row["latest_approved_version"]},
                    "openedAt":None,
                })
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
            connection.execute("UPDATE app.website_analytics_assignments SET status='revoked',effective_to=current_date WHERE analytics_connection_id=%s::uuid AND effective_to IS NULL",(connection_id,))
            if delete_token:
                connection.execute("DELETE FROM app.oauth_credentials WHERE analytics_connection_id=%s::uuid",(connection_id,))
            else:
                connection.execute("UPDATE app.oauth_credentials SET revoked_at=now() WHERE analytics_connection_id=%s::uuid",(connection_id,))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s,%s,%s,'analytics_connection',%s,%s)",(context.organization_id,context.user_id,"oauth.offboarded" if delete_token else "oauth.revoked",connection_id,json.dumps({"tokenDeleted":delete_token})))
        return True

    def assign_oauth_property(self, context: TenantContext, connection_id: str, website_id: str,
                              property_id: str, stream_id: str, property_name: str | None,
                              timezone_name: str | None, currency_code: str | None,
                              stream_name: str | None, measurement_id: str | None) -> dict:
        context.require_role(frozenset({"agency_owner","agency_admin","client_admin"}))
        property_uuid=stable_id(f"oauth-property:{connection_id}:{property_id}")
        stream_uuid=stable_id(f"oauth-stream:{connection_id}:{property_id}:{stream_id}")
        assignment_uuid=stable_id(f"oauth-assignment:{website_id}:{connection_id}:{property_id}:{stream_id}")
        with self.tenant_connection(context) as connection:
            connection_row=connection.execute("SELECT id FROM app.analytics_connections WHERE id=%s::uuid AND credential_type='oauth' AND status='pending_approval' AND disabled_at IS NULL",(connection_id,)).fetchone()
            website=connection.execute("SELECT resource_id FROM app.resource_identifiers WHERE resource_type='website' AND public_id=%s",(website_id,)).fetchone()
            if not connection_row or not website: raise PermissionError("oauth_connection_or_website_not_authorized")
            active=connection.execute("SELECT id,analytics_connection_id FROM app.website_analytics_assignments WHERE website_id=%s AND effective_to IS NULL AND status='approved'",(website["resource_id"],)).fetchone()
            if active: raise PermissionError("website_already_has_approved_assignment")
            connection.execute("INSERT INTO app.ga_properties(id,analytics_connection_id,external_property_id,display_name,timezone,currency_code,metadata_json) VALUES(%s,%s::uuid,%s,%s,%s,%s,'{}') ON CONFLICT(analytics_connection_id,external_property_id) DO UPDATE SET display_name=excluded.display_name,timezone=excluded.timezone,currency_code=excluded.currency_code",(property_uuid,connection_id,property_id,property_name,timezone_name,currency_code))
            connection.execute("INSERT INTO app.ga_data_streams(id,ga_property_id,external_stream_id,display_name,stream_type) VALUES(%s,%s,%s,%s,'WEB_DATA_STREAM') ON CONFLICT(ga_property_id,external_stream_id) DO UPDATE SET display_name=excluded.display_name",(stream_uuid,property_uuid,stream_id,stream_name))
            reporting_scope={"propertyId":property_id,"streamId":stream_id,"measurementId":measurement_id,"onePropertyPerWebsite":True}
            connection.execute("INSERT INTO app.website_analytics_assignments(id,website_id,analytics_connection_id,ga_property_id,ga_stream_id,reporting_scope,effective_from,status) VALUES(%s,%s,%s::uuid,%s,%s,%s,current_date,'approved')",(assignment_uuid,website["resource_id"],connection_id,property_uuid,stream_uuid,json.dumps(reporting_scope)))
            connection.execute("UPDATE app.analytics_connections SET status='approved' WHERE id=%s::uuid",(connection_id,))
            connection.execute("UPDATE app.oauth_credentials SET last_validated_at=now() WHERE analytics_connection_id=%s::uuid",(connection_id,))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'oauth.assignment_approved','website_analytics_assignment',%s,%s)",(context.organization_id,context.user_id,assignment_uuid,json.dumps({"websiteId":website_id,"propertyId":property_id,"streamId":stream_id,"scope":"https://www.googleapis.com/auth/analytics.readonly"})))
        return {"assignmentId":str(assignment_uuid),"websiteId":website_id,"connectionId":connection_id,"propertyId":property_id,"streamId":stream_id,"measurementId":measurement_id,"status":"approved"}

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
                                (SELECT max(source_sync_at) FROM analytics.first_party_outcomes x WHERE x.source_connection_id=c.id)) last_sync_at,
                       latest.started_at last_attempt_at,latest.status latest_status,latest.error_code last_error_code
                  FROM app.source_connections c JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=c.website_id
                  LEFT JOIN LATERAL (
                    SELECT started_at,status,error_code FROM analytics.source_sync_executions e
                     WHERE e.source_connection_id=c.id ORDER BY started_at DESC LIMIT 1
                  ) latest ON true
                 WHERE p.public_id=%s
            """,(website_id,)).fetchall()
        by_source={row["source_type"]:row for row in rows}
        result=[]
        for source in SOURCES:
            row=by_source.get(source)
            if not row:
                state=ExternalSourceState(source,"not_configured",None,None,None,"approved_source_account_and_credential_required")
            elif row["disabled_at"]:
                state=ExternalSourceState(source,"disabled",row["approval_status"],row["last_validated_at"],row["last_sync_at"],"connection_disabled",row["last_attempt_at"],row["last_error_code"])
            elif row["approval_status"]!="approved":
                state=ExternalSourceState(source,"pending_approval",row["approval_status"],row["last_validated_at"],row["last_sync_at"],"source_governance_approval_required",row["last_attempt_at"],row["last_error_code"])
            elif row["latest_status"]=="failed":
                state=ExternalSourceState(source,"sync_failed",row["approval_status"],row["last_validated_at"],row["last_sync_at"],"source_sync_failed",row["last_attempt_at"],row["last_error_code"])
            elif row["latest_status"]=="incomplete":
                state=ExternalSourceState(source,"partial_data",row["approval_status"],row["last_validated_at"],row["last_sync_at"],"source_dimension_coverage_limited",row["last_attempt_at"],row["last_error_code"])
            else:
                state=ExternalSourceState(source,"active" if row["last_sync_at"] else "approved_awaiting_first_sync",row["approval_status"],row["last_validated_at"],row["last_sync_at"],None,row["last_attempt_at"],row["last_error_code"])
            result.append(state.as_dict())
        return result

    def register_source_connection(self, context: TenantContext, website_id: str, source_type: str,
                                   credential_reference: str, external_account_id: str | None, configuration: dict) -> dict:
        context.require_role(frozenset({"agency_owner","agency_admin"}))
        allowed_configuration={
            "google_ads":{"loginCustomerId","timezone","lookbackDays","finalizationLagDays"},
            "search_console":{"siteUrl","privacyApprovedQueries","timezone","lookbackDays","finalizationLagDays"},
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

    def source_connection_for_website(self, context: TenantContext, website_id: str, source_type: str) -> dict:
        with self.tenant_connection(context) as connection:
            row=connection.execute("""
                SELECT c.id,c.source_type,c.credential_secret_reference,c.external_account_id,c.configuration_json,
                       c.approval_status,c.last_validated_at,c.disabled_at
                  FROM app.source_connections c JOIN app.resource_identifiers p
                    ON p.resource_type='website' AND p.resource_id=c.website_id
                 WHERE p.public_id=%s AND c.source_type=%s
            """,(website_id,source_type)).fetchone()
        if not row: raise PermissionError("source_connection_not_authorized")
        return dict(row)

    def approve_source_connection(self, context: TenantContext, website_id: str, source_type: str,
                                  approval_reference: str, validation: dict) -> dict:
        context.require_role(frozenset({"agency_owner","agency_admin"}))
        with self.tenant_connection(context) as connection:
            row=connection.execute("""
                UPDATE app.source_connections c
                   SET approval_status='approved',last_validated_at=now(),disabled_at=NULL
                  FROM app.resource_identifiers p
                 WHERE p.resource_type='website' AND p.public_id=%s AND p.resource_id=c.website_id
                   AND c.source_type=%s
                RETURNING c.id,c.source_type,c.last_validated_at
            """,(website_id,source_type)).fetchone()
            if not row: raise PermissionError("source_connection_not_authorized")
            detail={"websiteId":website_id,"sourceType":source_type,"approvalReference":approval_reference,"validation":validation}
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'source_connection.approved','source_connection',%s,%s)",(context.organization_id,context.user_id,row["id"],json.dumps(detail)))
        return {"connectionId":str(row["id"]),"sourceType":row["source_type"],"approvalStatus":"approved","lastValidatedAt":row["last_validated_at"].isoformat()}

    def active_external_sync_targets(self) -> list[dict]:
        with self.connection() as connection:
            rows=connection.execute("""
                SELECT id,source_type,configuration_json FROM app.source_connections
                 WHERE approval_status='approved' AND disabled_at IS NULL
                   AND source_type IN ('google_ads','search_console')
                 ORDER BY id
            """).fetchall()
        return [{"connection_id":str(row["id"]),"source_type":row["source_type"],"configuration_json":row["configuration_json"]} for row in rows]

    def source_sync_target(self, connection_id: str) -> dict:
        with self.connection() as connection:
            row=connection.execute("""
                SELECT id,source_type,credential_secret_reference,external_account_id,configuration_json
                  FROM app.source_connections
                 WHERE id=%s::uuid AND approval_status='approved' AND disabled_at IS NULL
            """,(connection_id,)).fetchone()
        if not row: raise PermissionError("external_source_not_approved")
        return dict(row)

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
            outcome_coverage=connection.execute("""
                SELECT DISTINCT c.source_type
                  FROM app.source_connections c
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=c.website_id
                  JOIN analytics.source_sync_executions e ON e.source_connection_id=c.id AND e.website_id=c.website_id
                 WHERE p.public_id=%s AND c.source_type IN ('call_tracking','crm_booking')
                   AND c.approval_status='approved' AND c.disabled_at IS NULL
                   AND e.status='succeeded'
                   AND COALESCE(e.reconciliation_json->>'complete','true')='true'
                   AND e.requested_start_date<=%s AND e.requested_end_date>=%s
            """,(website_id,start_date,end_date)).fetchall()
            complete_outcome_sources={row["source_type"] for row in outcome_coverage}
            available_outcome_types={outcome_type for outcome_type,source_types in OUTCOME_SOURCE_TYPES.items() if complete_outcome_sources.intersection(source_types)}
            outcomes_available=bool(available_outcome_types)
            outcomes=connection.execute("""
                SELECT o.outcome_type,count(*) count,sum(o.revenue_minor_units) revenue_minor_units,
                       min(o.currency_code) FILTER(WHERE o.currency_code IS NOT NULL) currency_code
                  FROM analytics.first_party_outcomes o
                  JOIN app.source_connections c ON c.id=o.source_connection_id
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=o.website_id
                 WHERE p.public_id=%s AND o.outcome_date BETWEEN %s AND %s
                   AND c.source_type IN ('call_tracking','crm_booking')
                   AND c.approval_status='approved' AND c.disabled_at IS NULL
                   AND (
                       (o.outcome_type IN ('generated_lead','qualified_lead') AND c.source_type IN ('call_tracking','crm_booking'))
                       OR (o.outcome_type IN ('booked_appointment','customer','revenue') AND c.source_type='crm_booking')
                       OR (o.outcome_type IN ('call_answered','call_qualified') AND c.source_type='call_tracking')
                   )
                   AND EXISTS(
                       SELECT 1 FROM analytics.source_sync_executions e
                        WHERE e.source_connection_id=o.source_connection_id AND e.website_id=o.website_id
                          AND e.status='succeeded'
                          AND COALESCE(e.reconciliation_json->>'complete','true')='true'
                          AND e.requested_start_date<=%s AND e.requested_end_date>=%s
                   )
                 GROUP BY o.outcome_type
            """,(website_id,start_date,end_date,start_date,end_date)).fetchall() if outcomes_available else []
            ads=connection.execute("""
                WITH complete_execution AS (
                    SELECT e.source_connection_id
                      FROM app.source_connections c
                      JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=c.website_id
                      JOIN analytics.source_sync_executions e ON e.source_connection_id=c.id AND e.website_id=c.website_id
                     WHERE p.public_id=%s AND c.source_type='google_ads'
                       AND c.approval_status='approved' AND c.disabled_at IS NULL
                       AND e.status='succeeded'
                       AND COALESCE(e.reconciliation_json->>'complete','true')='true'
                       AND e.requested_start_date<=%s AND e.requested_end_date>=%s
                     ORDER BY e.completed_at DESC NULLS LAST LIMIT 1
                )
                SELECT EXISTS(SELECT 1 FROM complete_execution) data_available,
                       count(a.source_connection_id) row_count,sum(a.cost_micros) cost_micros,
                       sum(a.clicks) clicks,min(a.currency_code) currency_code
                  FROM complete_execution e
                  LEFT JOIN analytics.google_ads_daily a
                    ON a.source_connection_id=e.source_connection_id
                   AND a.metric_date BETWEEN %s AND %s
            """,(website_id,start_date,end_date,start_date,end_date)).fetchone()
            revenue_channels=connection.execute("""
                SELECT COALESCE(o.attribution_json->>'channel','Unattributed') channel,
                       sum(o.revenue_minor_units) revenue_minor_units
                  FROM analytics.first_party_outcomes o
                  JOIN app.source_connections c ON c.id=o.source_connection_id
                  JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=o.website_id
                 WHERE p.public_id=%s AND o.outcome_type='revenue'
                   AND o.outcome_date BETWEEN %s AND %s
                   AND c.source_type='crm_booking'
                   AND c.approval_status='approved' AND c.disabled_at IS NULL
                   AND EXISTS(
                       SELECT 1 FROM analytics.source_sync_executions e
                        WHERE e.source_connection_id=o.source_connection_id AND e.website_id=o.website_id
                          AND e.status='succeeded'
                          AND COALESCE(e.reconciliation_json->>'complete','true')='true'
                          AND e.requested_start_date<=%s AND e.requested_end_date>=%s
                   )
                 GROUP BY 1 ORDER BY 2 DESC
            """,(website_id,start_date,end_date,start_date,end_date)).fetchall() if "revenue" in available_outcome_types else None
        ads_available=bool(ads["data_available"])
        counts={outcome_type:(0 if outcome_type in available_outcome_types else None) for outcome_type in CONFIRMED_OUTCOME_TYPES}
        counts.update({row["outcome_type"]:row["count"] for row in outcomes if row["outcome_type"] in available_outcome_types})
        qualified=counts["qualified_lead"]; appointments=counts["booked_appointment"]; customers=counts["customer"]
        cost=(float(ads["cost_micros"] or 0)/1_000_000) if ads_available else None
        clicks=(ads["clicks"] or 0) if ads_available else None
        revenue_available="revenue" in available_outcome_types
        revenue_minor_units=(sum(row["revenue_minor_units"] or 0 for row in outcomes if row["outcome_type"]=="revenue") if revenue_available else None)
        outcome_data_status="available" if len(available_outcome_types)==len(CONFIRMED_OUTCOME_TYPES) else ("partial" if outcomes_available else "unavailable")
        caveats=["GA4 intent events are not treated as confirmed business outcomes.","Identity matching occurs only in approved first-party systems; prohibited identifiers are not sent to GA4.","Null KPIs mean the required approved source data is unavailable, not zero."]
        if not ads_available: caveats.append("Google Ads cost, clicks, and impressions are unavailable because no approved complete execution covers this period; unavailable does not mean zero.")
        elif ads["row_count"]==0: caveats.append("Google Ads returned a complete approved execution with zero rows; the zero cost and click totals are measured for this period.")
        if not outcomes_available: caveats.append("Confirmed outcomes, appointments, customers, and revenue are unavailable because no approved complete first-party execution covers this period; unavailable does not mean zero.")
        elif outcome_data_status=="partial": caveats.append("Some confirmed outcome types remain unavailable because their owning approved complete first-party source does not cover this period; unavailable does not mean zero.")
        elif not outcomes: caveats.append("The approved first-party execution completed with zero outcome rows; zero counts and revenue are measured for this period.")
        return {"websiteId":website_id,"startDate":start_date.isoformat(),"endDate":end_date.isoformat(),
                "outcomeDataStatus":outcome_data_status,
                "paidDataStatus":"available" if ads_available else "unavailable",
                "outcomes":counts,"cost":cost,"clicks":clicks,"currency":ads["currency_code"] if ads_available else None,
                "costPerQualifiedLead":cost/qualified if cost is not None and qualified else None,
                "leadToAppointmentRate":appointments/qualified if qualified else None,
                "appointmentToCustomerRate":customers/appointments if appointments else None,
                "revenueMinorUnits":revenue_minor_units,
                "revenueByChannel":[{"channel":row["channel"],"revenueMinorUnits":row["revenue_minor_units"]} for row in revenue_channels] if revenue_channels is not None else None,
                "sourceFamilies":[source for source,available in (("google_ads",ads_available),("approved_first_party",outcomes_available)) if available],
                "caveats":caveats}

    def google_ads_performance(self, context: TenantContext, website_id: str, start_date: date, end_date: date) -> dict:
        with self.tenant_connection(context) as connection:
            total=connection.execute("""
                WITH complete_execution AS (
                    SELECT e.source_connection_id,e.completed_at
                      FROM app.source_connections c
                      JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=c.website_id
                      JOIN analytics.source_sync_executions e ON e.source_connection_id=c.id AND e.website_id=c.website_id
                     WHERE p.public_id=%s AND c.source_type='google_ads'
                       AND c.approval_status='approved' AND c.disabled_at IS NULL
                       AND e.status='succeeded'
                       AND COALESCE(e.reconciliation_json->>'complete','true')='true'
                       AND e.requested_start_date<=%s AND e.requested_end_date>=%s
                     ORDER BY e.completed_at DESC NULLS LAST LIMIT 1
                )
                SELECT EXISTS(SELECT 1 FROM complete_execution) data_available,
                       count(a.source_connection_id) row_count,sum(a.cost_micros) cost_micros,
                       sum(a.clicks) clicks,sum(a.impressions) impressions,
                       min(a.currency_code) currency_code,max(e.completed_at) last_execution_at
                  FROM complete_execution e
                  LEFT JOIN analytics.google_ads_daily a
                    ON a.source_connection_id=e.source_connection_id
                   AND a.metric_date BETWEEN %s AND %s
            """,(website_id,start_date,end_date,start_date,end_date)).fetchone()
            rows=connection.execute("""
                WITH complete_execution AS (
                    SELECT e.source_connection_id
                      FROM app.source_connections c
                      JOIN app.resource_identifiers p ON p.resource_type='website' AND p.resource_id=c.website_id
                      JOIN analytics.source_sync_executions e ON e.source_connection_id=c.id AND e.website_id=c.website_id
                     WHERE p.public_id=%s AND c.source_type='google_ads'
                       AND c.approval_status='approved' AND c.disabled_at IS NULL
                       AND e.status='succeeded'
                       AND COALESCE(e.reconciliation_json->>'complete','true')='true'
                       AND e.requested_start_date<=%s AND e.requested_end_date>=%s
                     ORDER BY e.completed_at DESC NULLS LAST LIMIT 1
                )
                SELECT campaign_id,campaign_name,ad_group_id,sum(a.cost_micros) cost_micros,
                       sum(a.clicks) clicks,sum(a.impressions) impressions,min(a.currency_code) currency_code
                  FROM analytics.google_ads_daily a JOIN complete_execution e
                    ON e.source_connection_id=a.source_connection_id
                 WHERE a.metric_date BETWEEN %s AND %s
                 GROUP BY campaign_id,campaign_name,ad_group_id ORDER BY cost_micros DESC,campaign_id,ad_group_id
            """,(website_id,start_date,end_date,start_date,end_date)).fetchall()
        available=bool(total["data_available"])
        caveats=[] if available else ["Google Ads is not configured, not approved, incomplete, or has no complete execution covering this period; unavailable does not mean zero."]
        if available and total["row_count"]==0: caveats=["A complete approved Google Ads execution returned no rows; zero totals are measured for this period."]
        return {"websiteId":website_id,"startDate":start_date.isoformat(),"endDate":end_date.isoformat(),
                "dataStatus":"available" if available else "unavailable","source":"google_ads",
                "totals":{"costMicros":total["cost_micros"] or 0,"clicks":total["clicks"] or 0,"impressions":total["impressions"] or 0,"currency":total["currency_code"]} if available else None,
                "rows":[{"campaignId":row["campaign_id"],"campaign":row["campaign_name"],"adGroupId":row["ad_group_id"],"costMicros":row["cost_micros"],"clicks":row["clicks"],"impressions":row["impressions"],"currency":row["currency_code"]} for row in rows],
                "lastSyncAt":total["last_execution_at"].isoformat() if total["last_execution_at"] else None,
                "caveats":caveats}

    def search_console_performance(self, context: TenantContext, website_id: str, start_date: date, end_date: date) -> dict:
        with self.tenant_connection(context) as connection:
            total=connection.execute("""
                SELECT count(*) row_count,sum(clicks) clicks,sum(impressions) impressions,
                       CASE WHEN sum(impressions)>0 THEN sum(clicks)::numeric/sum(impressions) END ctr,
                       CASE WHEN sum(impressions)>0 THEN sum(position*impressions)/sum(impressions) END position,
                       max(source_sync_at) last_sync_at
                  FROM analytics.search_console_daily s JOIN app.resource_identifiers p
                    ON p.resource_type='website' AND p.resource_id=s.website_id
                 WHERE p.public_id=%s AND metric_date BETWEEN %s AND %s
            """,(website_id,start_date,end_date)).fetchone()
            pages=connection.execute("""
                SELECT page_path,sum(clicks) clicks,sum(impressions) impressions,
                       CASE WHEN sum(impressions)>0 THEN sum(clicks)::numeric/sum(impressions) END ctr,
                       CASE WHEN sum(impressions)>0 THEN sum(position*impressions)/sum(impressions) END position
                  FROM analytics.search_console_daily s JOIN app.resource_identifiers p
                    ON p.resource_type='website' AND p.resource_id=s.website_id
                 WHERE p.public_id=%s AND metric_date BETWEEN %s AND %s
                 GROUP BY page_path ORDER BY clicks DESC,impressions DESC,page_path LIMIT 100
            """,(website_id,start_date,end_date)).fetchall()
            queries=connection.execute("""
                SELECT query_hash,min(query_text) query_text,sum(clicks) clicks,sum(impressions) impressions,
                       CASE WHEN sum(impressions)>0 THEN sum(clicks)::numeric/sum(impressions) END ctr,
                       CASE WHEN sum(impressions)>0 THEN sum(position*impressions)/sum(impressions) END position
                  FROM analytics.search_console_daily s JOIN app.resource_identifiers p
                    ON p.resource_type='website' AND p.resource_id=s.website_id
                 WHERE p.public_id=%s AND metric_date BETWEEN %s AND %s
                 GROUP BY query_hash ORDER BY clicks DESC,impressions DESC,query_hash LIMIT 100
            """,(website_id,start_date,end_date)).fetchall()
        available=total["row_count"]>0
        metric=lambda row:{"clicks":row["clicks"],"impressions":row["impressions"],"ctr":float(row["ctr"]) if row["ctr"] is not None else None,"position":float(row["position"]) if row["position"] is not None else None}
        return {"websiteId":website_id,"startDate":start_date.isoformat(),"endDate":end_date.isoformat(),
                "dataStatus":"partial_top_rows" if available else "unavailable","source":"search_console",
                "totals":metric(total) if available else None,
                "pages":[{"page":row["page_path"],**metric(row)} for row in pages],
                "queries":[{"queryKey":row["query_hash"],"query":row["query_text"],**metric(row)} for row in queries],
                "lastSyncAt":total["last_sync_at"].isoformat() if total["last_sync_at"] else None,
                "caveats":["Search Console Search Analytics returns top rows and does not guarantee complete dimension coverage."] if available else ["Search Console is not configured or has no approved rows for this period; unavailable does not mean zero."]}

    def _onboarding_admin(self, context: TenantContext) -> None:
        context.require_role(AGENCY_ADMIN_ROLES)

    @staticmethod
    def _onboarding_reference(value: str | None, error_code: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,159}", value.strip()):
            raise ValueError(error_code)
        return value.strip()

    @staticmethod
    def _onboarding_step(connection, workflow_id, organization_id, step_key, state, detail, actor_user_id):
        connection.execute(
            """INSERT INTO app.onboarding_workflow_steps(workflow_id,organization_id,step_key,status,detail_json,updated_by)
               VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s::uuid)
               ON CONFLICT(workflow_id,step_key) DO UPDATE SET status=excluded.status,detail_json=excluded.detail_json,
                 updated_by=excluded.updated_by,updated_at=now()""",
            (workflow_id, organization_id, step_key, state, json.dumps(detail), actor_user_id),
        )

    def _onboarding_refresh(self, connection, workflow_id: str, actor_user_id: str) -> dict:
        workflow = connection.execute(
            """SELECT id,organization_id,company_id,website_id,contract_version_id,governance_status,consent_status
                 FROM app.onboarding_workflows WHERE id=%s::uuid FOR UPDATE""",
            (workflow_id,),
        ).fetchone()
        if not workflow:
            raise ValueError("onboarding_workflow_not_found")

        connections = connection.execute(
            """SELECT connection_kind,mode,status,analytics_connection_id,source_connection_id
                 FROM app.onboarding_connection_requests WHERE workflow_id=%s::uuid ORDER BY connection_kind""",
            (workflow_id,),
        ).fetchall()
        for item in connections:
            if item["mode"] != "registered":
                continue
            approved = False
            if item["connection_kind"] == "ga4" and item["analytics_connection_id"]:
                approved = bool(connection.execute(
                    """SELECT EXISTS(
                              SELECT 1 FROM app.analytics_connections c
                              JOIN app.website_analytics_assignments a ON a.analytics_connection_id=c.id
                             WHERE c.id=%s::uuid AND c.status='approved' AND c.disabled_at IS NULL
                               AND a.website_id=%s::uuid AND a.status='approved' AND a.effective_to IS NULL
                          ) approved""",
                    (item["analytics_connection_id"], workflow["website_id"]),
                ).fetchone()["approved"])
            elif item["source_connection_id"]:
                approved = bool(connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM app.source_connections WHERE id=%s::uuid AND approval_status='approved' AND disabled_at IS NULL) approved",
                    (item["source_connection_id"],),
                ).fetchone()["approved"])
            next_status = "approved" if approved else "pending_approval"
            if item["status"] != "blocked" and item["status"] != next_status:
                connection.execute(
                    "UPDATE app.onboarding_connection_requests SET status=%s,updated_by=%s::uuid,updated_at=now() WHERE workflow_id=%s::uuid AND connection_kind=%s",
                    (next_status, actor_user_id, workflow_id, item["connection_kind"]),
                )

        assignment_status = None
        if workflow["website_id"] and workflow["contract_version_id"]:
            assignment_status = connection.execute(
                """SELECT approval_status FROM app.website_measurement_contract_assignments
                    WHERE website_id=%s::uuid AND measurement_contract_version_id=%s::uuid
                    ORDER BY effective_from DESC LIMIT 1""",
                (workflow["website_id"], workflow["contract_version_id"]),
            ).fetchone()
            assignment_status = assignment_status["approval_status"] if assignment_status else None

        readiness = connection.execute(
            "SELECT status,assignment_id,detail_json FROM app.onboarding_sync_readiness WHERE workflow_id=%s::uuid",
            (workflow_id,),
        ).fetchone()
        successful_sync = False
        if workflow["website_id"]:
            successful_sync = bool(connection.execute(
                """SELECT EXISTS(
                          SELECT 1 FROM analytics.sync_runs r
                          JOIN app.website_analytics_assignments a ON a.id=r.assignment_id
                         WHERE a.website_id=%s::uuid AND r.status='succeeded'
                      ) succeeded""",
                (workflow["website_id"],),
            ).fetchone()["succeeded"])
        if successful_sync and readiness and readiness["status"] != "ready":
            connection.execute(
                """UPDATE app.onboarding_sync_readiness SET status='ready',detail_json=%s,checked_at=now(),updated_at=now()
                    WHERE workflow_id=%s::uuid""",
                (json.dumps({"reason": "successful_sync_observed"}), workflow_id),
            )
            readiness = {"status": "ready", "assignment_id": readiness["assignment_id"], "detail_json": {"reason": "successful_sync_observed"}}
        elif readiness:
            readiness = dict(readiness)

        client_count = connection.execute(
            "SELECT count(*) count FROM app.client_membership_scopes WHERE workflow_id=%s::uuid",
            (workflow_id,),
        ).fetchone()["count"]
        ga4 = next((item for item in connections if item["connection_kind"] == "ga4"), None)
        source_requests = [item for item in connections if item["connection_kind"] != "ga4"]
        ga4_approved = bool(ga4 and ga4["mode"] == "registered" and ga4["status"] == "approved" and assignment_status == "approved")
        governance_ok = workflow["governance_status"] == "approved"
        consent_ok = workflow["consent_status"] == "approved"
        first_sync_ready = bool(readiness and readiness["status"] == "ready")
        handoff_ready = bool(workflow["contract_version_id"] and assignment_status == "approved" and governance_ok and consent_ok and ga4_approved and first_sync_ready and client_count)
        blocked = workflow["governance_status"] == "prohibited" or workflow["consent_status"] == "rejected" or bool(readiness and readiness["status"] == "blocked")
        workflow_status = "ready" if handoff_ready else "blocked" if blocked else "in_progress"
        connection_state = "completed" if ga4_approved else "deferred" if ga4 and ga4["mode"] == "deferred" else "in_progress"
        source_state = "deferred" if not source_requests or all(item["mode"] == "deferred" for item in source_requests) else "completed" if all(item["status"] == "approved" for item in source_requests if item["mode"] == "registered") else "in_progress"
        sync_state = "completed" if first_sync_ready else "blocked" if readiness and readiness["status"] == "blocked" else "in_progress"
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "organization", "completed", {"organizationId": str(workflow["organization_id"])}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "company", "completed" if workflow["company_id"] else "pending", {}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "website", "completed" if workflow["website_id"] else "pending", {}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "measurement_contract", "completed" if workflow["contract_version_id"] else "pending", {"assignmentStatus": assignment_status}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "governance", "completed" if governance_ok else "blocked" if workflow["governance_status"] == "prohibited" else "in_progress", {"status": workflow["governance_status"]}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "consent", "completed" if consent_ok else "blocked" if workflow["consent_status"] == "rejected" else "in_progress", {"status": workflow["consent_status"]}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "ga4_connection", connection_state, {"status": ga4["status"] if ga4 else "not_registered"}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "source_connections", source_state, {"registered": len(source_requests)}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "first_sync", sync_state, {"status": readiness["status"] if readiness else "not_requested"}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "client_access", "completed" if client_count else "in_progress", {"authorizedMembers": client_count}, actor_user_id)
        self._onboarding_step(connection, workflow_id, workflow["organization_id"], "handoff", "completed" if handoff_ready else "blocked" if blocked else "in_progress", {"ready": handoff_ready, "reportDispatch": "not_requested"}, actor_user_id)
        connection.execute(
            "UPDATE app.onboarding_workflows SET status=%s,updated_by=%s::uuid,updated_at=now() WHERE id=%s::uuid",
            (workflow_status, actor_user_id, workflow_id),
        )
        return {"status": workflow_status, "handoffReady": handoff_ready}

    def _onboarding_payload(self, context: TenantContext, workflow_id: str) -> dict:
        workflow_id = validate_workflow_uuid(workflow_id)
        with self.tenant_connection(context) as connection:
            summary = self._onboarding_refresh(connection, workflow_id, context.user_id)
            row = connection.execute(
                """SELECT w.id,w.organization_id,w.status,w.idempotency_key,w.company_id,w.website_id,w.contract_version_id,
                          w.governance_status,w.consent_status,w.governance_reference,w.consent_reference,w.created_at,w.updated_at,
                          o.name organization_name,c.name company_name,cp.public_id company_public_id,wpublic.public_id website_public_id,
                          ws.canonical_domain,mc.slug contract_slug,mcv.version contract_version,mcv.approval_status contract_approval_status
                     FROM app.onboarding_workflows w
                     JOIN app.organizations o ON o.id=w.organization_id
                     LEFT JOIN app.companies c ON c.id=w.company_id
                     LEFT JOIN app.resource_identifiers cp ON cp.resource_type='company' AND cp.resource_id=w.company_id AND cp.organization_id=w.organization_id
                     LEFT JOIN app.websites ws ON ws.id=w.website_id
                     LEFT JOIN app.resource_identifiers wpublic ON wpublic.resource_type='website' AND wpublic.resource_id=w.website_id AND wpublic.organization_id=w.organization_id
                     LEFT JOIN app.measurement_contract_versions mcv ON mcv.id=w.contract_version_id
                     LEFT JOIN app.measurement_contracts mc ON mc.id=mcv.contract_id
                    WHERE w.id=%s::uuid""",
                (workflow_id,),
            ).fetchone()
            if not row:
                raise ValueError("onboarding_workflow_not_found")
            steps = connection.execute(
                "SELECT step_key,status,detail_json,updated_at FROM app.onboarding_workflow_steps WHERE workflow_id=%s::uuid ORDER BY id",
                (workflow_id,),
            ).fetchall()
            connections = connection.execute(
                """SELECT connection_kind,mode,status,idempotency_key,external_property_id,external_stream_id,external_account_id,
                          configuration_json,defer_reason,approval_reference,created_at,updated_at
                     FROM app.onboarding_connection_requests WHERE workflow_id=%s::uuid ORDER BY connection_kind""",
                (workflow_id,),
            ).fetchall()
            readiness = connection.execute(
                "SELECT status,assignment_id,request_idempotency_key,detail_json,requested_at,checked_at,updated_at FROM app.onboarding_sync_readiness WHERE workflow_id=%s::uuid",
                (workflow_id,),
            ).fetchone()
            members = connection.execute(
                """SELECT u.id user_id,u.email,s.role,s.website_id,s.authorization_reference,s.created_at
                     FROM app.client_membership_scopes s JOIN app.users u ON u.id=s.user_id
                    WHERE s.workflow_id=%s::uuid ORDER BY u.email""",
                (workflow_id,),
            ).fetchall()
        checklist = [{"key": item["step_key"], "state": item["status"], "complete": item["status"] in {"completed", "deferred"}, "detail": item["detail_json"]} for item in steps]
        blocking = [item["key"] for item in checklist if item["state"] == "blocked"]
        return {
            "workflowId": str(row["id"]), "organization": {"id": str(row["organization_id"]), "name": row["organization_name"]},
            "status": row["status"], "idempotencyKey": row["idempotency_key"],
            "company": {"id": row["company_public_id"], "name": row["company_name"]},
            "website": {"id": row["website_public_id"], "canonicalDomain": row["canonical_domain"]},
            "contract": {"slug": row["contract_slug"], "version": row["contract_version"], "approvalStatus": row["contract_approval_status"]} if row["contract_version_id"] else None,
            "governance": {"status": row["governance_status"], "reference": row["governance_reference"]},
            "consent": {"status": row["consent_status"], "reference": row["consent_reference"]},
            "connections": [{"kind": item["connection_kind"], "mode": item["mode"], "status": item["status"], "idempotencyKey": item["idempotency_key"], "externalPropertyId": item["external_property_id"], "externalStreamId": item["external_stream_id"], "externalAccountId": item["external_account_id"], "configuration": item["configuration_json"], "deferReason": item["defer_reason"], "approvalReference": item["approval_reference"]} for item in connections],
            "firstSync": {"status": readiness["status"], "assignmentId": str(readiness["assignment_id"]) if readiness["assignment_id"] else None, "requestIdempotencyKey": readiness["request_idempotency_key"], "detail": readiness["detail_json"]} if readiness else {"status": "not_requested"},
            "clientMembers": [{"userId": str(item["user_id"]), "email": item["email"], "role": item["role"], "websiteId": row["website_public_id"], "authorizationReference": item["authorization_reference"]} for item in members],
            "checklist": checklist,
            "handoff": {"ready": summary["handoffReady"], "status": "ready" if summary["handoffReady"] else "blocked" if blocking else "pending", "blockingGates": blocking, "reportDispatch": {"enabled": False, "state": "not_requested", "realClientReportSent": False}},
            "createdAt": row["created_at"].isoformat(), "updatedAt": row["updated_at"].isoformat(),
        }

    def create_onboarding_workflow(self, context: TenantContext, idempotency_key: str, company_id: str,
                                   company_name: str, website_id: str, canonical_domain: str,
                                   contract_slug: str = "local_service_v1") -> dict:
        self._onboarding_admin(context)
        key = validate_onboarding_idempotency_key(idempotency_key)
        company_public_id = validate_onboarding_identifier(company_id, key="company_id")
        website_public_id = validate_onboarding_identifier(website_id, key="website_id")
        company_name = company_name.strip() if isinstance(company_name, str) else ""
        if not company_name or len(company_name) > 200:
            raise ValueError("invalid_company_name")
        domain = normalize_canonical_domain(canonical_domain)
        if contract_slug != "local_service_v1":
            raise ValueError("unsupported_measurement_contract")
        request_hash = canonical_hash({"companyId": company_public_id, "companyName": company_name, "websiteId": website_public_id, "canonicalDomain": domain, "contractSlug": contract_slug})
        workflow_id = str(stable_id(f"onboarding:{context.organization_id}:{key}"))
        replay = False
        with self.tenant_connection(context) as connection:
            existing = connection.execute("SELECT id,request_hash FROM app.onboarding_workflows WHERE organization_id=%s::uuid AND idempotency_key=%s FOR UPDATE", (context.organization_id, key)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ValueError("onboarding_idempotency_conflict")
                workflow_id = str(existing["id"]); replay = True
            else:
                contract = connection.execute(
                    """SELECT v.id FROM app.measurement_contracts c JOIN app.measurement_contract_versions v ON v.contract_id=c.id
                        WHERE c.slug=%s AND v.approval_status='approved' ORDER BY v.version DESC LIMIT 1""",
                    (contract_slug,),
                ).fetchone()
                if not contract:
                    raise ValueError("measurement_contract_not_available")
                company = connection.execute(
                    """SELECT c.id,c.name FROM app.resource_identifiers p JOIN app.companies c ON c.id=p.resource_id
                        WHERE p.organization_id=%s::uuid AND p.resource_type='company' AND p.public_id=%s""",
                    (context.organization_id, company_public_id),
                ).fetchone()
                if company and company["name"].casefold() != company_name.casefold():
                    raise ValueError("company_identifier_conflict")
                company_uuid = company["id"] if company else stable_id(f"onboarding:{context.organization_id}:company:{company_public_id}")
                if not company:
                    connection.execute("INSERT INTO app.companies(id,organization_id,name) VALUES(%s,%s::uuid,%s)", (company_uuid, context.organization_id, company_name))
                    connection.execute("INSERT INTO app.resource_identifiers(organization_id,resource_type,resource_id,public_id) VALUES(%s::uuid,'company',%s,%s)", (context.organization_id, company_uuid, company_public_id))
                website = connection.execute(
                    """SELECT w.id,w.company_id,w.canonical_domain FROM app.resource_identifiers p JOIN app.websites w ON w.id=p.resource_id
                        WHERE p.organization_id=%s::uuid AND p.resource_type='website' AND p.public_id=%s""",
                    (context.organization_id, website_public_id),
                ).fetchone()
                if website and (website["company_id"] != company_uuid or website["canonical_domain"] != domain):
                    raise ValueError("website_identifier_conflict")
                website_uuid = website["id"] if website else stable_id(f"onboarding:{context.organization_id}:website:{website_public_id}")
                if not website:
                    connection.execute("INSERT INTO app.websites(id,company_id,canonical_domain,healthcare_eligibility) VALUES(%s,%s,%s,'requires_review')", (website_uuid, company_uuid, domain))
                    connection.execute("INSERT INTO app.resource_identifiers(organization_id,resource_type,resource_id,public_id) VALUES(%s::uuid,'website',%s,%s)", (context.organization_id, website_uuid, website_public_id))
                assignment = connection.execute("SELECT measurement_contract_version_id FROM app.website_measurement_contract_assignments WHERE website_id=%s AND effective_from=current_date", (website_uuid,)).fetchone()
                if assignment and assignment["measurement_contract_version_id"] != contract["id"]:
                    raise ValueError("website_contract_assignment_conflict")
                if not assignment:
                    connection.execute("INSERT INTO app.website_measurement_contract_assignments(website_id,measurement_contract_version_id,effective_from,approval_status) VALUES(%s,%s,current_date,'pending_approval')", (website_uuid, contract["id"]))
                connection.execute(
                    """INSERT INTO app.onboarding_workflows(id,organization_id,idempotency_key,request_hash,company_id,website_id,contract_version_id,created_by,updated_by)
                       VALUES(%s,%s::uuid,%s,%s,%s,%s,%s,%s::uuid,%s::uuid)""",
                    (workflow_id, context.organization_id, key, request_hash, company_uuid, website_uuid, contract["id"], context.user_id, context.user_id),
                )
                for step in ("organization", "company", "website", "measurement_contract", "governance", "consent", "ga4_connection", "source_connections", "first_sync", "client_access", "handoff"):
                    self._onboarding_step(connection, workflow_id, context.organization_id, step, "pending", {}, context.user_id)
                connection.execute("INSERT INTO app.onboarding_sync_readiness(workflow_id,organization_id,website_id) VALUES(%s,%s::uuid,%s)", (workflow_id, context.organization_id, website_uuid))
                connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'onboarding.workflow_created','onboarding_workflow',%s::uuid,%s)", (context.organization_id, context.user_id, workflow_id, json.dumps({"companyId": company_public_id, "websiteId": website_public_id, "contractSlug": contract_slug})))
        result = self._onboarding_payload(context, workflow_id)
        result["idempotentReplay"] = replay
        return result

    def onboarding_workflow(self, context: TenantContext, workflow_id: str) -> dict:
        self._onboarding_admin(context)
        return self._onboarding_payload(context, workflow_id)

    def record_onboarding_governance(self, context: TenantContext, workflow_id: str, governance_status: str,
                                     consent_status: str, governance_reference: str | None, consent_reference: str | None) -> dict:
        self._onboarding_admin(context)
        workflow_id = validate_workflow_uuid(workflow_id)
        if governance_status not in {"pending_review", "requires_review", "approved", "prohibited"}:
            raise ValueError("invalid_governance_status")
        if consent_status not in {"pending_client_consent", "approved", "rejected"}:
            raise ValueError("invalid_consent_status")
        governance_reference = self._onboarding_reference(governance_reference, "governance_reference_required") if governance_status != "pending_review" else None
        consent_reference = self._onboarding_reference(consent_reference, "consent_reference_required") if consent_status != "pending_client_consent" else None
        with self.tenant_connection(context) as connection:
            workflow = connection.execute("SELECT organization_id,website_id FROM app.onboarding_workflows WHERE id=%s::uuid FOR UPDATE", (workflow_id,)).fetchone()
            if not workflow:
                raise ValueError("onboarding_workflow_not_found")
            eligibility = "approved" if governance_status == "approved" else "prohibited" if governance_status == "prohibited" else "requires_review"
            connection.execute("UPDATE app.websites SET healthcare_eligibility=%s WHERE id=%s", (eligibility, workflow["website_id"]))
            connection.execute("UPDATE app.onboarding_workflows SET governance_status=%s,consent_status=%s,governance_reference=%s,consent_reference=%s,updated_by=%s::uuid,updated_at=now() WHERE id=%s::uuid", (governance_status, consent_status, governance_reference, consent_reference, context.user_id, workflow_id))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'onboarding.governance_recorded','onboarding_workflow',%s::uuid,%s)", (context.organization_id, context.user_id, workflow_id, json.dumps({"governanceStatus": governance_status, "consentStatus": consent_status, "governanceReference": governance_reference, "consentReference": consent_reference})))
        return self._onboarding_payload(context, workflow_id)

    def register_onboarding_connection(self, context: TenantContext, workflow_id: str, connection_kind: str, mode: str,
                                       idempotency_key: str, credential_type: str | None, credential_reference: str | None,
                                       external_property_id: str | None, external_stream_id: str | None,
                                       external_account_id: str | None, configuration: dict, defer_reason: str | None) -> dict:
        self._onboarding_admin(context)
        workflow_id = validate_workflow_uuid(workflow_id)
        key = validate_onboarding_idempotency_key(idempotency_key)
        if connection_kind not in ONBOARDING_CONNECTION_KINDS or mode not in {"registered", "deferred"}:
            raise ValueError("invalid_onboarding_connection")
        configuration = configuration if isinstance(configuration, dict) else {}
        if mode == "deferred":
            if not isinstance(defer_reason, str) or not defer_reason.strip() or len(defer_reason.strip()) > 500:
                raise ValueError("defer_reason_required")
            if any(value is not None for value in (credential_type, credential_reference, external_property_id, external_stream_id, external_account_id)) or configuration:
                raise ValueError("deferred_connection_must_not_include_credentials")
        elif connection_kind == "ga4":
            if credential_type not in {"service_account", "oauth"} or not isinstance(credential_reference, str):
                raise ValueError("ga4_connection_reference_required")
            if credential_type == "service_account" and credential_reference != "application_default_credentials" and not PINNED_SECRET_REFERENCE.fullmatch(credential_reference):
                raise ValueError("version_pinned_secret_reference_required")
            if credential_type == "oauth" and not GA4_OAUTH_REFERENCE.fullmatch(credential_reference):
                raise ValueError("oauth_connection_reference_required")
            if (external_property_id is None) != (external_stream_id is None) or (external_property_id and not str(external_property_id).isdigit()) or (external_stream_id and not str(external_stream_id).isdigit()):
                raise ValueError("invalid_google_resource_identifier")
        else:
            if not isinstance(credential_reference, str) or not PINNED_SECRET_REFERENCE.fullmatch(credential_reference):
                raise ValueError("version_pinned_secret_reference_required")
            allowed = {"google_ads": {"loginCustomerId", "timezone", "lookbackDays", "finalizationLagDays"}, "search_console": {"siteUrl", "privacyApprovedQueries", "timezone", "lookbackDays", "finalizationLagDays"}, "call_tracking": {"provider", "identityPolicyReference"}, "crm_booking": {"provider", "identityPolicyReference"}}[connection_kind]
            if set(configuration) - allowed:
                raise ValueError("unapproved_source_configuration")
            if connection_kind == "google_ads" and not external_account_id:
                raise ValueError("google_ads_customer_id_required")
            if connection_kind == "search_console":
                site_url = configuration.get("siteUrl")
                if not isinstance(site_url, str) or not site_url.startswith("https://"):
                    raise ValueError("search_console_site_required")
                parsed = urlsplit(site_url)
                normalize_canonical_domain(parsed.hostname or "")
                if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
                    raise ValueError("invalid_search_console_site")
            if connection_kind in {"call_tracking", "crm_booking"} and not isinstance(configuration.get("identityPolicyReference"), str):
                raise ValueError("identity_policy_reference_required")
            if "timezone" in configuration:
                try: ZoneInfo(configuration["timezone"])
                except (TypeError, ZoneInfoNotFoundError): raise ValueError("invalid_source_timezone")
            for field, minimum, maximum in (("lookbackDays", 1, 90), ("finalizationLagDays", 1, 14)):
                if field in configuration and (isinstance(configuration[field], bool) or not isinstance(configuration[field], int) or not minimum <= configuration[field] <= maximum):
                    raise ValueError(f"invalid_{field}")
        request_hash = canonical_hash({"kind": connection_kind, "mode": mode, "idempotencyKey": key, "credentialType": credential_type, "credentialReference": credential_reference, "externalPropertyId": external_property_id, "externalStreamId": external_stream_id, "externalAccountId": external_account_id, "configuration": configuration, "deferReason": defer_reason})
        with self.tenant_connection(context) as probe:
            existing_probe = probe.execute("SELECT request_hash FROM app.onboarding_connection_requests WHERE workflow_id=%s::uuid AND connection_kind=%s", (workflow_id, connection_kind)).fetchone()
        if existing_probe:
            if existing_probe["request_hash"] != request_hash:
                raise ValueError("onboarding_connection_idempotency_conflict")
            result = self._onboarding_payload(context, workflow_id)
            result["idempotentReplay"] = True
            return result
        with self.tenant_connection(context) as connection:
            workflow = connection.execute("SELECT organization_id,website_id,contract_version_id FROM app.onboarding_workflows WHERE id=%s::uuid FOR UPDATE", (workflow_id,)).fetchone()
            if not workflow:
                raise ValueError("onboarding_workflow_not_found")
            existing = connection.execute("SELECT id,request_hash,idempotency_key FROM app.onboarding_connection_requests WHERE workflow_id=%s::uuid AND connection_kind=%s FOR UPDATE", (workflow_id, connection_kind)).fetchone()
            if existing and existing["request_hash"] == request_hash:
                raise ValueError("onboarding_connection_idempotency_conflict")
            if existing and existing["idempotency_key"] == key:
                raise ValueError("onboarding_connection_idempotency_conflict")
            analytics_connection_id = None; source_connection_id = None; status = "deferred" if mode == "deferred" else "pending_approval"
            if mode == "registered" and connection_kind == "ga4":
                analytics_connection_id = stable_id(f"onboarding:{workflow_id}:ga4")
                connection.execute("""INSERT INTO app.analytics_connections(id,organization_id,credential_type,credential_reference,status,disabled_at)
                    VALUES(%s,%s::uuid,%s,%s,'pending_approval',NULL)
                    ON CONFLICT(id) DO UPDATE SET credential_type=excluded.credential_type,credential_reference=excluded.credential_reference,status='pending_approval',disabled_at=NULL""", (analytics_connection_id, context.organization_id, credential_type, credential_reference))
                if external_property_id:
                    property_id = stable_id(f"onboarding:{workflow_id}:property:{external_property_id}")
                    stream_id = stable_id(f"onboarding:{workflow_id}:stream:{external_stream_id}")
                    connection.execute("""INSERT INTO app.ga_properties(id,analytics_connection_id,external_property_id,display_name)
                        VALUES(%s,%s,%s,%s) ON CONFLICT(analytics_connection_id,external_property_id) DO UPDATE SET display_name=excluded.display_name""", (property_id, analytics_connection_id, external_property_id, external_property_id))
                    connection.execute("""INSERT INTO app.ga_data_streams(id,ga_property_id,external_stream_id,display_name,stream_type)
                        VALUES(%s,%s,%s,%s,'WEB_DATA_STREAM') ON CONFLICT(ga_property_id,external_stream_id) DO UPDATE SET display_name=excluded.display_name""", (stream_id, property_id, external_stream_id, external_stream_id))
                    existing_assignment = connection.execute("SELECT id,ga_property_id,ga_stream_id FROM app.website_analytics_assignments WHERE website_id=%s AND effective_from=current_date", (workflow["website_id"],)).fetchone()
                    if existing_assignment and (existing_assignment["ga_property_id"] != property_id or existing_assignment["ga_stream_id"] != stream_id):
                        raise ValueError("website_analytics_assignment_conflict")
                    if not existing_assignment:
                        connection.execute("""INSERT INTO app.website_analytics_assignments(website_id,analytics_connection_id,ga_property_id,ga_stream_id,reporting_scope,effective_from,status)
                            VALUES(%s,%s,%s,%s,%s,current_date,'pending_approval')""", (workflow["website_id"], analytics_connection_id, property_id, stream_id, json.dumps({"propertyId": external_property_id, "streamId": external_stream_id})))
            elif mode == "registered":
                source_connection_id = connection.execute("""INSERT INTO app.source_connections(organization_id,website_id,source_type,credential_secret_reference,external_account_id,configuration_json,approval_status,disabled_at)
                    VALUES(%s::uuid,%s,%s,%s,%s,%s,'pending_approval',NULL)
                    ON CONFLICT(website_id,source_type) DO UPDATE SET credential_secret_reference=excluded.credential_secret_reference,external_account_id=excluded.external_account_id,configuration_json=excluded.configuration_json,approval_status='pending_approval',last_validated_at=NULL,disabled_at=NULL
                    RETURNING id""", (context.organization_id, workflow["website_id"], connection_kind, credential_reference, external_account_id, json.dumps(configuration))).fetchone()["id"]
            if existing:
                connection.execute("""UPDATE app.onboarding_connection_requests SET mode=%s,status=%s,idempotency_key=%s,request_hash=%s,analytics_connection_id=%s,source_connection_id=%s,
                    external_property_id=%s,external_stream_id=%s,external_account_id=%s,configuration_json=%s,defer_reason=%s,updated_by=%s::uuid,updated_at=now()
                    WHERE id=%s""", (mode, status, key, request_hash, analytics_connection_id, source_connection_id, external_property_id, external_stream_id, external_account_id, json.dumps(configuration), defer_reason.strip() if isinstance(defer_reason, str) else None, context.user_id, existing["id"]))
            else:
                connection.execute("""INSERT INTO app.onboarding_connection_requests(workflow_id,organization_id,connection_kind,mode,status,idempotency_key,request_hash,analytics_connection_id,source_connection_id,external_property_id,external_stream_id,external_account_id,configuration_json,defer_reason,created_by,updated_by)
                    VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid,%s::uuid)""", (workflow_id, context.organization_id, connection_kind, mode, status, key, request_hash, analytics_connection_id, source_connection_id, external_property_id, external_stream_id, external_account_id, json.dumps(configuration), defer_reason.strip() if isinstance(defer_reason, str) else None, context.user_id, context.user_id))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'onboarding.connection_recorded','onboarding_workflow',%s::uuid,%s)", (context.organization_id, context.user_id, workflow_id, json.dumps({"connectionKind": connection_kind, "mode": mode, "status": status})))
        return self._onboarding_payload(context, workflow_id)

    def onboarding_first_sync(self, context: TenantContext, workflow_id: str, action: str, idempotency_key: str | None = None) -> dict:
        self._onboarding_admin(context)
        workflow_id = validate_workflow_uuid(workflow_id)
        if action not in {"request", "check"}:
            raise ValueError("invalid_first_sync_action")
        if action == "request":
            key = validate_onboarding_idempotency_key(idempotency_key or "")
        else:
            key = None
        with self.tenant_connection(context) as connection:
            workflow = connection.execute("SELECT website_id FROM app.onboarding_workflows WHERE id=%s::uuid FOR UPDATE", (workflow_id,)).fetchone()
            if not workflow:
                raise ValueError("onboarding_workflow_not_found")
            ga4 = connection.execute("SELECT mode,status FROM app.onboarding_connection_requests WHERE workflow_id=%s::uuid AND connection_kind='ga4'", (workflow_id,)).fetchone()
            assignment = connection.execute("""SELECT a.id FROM app.website_analytics_assignments a JOIN app.analytics_connections c ON c.id=a.analytics_connection_id
                WHERE a.website_id=%s::uuid AND a.status='approved' AND a.effective_to IS NULL AND c.status='approved' AND c.disabled_at IS NULL ORDER BY a.effective_from DESC LIMIT 1""", (workflow["website_id"],)).fetchone()
            readiness = connection.execute("SELECT status,request_idempotency_key FROM app.onboarding_sync_readiness WHERE workflow_id=%s::uuid FOR UPDATE", (workflow_id,)).fetchone()
            succeeded = bool(assignment and connection.execute("SELECT EXISTS(SELECT 1 FROM analytics.sync_runs WHERE assignment_id=%s AND status='succeeded') succeeded", (assignment["id"],)).fetchone()["succeeded"])
            if succeeded:
                state, detail = "ready", {"reason": "successful_sync_observed"}
            elif not ga4:
                state, detail = "blocked", {"code": "ga4_connection_required"}
            elif ga4["mode"] == "deferred":
                state, detail = "blocked", {"code": "ga4_connection_deferred"}
            elif not assignment:
                state, detail = "blocked", {"code": "ga4_assignment_approval_required"}
            elif action == "request":
                state, detail = "requested", {"code": "first_sync_requested", "externalExecutionRequired": True, "reportDispatch": "not_requested"}
            else:
                state, detail = readiness["status"] if readiness else "not_requested", {"code": "awaiting_first_sync"}
            if action == "request" and readiness and readiness["request_idempotency_key"] == key and readiness["status"] == "requested":
                replay = True
            else:
                replay = False
                connection.execute("""INSERT INTO app.onboarding_sync_readiness(workflow_id,organization_id,website_id,assignment_id,status,request_idempotency_key,detail_json,requested_by,requested_at,checked_at,updated_at)
                    VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s::uuid,CASE WHEN %s IS NULL THEN NULL ELSE now() END,now(),now())
                    ON CONFLICT(workflow_id) DO UPDATE SET assignment_id=excluded.assignment_id,status=excluded.status,request_idempotency_key=excluded.request_idempotency_key,detail_json=excluded.detail_json,requested_by=excluded.requested_by,requested_at=excluded.requested_at,checked_at=now(),updated_at=now()""", (workflow_id, context.organization_id, workflow["website_id"], assignment["id"] if assignment else None, state, key, json.dumps(detail), context.user_id if action == "request" else None, key))
                if action == "request":
                    connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'onboarding.first_sync_requested','onboarding_workflow',%s::uuid,%s)", (context.organization_id, context.user_id, workflow_id, json.dumps(detail)))
        payload = self._onboarding_payload(context, workflow_id)
        payload["firstSync"]["idempotentReplay"] = replay
        return payload

    def add_onboarding_client_membership(self, context: TenantContext, workflow_id: str, email: str, role: str, authorization_reference: str) -> dict:
        self._onboarding_admin(context)
        workflow_id = validate_workflow_uuid(workflow_id)
        email = email.strip().lower() if isinstance(email, str) else ""
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
            raise ValueError("invalid_client_member_email")
        if role not in CLIENT_MEMBERSHIP_ROLES:
            raise ValueError("invalid_client_membership_role")
        authorization_reference = self._onboarding_reference(authorization_reference, "authorization_reference_required")
        with self.tenant_connection(context) as connection:
            workflow = connection.execute("SELECT organization_id,website_id FROM app.onboarding_workflows WHERE id=%s::uuid FOR UPDATE", (workflow_id,)).fetchone()
            if not workflow:
                raise ValueError("onboarding_workflow_not_found")
            user = connection.execute("INSERT INTO app.users(email) VALUES(%s) ON CONFLICT(email) DO UPDATE SET email=excluded.email RETURNING id,email", (email,)).fetchone()
            existing = connection.execute("SELECT role FROM app.memberships WHERE organization_id=%s::uuid AND user_id=%s", (context.organization_id, user["id"])).fetchone()
            if existing and existing["role"] in {"agency_owner", "agency_admin", "agency_analyst"}:
                raise ValueError("cannot_reclassify_agency_member")
            connection.execute("INSERT INTO app.memberships(organization_id,user_id,role) VALUES(%s::uuid,%s,%s) ON CONFLICT(organization_id,user_id) DO UPDATE SET role=excluded.role", (context.organization_id, user["id"], role))
            connection.execute("""INSERT INTO app.client_membership_scopes(organization_id,workflow_id,user_id,website_id,role,authorization_reference,created_by)
                VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s::uuid)
                ON CONFLICT(organization_id,user_id,website_id) DO UPDATE SET workflow_id=excluded.workflow_id,role=excluded.role,authorization_reference=excluded.authorization_reference,created_by=excluded.created_by""", (context.organization_id, workflow_id, user["id"], workflow["website_id"], role, authorization_reference, context.user_id))
            connection.execute("INSERT INTO audit.events(organization_id,actor_user_id,action,target_type,target_id,detail_json) VALUES(%s::uuid,%s::uuid,'onboarding.client_membership_authorized','user',%s,%s)", (context.organization_id, context.user_id, user["id"], json.dumps({"workflowId": workflow_id, "websiteId": str(workflow["website_id"]), "role": role, "authorizationReference": authorization_reference})))
        payload = self._onboarding_payload(context, workflow_id)
        payload["membership"] = next(item for item in payload["clientMembers"] if item["email"] == email)
        return payload

    def onboarding_checklist(self, context: TenantContext, workflow_id: str) -> dict:
        self._onboarding_admin(context)
        payload = self._onboarding_payload(context, workflow_id)
        return {"workflowId": payload["workflowId"], "status": payload["status"], "checklist": payload["checklist"], "handoff": payload["handoff"], "externalGates": {"credentials": "explicit_reference_or_deferred", "approvals": "caller_supplied_only", "reportDispatch": "not_requested"}}

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
            if existing and existing["status"] in {"succeeded","incomplete"}:
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
            execution_status="succeeded" if reconciliation.get("complete",True) else "incomplete"
            connection.execute("UPDATE analytics.source_sync_executions SET status=%s,response_hash=%s,row_count=%s,reconciliation_json=%s,completed_at=now() WHERE id=%s::uuid",(execution_status,response_hash,len(rows),json.dumps(reconciliation),execution["executionId"]))
            connection.execute("UPDATE app.source_connections SET last_validated_at=coalesce(last_validated_at,now()) WHERE id=%s::uuid",(connection_id,))
        return {"executionId":execution["executionId"],"websiteId":execution["websiteId"],"source":source_type,"status":execution_status,"rowCount":len(rows),"responseHash":response_hash,"reconciliation":reconciliation}

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
