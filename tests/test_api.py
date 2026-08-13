from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
class Stub:
    def report(self,*_): return {"rows":[{"dimensions":[],"metrics":["2","3","5"]}],"row_count":1,"property_timezone":"America/New_York","data_loss_from_other_row":False}
    def overview(self,period): return {"dataStatus":"live","period":period,"metrics":[{"metric":"generated_leads","value":0},{"metric":"cta_engagement","value":2},{"metric":"active_users","value":2},{"metric":"sessions","value":3}]}
    def acquisition(self,period): return {"dataStatus":"live","period":period,"rows":[{"channel":"Organic Search","sessions":3,"activeUsers":2}]}
def settings(): return Settings("live",True,True,"549721844","15408312790","x"*32,"127.0.0.1",3000)
def headers(): return {"Authorization":"Bearer "+"x"*32}
def test_docs_are_public_and_data_is_protected():
    with TestClient(create_app(settings(),Stub())) as client:
        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/api/portfolio/summary").status_code == 401
        assert client.get("/api/portfolio/summary",headers=headers()).status_code == 200
def test_live_contract_and_scope_are_preserved():
    with TestClient(create_app(settings(),Stub())) as client:
        response=client.get("/api/companies/company_house_of_dental/overview?period=28d",headers=headers()); assert response.status_code == 200
        values={item["metric"]:item["value"] for item in response.json()["metrics"]}; assert values["generated_leads"] == 0 and values["cta_engagement"] == 2
        assert client.get("/api/websites/not-this-site/sync-status",headers=headers()).status_code == 403
        assert client.get("/api/websites/website_house_of_dental/measurement-health",headers=headers()).json()["state"] == "ready"
