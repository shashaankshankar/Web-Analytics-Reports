from fastapi.testclient import TestClient

from app.config import Settings
from app.auth import TenantContext
from app.external_sources import FirstPartyOutcomeConnector
from app.main import create_app


class StubReporter:
    def report(self,*_): return {"rows":[],"row_count":0,"property_timezone":"America/New_York","data_loss_from_other_row":False}


class StubDatabase:
    configured=True
    def __init__(self,role="agency_owner"): self.role=role; self.annotations=[]; self.goals=[]
    def close(self): pass
    def health(self): return {"status":"ok","database":"measurement","migrated":True}
    def authorize_context(self,email,organization_id=None):
        if organization_id == "other-org": raise PermissionError("membership_not_found")
        return TenantContext("org-1","user-1",email,self.role)
    def company_authorized(self,context,company_id): return context.organization_id == "org-1" and company_id == "company_house_of_dental"
    def website_authorized(self,context,website_id): return context.organization_id == "org-1" and website_id == "website_house_of_dental"
    def sync_status(self,context=None,website_id=None): return {"status":"warning","lastSuccessfulSync":"2026-08-12T12:00:00+00:00","queuedJobs":0,"failedJobs":0,"freshness":"provisional","quality":{"empty":True}}
    def latest_snapshot(self,context,website_id,view,period):
        values={
          "overview":{"websiteId":"website_house_of_dental","period":period,"metrics":[{"metric":"generated_leads","value":0,"previousValue":0},{"metric":"cta_engagement","value":2,"previousValue":1}]},
          "acquisition":{"websiteId":"website_house_of_dental","period":period,"rows":[]},
          "conversion":{"websiteId":"website_house_of_dental","period":period,"funnel":[]},
          "landing-pages":{"websiteId":"website_house_of_dental","period":period,"rows":[]},
          "events":{"websiteId":"website_house_of_dental","period":period,"expectedEvents":[]},
        }
        return values.get(view)
    def latest_measurement_health(self,context,website_id): return None
    def portfolio_summary(self,context,period): return {"organizationId":context.organization_id,"period":period,"measurementCompatibility":"compatible","websites":[{"websiteId":"website_house_of_dental"}]}
    def list_annotations(self,context,website_id): return self.annotations
    def create_annotation(self,context,website_id,annotation_date,annotation_type,note):
        context.require_role(frozenset({"agency_owner","agency_admin","client_admin"}))
        value={"id":"annotation-1","date":annotation_date.isoformat(),"type":annotation_type,"note":note,"createdAt":"2026-08-13T00:00:00+00:00"}; self.annotations.append(value); return value
    def list_goals(self,context,website_id): return self.goals
    def list_goal_metrics(self,context,website_id): return [{"metric":"generated_leads","version":1,"aggregation":"SUM"}]
    def create_goal(self,context,website_id,metric,target,effective_from,effective_to):
        context.require_role(frozenset({"agency_owner","agency_admin","client_admin"}))
        if metric != "generated_leads": raise PermissionError("website_or_approved_metric_not_authorized")
        value={"id":"goal-1","metric":metric,"target":target,"effectiveFrom":effective_from.isoformat(),"effectiveTo":None,"createdAt":"2026-08-13T00:00:00+00:00"}; self.goals.append(value); return value
    def list_recurring_reports(self,context,website_id): return []
    def due_recurring_reports(self,limit=20): return []
    def list_oauth_connections(self,context): return []
    def retention_policy(self,context): return {"aggregateDays":760,"operationsDays":180,"auditDays":2555,"deletionGraceDays":30,"updatedAt":"2026-08-13T00:00:00+00:00"}
    def deletion_preview(self,context,website_id):
        context.require_role(frozenset({"agency_owner","agency_admin","client_admin"})); return {"websiteId":website_id,"reportExecutions":5}
    def request_offboarding(self,context,website_id,confirmation):
        context.require_role(frozenset({"agency_owner","agency_admin"}))
        if confirmation != website_id: raise PermissionError("offboarding_confirmation_mismatch")
        return {"websiteId":website_id,"status":"scheduled","executeAfter":"2026-09-12T00:00:00+00:00"}
    def external_source_status(self,context,website_id): return [{"source":"google_ads","status":"not_configured"},{"source":"search_console","status":"not_configured"},{"source":"call_tracking","status":"not_configured"},{"source":"crm_booking","status":"not_configured"}]
    def register_source_connection(self,context,website_id,source_type,credential_reference,external_account_id,configuration):
        context.require_role(frozenset({"agency_owner","agency_admin"})); return {"connectionId":"source-1","sourceType":source_type,"approvalStatus":"pending_approval"}
    def business_outcomes(self,context,website_id,start_date,end_date): return {"websiteId":website_id,"outcomes":{},"costPerQualifiedLead":None,"caveats":["Null means unavailable"]}
    def google_ads_performance(self,context,website_id,start_date,end_date): return {"websiteId":website_id,"dataStatus":"unavailable","totals":None,"rows":[],"caveats":["Unavailable is not zero"]}
    def search_console_performance(self,context,website_id,start_date,end_date): return {"websiteId":website_id,"dataStatus":"unavailable","totals":None,"pages":[],"queries":[],"caveats":["Unavailable is not zero"]}
    def source_connection_for_website(self,context,website_id,source_type): return {"id":"00000000-0000-0000-0000-000000000001","source_type":source_type,"credential_secret_reference":"projects/project/secrets/source/versions/1","external_account_id":"123","configuration_json":{"identityPolicyReference":"approved_policy_v1"},"approval_status":"approved","disabled_at":None}
    def approve_source_connection(self,context,website_id,source_type,approval_reference,validation): return {"connectionId":"source-1","sourceType":source_type,"approvalStatus":"approved"}
    def begin_external_sync(self,connection_id,source_type,start_date,end_date,request_hash): return {"executionId":"execution-1","websiteId":"website_house_of_dental","idempotentReplay":False}
    def complete_external_sync(self,execution,connection_id,source_type,rows,response_hash,reconciliation): return {"executionId":execution["executionId"],"websiteId":execution["websiteId"],"source":source_type,"status":"succeeded","rowCount":len(rows),"reconciliation":reconciliation}
    def fail_external_sync(self,execution_id,error_code): self.external_failure=error_code
    def list_memberships(self,context): return [{"userId":"user-1","email":"operator@example.com","role":self.role}]
    def upsert_membership(self,context,email,role):
        context.require_role(frozenset({"agency_owner","agency_admin"})); return {"userId":"user-2","email":email,"role":role}


def settings(): return Settings("live",True,True,"549721844","15427015396","x"*32,"127.0.0.1",3000,database_url="postgresql://configured",operator_email="operator@example.com")
def headers(): return {"Authorization":"Bearer "+"x"*32}


def test_docs_health_and_data_protection():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/health").json()["runtime"] == "fastapi"
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/ready").json()["status"] == "ready"
        assert client.get("/api/portfolio/summary").status_code == 401
        assert client.get("/api/portfolio/summary",headers=headers()).status_code == 200
        assert client.get("/agency",headers=headers()).status_code == 200


def test_stored_reporting_contract_and_scope_are_preserved():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        response=client.get("/api/companies/company_house_of_dental/overview?period=28d",headers=headers())
        assert response.status_code == 200
        values={item["metric"]:item["value"] for item in response.json()["metrics"]}
        assert values["generated_leads"] == 0 and values["cta_engagement"] == 2
        assert client.get("/api/websites/not-this-site/sync-status",headers=headers()).status_code == 403
        health=client.get("/api/websites/website_house_of_dental/measurement-health",headers=headers()).json()
        assert health["state"] == "attention_required"
        assert any(check["key"] == "collection" and check["state"] == "warning" for check in health["checks"])


def test_cloud_run_auth_mode_relies_on_platform_identity():
    value=settings(); value=Settings(**{**value.__dict__,"auth_mode":"cloud_run","api_token":""})
    with TestClient(create_app(value,StubReporter(),StubDatabase())) as client:
        assert client.get("/dashboard").status_code == 200
        assert client.get("/api/portfolio/summary").status_code == 200


def test_membership_role_and_resource_boundaries_are_enforced():
    database=StubDatabase(role="client_viewer")
    with TestClient(create_app(settings(),StubReporter(),database)) as client:
        assert client.get("/api/portfolio/summary",headers=headers()).status_code == 403
        assert client.get("/agency",headers=headers()).status_code == 403
        assert client.get("/api/companies/other-company/overview?period=28d",headers=headers()).status_code == 403
        assert client.get("/api/websites/other-site/events?period=28d",headers=headers()).status_code == 403
        assert client.get("/api/websites/website_house_of_dental/events?period=28d",headers=headers()).status_code == 200
        assert client.get("/api/websites/website_house_of_dental/events?period=28d",headers={**headers(),"X-Organization-ID":"other-org"}).status_code == 403
        response=client.post("/api/websites/website_house_of_dental/annotations",headers=headers(),json={"date":"2026-08-13","type":"site_launch","note":"Launch"})
        assert response.status_code == 403


def test_authorized_operator_can_create_and_read_annotations():
    database=StubDatabase()
    with TestClient(create_app(settings(),StubReporter(),database)) as client:
        created=client.post("/api/websites/website_house_of_dental/annotations",headers=headers(),json={"date":"2026-08-13","type":"tracking_change","note":"Approved anchor allowlist deployed"})
        assert created.status_code == 201
        listed=client.get("/api/websites/website_house_of_dental/annotations",headers=headers())
        assert listed.status_code == 200 and listed.json()["annotations"][0]["type"] == "tracking_change"


def test_client_reports_and_approved_goals_use_tenant_scoped_data():
    database=StubDatabase(role="client_admin")
    with TestClient(create_app(settings(),StubReporter(),database)) as client:
        goal=client.post("/api/websites/website_house_of_dental/goals",headers=headers(),json={"metric":"generated_leads","target":50,"effectiveFrom":"2026-09-01"})
        assert goal.status_code == 201 and goal.json()["goal"]["target"] == 50
        assert client.post("/api/websites/website_house_of_dental/goals",headers=headers(),json={"metric":"unapproved_revenue","target":1,"effectiveFrom":"2026-09-01"}).status_code == 403
        pdf=client.get("/api/websites/website_house_of_dental/reports/pdf?period=28d",headers=headers())
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")


def test_client_viewer_can_read_but_not_write_goals():
    database=StubDatabase(role="client_viewer")
    with TestClient(create_app(settings(),StubReporter(),database)) as client:
        assert client.get("/api/websites/website_house_of_dental/goals",headers=headers()).status_code == 200
        response=client.post("/api/websites/website_house_of_dental/goals",headers=headers(),json={"metric":"generated_leads","target":50,"effectiveFrom":"2026-09-01"})
        assert response.status_code == 403


def test_recurring_email_stays_fail_closed_without_owned_configuration():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        listed=client.get("/api/websites/website_house_of_dental/recurring-reports",headers=headers())
        assert listed.status_code == 200 and listed.json()["emailDeliveryConfigured"] is False
        created=client.post("/api/websites/website_house_of_dental/recurring-reports",headers=headers(),json={"name":"Monthly report","period":"last_month","cadence":"monthly","timezone":"America/New_York","recipientReference":"office","nextRunAt":"2026-09-01T13:00:00-04:00"})
        assert created.status_code == 503 and created.json()["detail"] == "report_email_not_configured"


def test_oauth_stays_fail_closed_until_production_configuration_is_approved():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        status=client.get("/api/oauth/google/status",headers=headers())
        assert status.status_code == 200 and status.json()["configured"] is False
        assert client.post("/api/oauth/google/authorize",headers=headers()).status_code == 503
        invalid=client.post("/api/oauth/google/connections/connection-1/assign",headers=headers(),json={"websiteId":"website_house_of_dental","propertyId":"not-a-number","streamId":"1"})
        assert invalid.status_code == 422


def test_offboarding_requires_agency_role_and_exact_confirmation():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        preview=client.get("/api/websites/website_house_of_dental/offboarding-preview",headers=headers())
        assert preview.status_code == 200 and preview.json()["retention"]["deletionGraceDays"] == 30
        wrong=client.post("/api/websites/website_house_of_dental/offboarding",headers=headers(),json={"confirmationWebsiteId":"wrong"})
        assert wrong.status_code == 403
        valid=client.post("/api/websites/website_house_of_dental/offboarding",headers=headers(),json={"confirmationWebsiteId":"website_house_of_dental"})
        assert valid.status_code == 202 and valid.json()["status"] == "scheduled"
    with TestClient(create_app(settings(),StubReporter(),StubDatabase(role="client_admin"))) as client:
        assert client.get("/api/websites/website_house_of_dental/offboarding-preview",headers=headers()).status_code == 200
        assert client.post("/api/websites/website_house_of_dental/offboarding",headers=headers(),json={"confirmationWebsiteId":"website_house_of_dental"}).status_code == 403


def test_external_sources_and_business_outcomes_do_not_invent_missing_data():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        sources=client.get("/api/websites/website_house_of_dental/external-sources",headers=headers()).json()
        assert [item["status"] for item in sources["sources"]] == ["not_configured"]*4
        outcomes=client.get("/api/websites/website_house_of_dental/business-outcomes?startDate=2026-08-01&endDate=2026-08-31",headers=headers())
        assert outcomes.status_code == 200 and outcomes.json()["costPerQualifiedLead"] is None
        paid=client.get("/api/websites/website_house_of_dental/paid-performance?startDate=2026-08-01&endDate=2026-08-31",headers=headers())
        search=client.get("/api/websites/website_house_of_dental/search-performance?startDate=2026-08-01&endDate=2026-08-31",headers=headers())
        assert paid.json()["dataStatus"]=="unavailable" and paid.json()["totals"] is None
        assert search.json()["dataStatus"]=="unavailable" and search.json()["totals"] is None
        assert client.get("/api/websites/website_house_of_dental/business-outcomes?startDate=2026-08-31&endDate=2026-08-01",headers=headers()).status_code == 422


def test_agency_owner_can_manage_memberships_but_viewer_cannot():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        assert client.get("/api/memberships",headers=headers()).status_code == 200
        created=client.post("/api/memberships",headers=headers(),json={"email":"client@example.com","role":"client_viewer"})
        assert created.status_code == 201 and created.json()["cloudRunIamRequired"] is True
    with TestClient(create_app(settings(),StubReporter(),StubDatabase(role="client_viewer"))) as client:
        assert client.get("/api/memberships",headers=headers()).status_code == 403


def test_external_source_registration_accepts_only_versioned_secret_references():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        invalid=client.post("/api/websites/website_house_of_dental/external-sources",headers=headers(),json={"sourceType":"google_ads","credentialSecretReference":"raw-api-key","externalAccountId":"123","configuration":{}})
        assert invalid.status_code == 422
        valid=client.post("/api/websites/website_house_of_dental/external-sources",headers=headers(),json={"sourceType":"google_ads","credentialSecretReference":"projects/project/secrets/google-ads/versions/1","externalAccountId":"123","configuration":{}})
        assert valid.status_code == 201 and valid.json()["connection"]["approvalStatus"]=="pending_approval"


class SourceFactory:
    def create(self,target):
        if target["source_type"] in {"call_tracking","crm_booking"}:
            return FirstPartyOutcomeConnector(target["source_type"],"s"*32,"approved_policy_v1")
        class Connector:
            def validate_access(self): return {"status":"ok","account":"validated"}
        return Connector()


def test_source_approval_and_first_party_ingestion_are_operable_and_audited():
    database=StubDatabase()
    with TestClient(create_app(settings(),StubReporter(),database,source_connector_factory=SourceFactory())) as client:
        approval=client.post("/api/websites/website_house_of_dental/external-sources/google_ads/approve",headers=headers(),json={"confirmationSourceType":"google_ads","approvalReference":"owner-review-2026-08-13"})
        assert approval.status_code==200 and approval.json()["connection"]["approvalStatus"]=="approved"
        mismatch=client.post("/api/websites/website_house_of_dental/external-sources/google_ads/approve",headers=headers(),json={"confirmationSourceType":"search_console","approvalReference":"owner-review-2026-08-13"})
        assert mismatch.status_code==422
        batch=client.post("/api/websites/website_house_of_dental/external-sources/crm_booking/outcomes",headers=headers(),json={"requestId":"batch-2026-08-13-1","records":[{"sourceRecordId":"crm-123","outcomeType":"booked_appointment","outcomeDate":"2026-08-13"}]})
        assert batch.status_code==200 and batch.json()["rowCount"]==1
        rejected=client.post("/api/websites/website_house_of_dental/external-sources/crm_booking/outcomes",headers=headers(),json={"requestId":"batch-2026-08-13-2","records":[{"sourceRecordId":"crm-124","outcomeType":"customer","outcomeDate":"2026-08-13","email":"patient@example.com"}]})
        assert rejected.status_code==502 and not hasattr(database,"external_failure")
