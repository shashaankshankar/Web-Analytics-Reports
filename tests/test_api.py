from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class StubReporter:
    def report(self,*_): return {"rows":[],"row_count":0,"property_timezone":"America/New_York","data_loss_from_other_row":False}


class StubDatabase:
    configured=True
    def close(self): pass
    def health(self): return {"status":"ok","database":"measurement","migrated":True}
    def sync_status(self): return {"status":"warning","lastSuccessfulSync":"2026-08-12T12:00:00+00:00","queuedJobs":0,"failedJobs":0,"freshness":"provisional","quality":{"empty":True}}
    def latest_snapshot(self,view,period):
        values={
          "overview":{"websiteId":"website_house_of_dental","period":period,"metrics":[{"metric":"generated_leads","value":0,"previousValue":0},{"metric":"cta_engagement","value":2,"previousValue":1}]},
          "acquisition":{"websiteId":"website_house_of_dental","period":period,"rows":[]},
          "conversion":{"websiteId":"website_house_of_dental","period":period,"funnel":[]},
          "landing-pages":{"websiteId":"website_house_of_dental","period":period,"rows":[]},
          "events":{"websiteId":"website_house_of_dental","period":period,"expectedEvents":[]},
        }
        return values.get(view)


def settings(): return Settings("live",True,True,"549721844","15427015396","x"*32,"127.0.0.1",3000,database_url="postgresql://configured")
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
