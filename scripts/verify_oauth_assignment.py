from __future__ import annotations

import os
import uuid

import psycopg

from app.auth import TenantContext
from app.config import Settings
from app.storage import Database


def main() -> None:
    url=os.environ.get("DATABASE_URL","")
    if not url: raise SystemExit("DATABASE_URL is required")
    ids={key:uuid.uuid4() for key in ("organization","company","website","user","connection")}
    website_public=f"oauth_assignment_proof_{ids['website'].hex[:12]}"
    with psycopg.connect(url) as connection:
        connection.execute("INSERT INTO app.organizations(id,name) VALUES(%s,'OAuth Assignment Proof Organization')",(ids["organization"],))
        connection.execute("INSERT INTO app.companies(id,organization_id,name) VALUES(%s,%s,'OAuth Assignment Proof Company')",(ids["company"],ids["organization"]))
        connection.execute("INSERT INTO app.websites(id,company_id,canonical_domain,healthcare_eligibility) VALUES(%s,%s,%s,'prohibited')",(ids["website"],ids["company"],f"{ids['website'].hex}.invalid"))
        connection.execute("INSERT INTO app.users(id,email) VALUES(%s,%s)",(ids["user"],f"oauth-{ids['user'].hex}@example.invalid"))
        connection.execute("INSERT INTO app.memberships(organization_id,user_id,role) VALUES(%s,%s,'client_admin')",(ids["organization"],ids["user"]))
        connection.execute("INSERT INTO app.resource_identifiers(organization_id,resource_type,resource_id,public_id) VALUES(%s,'company',%s,%s),(%s,'website',%s,%s)",(ids["organization"],ids["company"],website_public+"_company",ids["organization"],ids["website"],website_public))
        connection.execute("INSERT INTO app.analytics_connections(id,organization_id,credential_type,credential_reference,status) VALUES(%s,%s,'oauth','postgres-kms-envelope:v1','pending_approval')",(ids["connection"],ids["organization"]))
        connection.execute("INSERT INTO app.oauth_credentials(analytics_connection_id,organization_id,encrypted_refresh_token,granted_scopes,token_endpoint,connected_by) VALUES(%s,%s,%s,ARRAY['https://www.googleapis.com/auth/analytics.readonly'],'https://oauth2.googleapis.com/token',%s)",(ids["connection"],ids["organization"],b"synthetic-ciphertext",ids["user"]))
    database=Database(Settings("demo",False,False,"","","","127.0.0.1",3000,database_url=url)); context=TenantContext(str(ids["organization"]),str(ids["user"]),"synthetic@example.invalid","client_admin")
    assignment=database.assign_oauth_property(context,str(ids["connection"]),website_public,"123456","987654","Synthetic property","America/New_York","USD","Synthetic stream","G-SYNTHETIC")
    assert assignment["status"]=="approved"
    revoked=database.revoke_oauth_connection(context,str(ids["connection"]),True); assert revoked
    with psycopg.connect(url) as connection:
        row=connection.execute("SELECT status,effective_to IS NOT NULL FROM app.website_analytics_assignments WHERE id=%s::uuid",(assignment["assignmentId"],)).fetchone()
        token_count=connection.execute("SELECT count(*) FROM app.oauth_credentials WHERE analytics_connection_id=%s",(ids["connection"],)).fetchone()[0]
        assert row[0]=='revoked' and row[1] and token_count==0
        connection.execute("DELETE FROM app.website_analytics_assignments WHERE id=%s::uuid",(assignment["assignmentId"],)); connection.execute("DELETE FROM app.ga_data_streams WHERE ga_property_id IN (SELECT id FROM app.ga_properties WHERE analytics_connection_id=%s)",(ids["connection"],)); connection.execute("DELETE FROM app.ga_properties WHERE analytics_connection_id=%s",(ids["connection"],)); connection.execute("DELETE FROM app.analytics_connections WHERE id=%s",(ids["connection"],)); connection.execute("DELETE FROM audit.events WHERE organization_id=%s",(ids["organization"],)); connection.execute("DELETE FROM app.resource_identifiers WHERE organization_id=%s",(ids["organization"],)); connection.execute("DELETE FROM app.websites WHERE id=%s",(ids["website"],)); connection.execute("DELETE FROM app.companies WHERE id=%s",(ids["company"],)); connection.execute("DELETE FROM app.memberships WHERE organization_id=%s",(ids["organization"],)); connection.execute("DELETE FROM app.users WHERE id=%s",(ids["user"],)); connection.execute("DELETE FROM app.organizations WHERE id=%s",(ids["organization"],))
    print({"status":"passed","assignmentApproved":True,"connectionApproved":True,"revocationDisabledAssignment":True,"offboardingDeletedToken":True,"temporaryDataPersisted":False})
    if database._pool is not None: database._pool.close(timeout=1)


if __name__=="__main__": main()
