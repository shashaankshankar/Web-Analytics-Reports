from fastapi.testclient import TestClient

from app.config import Settings
from app.auth import TenantContext
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
