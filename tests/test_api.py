from fastapi.testclient import TestClient
from contextlib import contextmanager
from datetime import date, datetime, timezone

from app.config import Settings
from app.auth import TenantContext
from app.external_sources import FirstPartyOutcomeConnector
from app.main import create_app
from app.storage import Database


class StubReporter:
    def report(self,*_): return {"rows":[],"row_count":0,"property_timezone":"America/New_York","data_loss_from_other_row":False}


class StubDatabase:
    configured=True
    def __init__(self,role="agency_owner"): self.role=role; self.annotations=[]; self.goals=[]; self.oauth_states=[]
    def close(self): pass
    def health(self): return {"status":"ok","database":"measurement","migrated":True}
    def authorize_context(self,email,organization_id=None):
        if organization_id == "other-org": raise PermissionError("membership_not_found")
        return TenantContext("org-1","user-1",email,self.role)
    def company_authorized(self,context,company_id): return context.organization_id == "org-1" and company_id == "company_house_of_dental"
    def website_authorized(self,context,website_id): return context.organization_id == "org-1" and website_id == "website_house_of_dental"
    def website_site_context(self,context,website_id):
        if website_id != "website_house_of_dental": raise PermissionError("website_not_authorized")
        return {"company_id":"company_house_of_dental","company":"The House of Dental","site_id":"website_house_of_dental","canonical_domain":"https://thehouseofdentalwp.com","governance_status":"approved","property_timezone":"America/New_York","property_id":"549721844","stream_id":"15427015396","measurement_id":"G-TC66MQQ0T7",}
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
    def create_oauth_state(self,context,state_hash,verifier_ciphertext,scopes,redirect_uri,expires_at):
        self.oauth_states.append({"stateHash":state_hash,"verifierCiphertext":verifier_ciphertext,"scopes":scopes,"redirectUri":redirect_uri,"expiresAt":expires_at})
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


class DispatchDatabase(StubDatabase):
    def __init__(self,reports,delivery_ids):
        super().__init__()
        self.reports=reports
        self.delivery_ids=delivery_ids
        self.finished=[]

    def due_recurring_reports(self,limit=20): return self.reports[:limit]
    def begin_report_delivery(self,report): return self.delivery_ids[report["id"]]
    def finish_report_delivery(self,report,delivery_id,report_hash,provider_message_id,error_code):
        self.finished.append({"reportId":report["id"],"deliveryId":delivery_id,"reportHash":report_hash,"providerMessageId":provider_message_id,"errorCode":error_code})


class DispatchSender:
    configured=True

    def __init__(self,outcomes): self.outcomes=outcomes
    def send_pdf(self,recipient_reference,*_):
        outcome=self.outcomes[recipient_reference]
        if isinstance(outcome,BaseException): raise outcome
        return outcome


def dispatch_settings(): return Settings(**{**settings().__dict__,"internal_trigger_token":"i"*32})
def internal_headers(): return {"X-Internal-Trigger-Token":"i"*32}
def dispatch_report(report_id,recipient_reference):
    return {"id":report_id,"organization_id":"org-1","created_by":"user-1","website_id":"website_house_of_dental","period_key":"28d","recipient_secret_reference":recipient_reference,"company":"House of Dental","next_run_at":"2026-08-14T13:00:00+00:00","cadence":"monthly"}


def settings(): return Settings("live",True,True,"549721844","15427015396","x"*32,"127.0.0.1",3000,database_url="postgresql://configured",operator_email="operator@example.com")
def headers(): return {"Authorization":"Bearer "+"x"*32}


def test_docs_health_and_data_protection():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        assert client.get("/").status_code == 200
        assert client.get("/favicon.ico").status_code == 204
        assert client.get("/docs").status_code == 200
        assert client.get("/health").json()["runtime"] == "fastapi"
        assert client.get("/healthz").json()["status"] == "ready"
        assert client.get("/ready").json()["status"] == "ready"
        assert client.get("/api/portfolio/summary").status_code == 401
        assert client.get("/api/portfolio/summary",headers=headers()).status_code == 200
        agency=client.get("/agency",headers=headers())
        assert agency.status_code == 200
        assert 'id="oauth-connect"' in agency.text
        assert "/api/oauth/google/authorize" in agency.text


def test_readiness_fails_closed_when_database_is_unavailable():
    database=StubDatabase()
    database.health=lambda: {"status":"error","reason":"ConnectionTimeout"}
    with TestClient(create_app(settings(),StubReporter(),database)) as client:
        assert client.get("/health").status_code == 200
        healthz=client.get("/healthz")
        ready=client.get("/ready")
        assert healthz.status_code == 503 and healthz.json()["status"] == "not_ready"
        assert ready.status_code == 503 and ready.json()["status"] == "not_ready"


def test_stored_reporting_contract_and_scope_are_preserved():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        response=client.get("/api/companies/company_house_of_dental/overview?period=28d",headers=headers())
        assert response.status_code == 200
        values={item["metric"]:item["value"] for item in response.json()["metrics"]}
        assert values["generated_leads"] == 0 and values["cta_engagement"] == 2
        assert client.get("/api/websites/not-this-site/sync-status",headers=headers()).status_code == 403
        health=client.get("/api/websites/website_house_of_dental/measurement-health",headers=headers()).json()
        assert health["state"] == "attention_required"
        assert health["websiteId"] == "website_house_of_dental"
        assert health["governanceStatus"] == "approved"
        assert any(check["key"] == "collection" and check["state"] == "warning" for check in health["checks"])
        assert "deploymentStatus" not in health
        assert "publicCollectionStatus" not in health


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


def test_report_dispatch_preserves_success_and_already_sent_results(monkeypatch):
    database=DispatchDatabase(
        [dispatch_report("report-sent","recipient-sent"),dispatch_report("report-already","recipient-already")],
        {"report-sent":"delivery-sent","report-already":None},
    )
    sender=DispatchSender({"recipient-sent":"provider-message-id"})
    monkeypatch.setattr("app.main.ReportEmailSender",lambda *_: sender)
    monkeypatch.setattr("app.main.build_client_pdf",lambda *_: b"pdf")
    with TestClient(create_app(dispatch_settings(),StubReporter(),database)) as client:
        response=client.post("/internal/reports/dispatch",headers=internal_headers())
    assert response.status_code == 200
    assert response.json() == {"status":"ok","processed":2,"deliveries":[
        {"reportId":"report-sent","status":"sent","deliveryId":"delivery-sent"},
        {"reportId":"report-already","status":"already_sent"},
    ]}
    assert len(database.finished) == 1
    assert database.finished[0]["providerMessageId"] == "provider-message-id" and database.finished[0]["errorCode"] is None


def test_report_dispatch_returns_non_2xx_for_mixed_failures_and_preserves_ledger(monkeypatch):
    database=DispatchDatabase(
        [dispatch_report("report-sent","recipient-sent"),dispatch_report("report-failed","recipient-failed"),dispatch_report("report-blocked","recipient-blocked")],
        {"report-sent":"delivery-sent","report-failed":"delivery-failed","report-blocked":"delivery-blocked"},
    )
    sender=DispatchSender({
        "recipient-sent":"provider-message-id",
        "recipient-failed":RuntimeError("provider_unavailable:"+"secret-provider-payload"),
        "recipient-blocked":RuntimeError("report_email_not_configured"),
    })
    monkeypatch.setattr("app.main.ReportEmailSender",lambda *_: sender)
    monkeypatch.setattr("app.main.build_client_pdf",lambda *_: b"pdf")
    with TestClient(create_app(dispatch_settings(),StubReporter(),database)) as client:
        response=client.post("/internal/reports/dispatch",headers=internal_headers())
    body=response.json()
    assert response.status_code == 502
    assert body["detail"]["code"] == "report_dispatch_failed" and body["detail"]["processed"] == 3
    assert body["detail"]["deliveries"] == [
        {"reportId":"report-sent","status":"sent","deliveryId":"delivery-sent"},
        {"reportId":"report-failed","status":"failed","errorCode":"HTTPException"},
        {"reportId":"report-blocked","status":"failed","errorCode":"HTTPException"},
    ]
    assert "recipient-failed" not in response.text and "secret-provider-payload" not in response.text
    assert [(item["deliveryId"],item["providerMessageId"],item["errorCode"]) for item in database.finished] == [
        ("delivery-sent","provider-message-id",None),
        ("delivery-failed",None,"HTTPException"),
        ("delivery-blocked",None,"HTTPException"),
    ]


def test_oauth_stays_fail_closed_until_production_configuration_is_approved():
    with TestClient(create_app(settings(),StubReporter(),StubDatabase())) as client:
        status=client.get("/api/oauth/google/status",headers=headers())
        assert status.status_code == 200 and status.json()["configured"] is False
        assert client.post("/api/oauth/google/authorize",headers=headers()).status_code == 503
        invalid=client.post("/api/oauth/google/connections/connection-1/assign",headers=headers(),json={"websiteId":"website_house_of_dental","propertyId":"not-a-number","streamId":"1"})
        assert invalid.status_code == 422


def test_oauth_testing_mode_can_authorize_without_claiming_public_approval(monkeypatch):
    class StubCipher:
        configured=True
        def encrypt(self,plaintext,associated_data):
            assert plaintext and associated_data.startswith("oauth-state:")
            return b"encrypted-verifier"
    monkeypatch.setattr("app.main.KmsCipher",lambda _key: StubCipher())
    value=Settings(**{
        **settings().__dict__,
        "google_oauth_client_id":"client-id",
        "google_oauth_client_secret":"client-secret",
        "google_oauth_redirect_uri":"https://service.example/oauth/google/callback",
        "google_oauth_state_secret":"s"*32,
        "google_oauth_kms_key":"projects/p/locations/global/keyRings/r/cryptoKeys/k",
        "google_oauth_enabled":True,
        "google_oauth_production_approved":False,
    })
    database=StubDatabase()
    with TestClient(create_app(value,StubReporter(),database)) as client:
        status=client.get("/api/oauth/google/status",headers=headers()).json()
        assert status["configured"] is True and status["enabled"] is True and status["productionApproved"] is False
        response=client.post("/api/oauth/google/authorize",headers=headers())
        assert response.status_code == 200
        authorization=response.json()
        assert authorization["authorizationUrl"].startswith("https://accounts.google.com/")
        assert authorization["scope"] == "https://www.googleapis.com/auth/analytics.readonly"
        assert database.oauth_states[0]["redirectUri"] == "https://service.example/oauth/google/callback"


def test_public_oauth_callback_service_exposes_only_callback_and_health():
    value=Settings(**{**settings().__dict__,"oauth_callback_only":True})
    with TestClient(create_app(value,StubReporter(),StubDatabase())) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/oauth/google/ready").status_code == 200
        assert client.get("/oauth/google/callback").status_code == 400
        assert client.get("/").status_code == 404
        assert client.get("/docs").status_code == 404
        assert client.get("/ready").status_code == 404
        assert client.get("/agency",headers=headers()).status_code == 404
        assert client.get("/api/oauth/google/status",headers=headers()).status_code == 404


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


class ReportingResult:
    def __init__(self,one=None,many=None): self.one=one; self.many=many
    def fetchone(self): return self.one
    def fetchall(self): return self.many or []


class ReportingConnection:
    def __init__(self,outcomes_available=False,ads_available=False):
        self.outcomes_available=outcomes_available
        self.ads_available=ads_available
        self.calls=[]
        self.outcomes=[]
        self.revenue=[]
        self.website_id="website_house_of_dental"
        self.start_date=date(2026,8,1)
        self.end_date=date(2026,8,31)
        self.business_ads={"data_available":ads_available,"row_count":0,"cost_micros":None,"clicks":None,"currency_code":None}
        self.google_ads={"data_available":ads_available,"row_count":0,"cost_micros":None,"clicks":None,"impressions":None,"currency_code":None,"last_execution_at":datetime(2026,8,31,tzinfo=timezone.utc) if ads_available else None}

    def execute(self,query,params=()):
        normalized=" ".join(query.split())
        self.calls.append(normalized)
        coverage_params=(self.website_id,self.start_date,self.end_date)
        period_params=coverage_params+(self.start_date,self.end_date)
        if normalized.startswith("SELECT DISTINCT c.source_type"):
            assert tuple(params)==coverage_params
            sources=[{"source_type":"call_tracking"},{"source_type":"crm_booking"}] if self.outcomes_available else []
            return ReportingResult(many=sources)
        if "FROM analytics.first_party_outcomes" in normalized:
            assert tuple(params)==period_params
            return ReportingResult(many=self.revenue if "outcome_type='revenue'" in normalized else self.outcomes)
        if "campaign_id" in normalized:
            assert tuple(params)==period_params
            return ReportingResult(many=[])
        if "sum(a.impressions)" in normalized:
            assert tuple(params)==period_params
            return ReportingResult(one=self.google_ads)
        if "SELECT EXISTS(SELECT 1 FROM complete_execution)" in normalized:
            assert tuple(params)==period_params
            return ReportingResult(one=self.business_ads)
        raise AssertionError(f"unexpected reporting query: {normalized}")


def reporting_database(connection):
    database=Database.__new__(Database)
    @contextmanager
    def tenant_connection(_context):
        yield connection
    database.tenant_connection=tenant_connection
    return database


def test_business_and_paid_reporting_distinguish_unavailable_from_complete_zero():
    context=TenantContext("org-1","user-1","operator@example.com","agency_owner")
    unavailable_connection=ReportingConnection()
    unavailable_database=reporting_database(unavailable_connection)
    unavailable=unavailable_database.business_outcomes(context,"website_house_of_dental",date(2026,8,1),date(2026,8,31))
    assert unavailable["outcomeDataStatus"]=="unavailable" and unavailable["paidDataStatus"]=="unavailable"
    assert unavailable["outcomes"]["qualified_lead"] is None and unavailable["outcomes"]["booked_appointment"] is None
    assert unavailable["outcomes"]["customer"] is None and unavailable["outcomes"]["revenue"] is None
    assert unavailable["cost"] is None and unavailable["clicks"] is None and unavailable["revenueMinorUnits"] is None
    assert unavailable["revenueByChannel"] is None and any("unavailable" in caveat for caveat in unavailable["caveats"])
    paid_unavailable=unavailable_database.google_ads_performance(context,"website_house_of_dental",date(2026,8,1),date(2026,8,31))
    assert paid_unavailable["dataStatus"]=="unavailable" and paid_unavailable["totals"] is None

    zero_connection=ReportingConnection(outcomes_available=True,ads_available=True)
    zero_database=reporting_database(zero_connection)
    zero=zero_database.business_outcomes(context,"website_house_of_dental",date(2026,8,1),date(2026,8,31))
    assert zero["outcomeDataStatus"]=="available" and zero["paidDataStatus"]=="available"
    assert zero["outcomes"]["qualified_lead"]==0 and zero["outcomes"]["booked_appointment"]==0
    assert zero["outcomes"]["customer"]==0 and zero["outcomes"]["revenue"]==0
    assert zero["cost"]==0 and zero["clicks"]==0 and zero["revenueMinorUnits"]==0
    assert zero["revenueByChannel"]==[] and any("zero" in caveat for caveat in zero["caveats"])
    paid_zero=zero_database.google_ads_performance(context,"website_house_of_dental",date(2026,8,1),date(2026,8,31))
    assert paid_zero["dataStatus"]=="available" and paid_zero["totals"]=={"costMicros":0,"clicks":0,"impressions":0,"currency":None}
    assert paid_zero["rows"]==[] and paid_zero["lastSyncAt"]=="2026-08-31T00:00:00+00:00"
