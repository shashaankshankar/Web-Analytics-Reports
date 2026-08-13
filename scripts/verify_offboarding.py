from __future__ import annotations

import os
import uuid

import psycopg
from psycopg.rows import dict_row

from app.config import Settings
from app.storage import Database


def main() -> None:
    database_url=os.environ.get("DATABASE_URL","")
    if not database_url: raise SystemExit("DATABASE_URL is required")
    ids={key:uuid.uuid4() for key in ("organization","company","website","user","request")}
    public_id=f"deletion_proof_{ids['website'].hex[:12]}"
    with psycopg.connect(database_url,row_factory=dict_row) as connection:
        connection.execute("INSERT INTO app.organizations(id,name) VALUES(%s,'Deletion Proof Organization')",(ids["organization"],))
        connection.execute("INSERT INTO app.companies(id,organization_id,name) VALUES(%s,%s,'Deletion Proof Company')",(ids["company"],ids["organization"]))
        connection.execute("INSERT INTO app.websites(id,company_id,canonical_domain,healthcare_eligibility) VALUES(%s,%s,%s,'prohibited')",(ids["website"],ids["company"],f"{ids['website'].hex}.invalid"))
        connection.execute("INSERT INTO app.users(id,email) VALUES(%s,%s)",(ids["user"],f"deletion-{ids['user'].hex}@example.invalid"))
        connection.execute("INSERT INTO app.memberships(organization_id,user_id,role) VALUES(%s,%s,'agency_owner')",(ids["organization"],ids["user"]))
        connection.execute("INSERT INTO app.resource_identifiers(organization_id,resource_type,resource_id,public_id) VALUES(%s,'company',%s,%s),(%s,'website',%s,%s)",(ids["organization"],ids["company"],public_id+"_company",ids["organization"],ids["website"],public_id))
        connection.execute("INSERT INTO app.data_retention_policies(organization_id) VALUES(%s)",(ids["organization"],))
        connection.execute("INSERT INTO app.deletion_requests(id,organization_id,website_id,requested_by,status,execute_after,confirmation_public_id) VALUES(%s,%s,%s,%s,'scheduled',now()-interval '1 minute',%s)",(ids["request"],ids["organization"],ids["website"],ids["user"],public_id))

    settings=Settings("demo",False,False,"","","","127.0.0.1",3000,database_url=database_url)
    database=Database(settings)
    try: results=database.execute_due_deletions(5)
    finally: database.close()
    match=next((item for item in results if item["deletionRequestId"]==str(ids["request"])),None)
    if not match: raise RuntimeError("deletion_request_not_executed")

    with psycopg.connect(database_url,row_factory=dict_row) as connection:
        website_count=connection.execute("SELECT count(*) count FROM app.websites WHERE id=%s",(ids["website"],)).fetchone()["count"]
        request=connection.execute("SELECT status,website_id,result_json FROM app.deletion_requests WHERE id=%s",(ids["request"],)).fetchone()
        assert website_count==0 and request["status"]=="completed" and request["website_id"] is None
        connection.execute("DELETE FROM audit.events WHERE organization_id=%s",(ids["organization"],))
        connection.execute("DELETE FROM app.deletion_requests WHERE organization_id=%s",(ids["organization"],))
        connection.execute("DELETE FROM app.data_retention_policies WHERE organization_id=%s",(ids["organization"],))
        connection.execute("DELETE FROM app.memberships WHERE organization_id=%s",(ids["organization"],))
        connection.execute("DELETE FROM app.users WHERE id=%s",(ids["user"],))
        connection.execute("DELETE FROM app.organizations WHERE id=%s",(ids["organization"],))
    print({"status":"passed","gracePeriod":"represented_by_due_fixture","websiteDeleted":True,"requestTombstoneRecorded":True,"temporaryDataPersisted":False})


if __name__=="__main__": main()
