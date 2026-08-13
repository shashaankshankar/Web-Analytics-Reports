from __future__ import annotations

import os
import uuid
from datetime import date

import psycopg
from psycopg.rows import dict_row

from app.config import Settings
from app.external_sync import ExternalSyncEngine
from app.storage import Database


class ProofConnector:
    source_type="google_ads"
    def validate_access(self): return {"status":"ok","fixture":"synthetic"}
    def sync(self,start_date,end_date):
        return [{"date":start_date.isoformat(),"campaignId":"proof-campaign","campaignName":"Synthetic proof","adGroupId":"proof-ad-group","costMicros":1250000,"clicks":3,"impressions":30,"currency":"USD"}]
    def disable(self): pass


def main() -> None:
    database_url=os.environ.get("DATABASE_URL","")
    if not database_url: raise SystemExit("DATABASE_URL is required")
    ids={key:uuid.uuid4() for key in ("organization","company","website","user","connection","request")}
    public_id=f"external_sync_proof_{ids['website'].hex[:12]}"
    with psycopg.connect(database_url,row_factory=dict_row) as connection:
        connection.execute("INSERT INTO app.organizations(id,name) VALUES(%s,'External Sync Proof Organization')",(ids["organization"],))
        connection.execute("INSERT INTO app.companies(id,organization_id,name) VALUES(%s,%s,'External Sync Proof Company')",(ids["company"],ids["organization"]))
        connection.execute("INSERT INTO app.websites(id,company_id,canonical_domain,healthcare_eligibility) VALUES(%s,%s,%s,'prohibited')",(ids["website"],ids["company"],f"{ids['website'].hex}.invalid"))
        connection.execute("INSERT INTO app.users(id,email) VALUES(%s,%s)",(ids["user"],f"external-{ids['user'].hex}@example.invalid"))
        connection.execute("INSERT INTO app.memberships(organization_id,user_id,role) VALUES(%s,%s,'agency_owner')",(ids["organization"],ids["user"]))
        connection.execute("INSERT INTO app.resource_identifiers(organization_id,resource_type,resource_id,public_id) VALUES(%s,'company',%s,%s),(%s,'website',%s,%s)",(ids["organization"],ids["company"],public_id+"_company",ids["organization"],ids["website"],public_id))
        connection.execute("INSERT INTO app.data_retention_policies(organization_id) VALUES(%s)",(ids["organization"],))
        connection.execute("INSERT INTO app.source_connections(id,organization_id,website_id,source_type,credential_secret_reference,external_account_id,approval_status) VALUES(%s,%s,%s,'google_ads','synthetic-proof','proof-account','approved')",(ids["connection"],ids["organization"],ids["website"]))

    database=Database(Settings("demo",False,False,"","","","127.0.0.1",3000,database_url=database_url))
    engine=ExternalSyncEngine(database,str(ids["connection"]),ProofConnector())
    first=engine.run(date(2026,8,1),date(2026,8,1)); second=engine.run(date(2026,8,1),date(2026,8,1))
    assert first["status"]=="succeeded" and first["rowCount"]==1 and second["idempotentReplay"] is True
    with psycopg.connect(database_url,row_factory=dict_row) as connection:
        fact=connection.execute("SELECT cost_micros,clicks,impressions FROM analytics.google_ads_daily WHERE source_connection_id=%s",(ids["connection"],)).fetchone()
        assert fact and fact["cost_micros"]==1250000 and fact["clicks"]==3 and fact["impressions"]==30
        connection.execute("INSERT INTO app.deletion_requests(id,organization_id,website_id,requested_by,status,execute_after,confirmation_public_id) VALUES(%s,%s,%s,%s,'scheduled',now()-interval '1 minute',%s)",(ids["request"],ids["organization"],ids["website"],ids["user"],public_id))
    deleted=database.execute_due_deletions(5)
    assert any(item["deletionRequestId"]==str(ids["request"]) for item in deleted)
    with psycopg.connect(database_url,row_factory=dict_row) as connection:
        assert connection.execute("SELECT count(*) count FROM analytics.google_ads_daily WHERE source_connection_id=%s",(ids["connection"],)).fetchone()["count"]==0
        connection.execute("DELETE FROM audit.events WHERE organization_id=%s",(ids["organization"],)); connection.execute("DELETE FROM app.deletion_requests WHERE organization_id=%s",(ids["organization"],)); connection.execute("DELETE FROM app.data_retention_policies WHERE organization_id=%s",(ids["organization"],)); connection.execute("DELETE FROM app.memberships WHERE organization_id=%s",(ids["organization"],)); connection.execute("DELETE FROM app.users WHERE id=%s",(ids["user"],)); connection.execute("DELETE FROM app.organizations WHERE id=%s",(ids["organization"],))
    print({"status":"passed","normalizedPersistence":True,"reconciliation":first["reconciliation"],"idempotentReplay":True,"offboardingDeletion":True,"temporaryDataPersisted":False})
    if database._pool is not None: database._pool.close(timeout=1)


if __name__=="__main__": main()
