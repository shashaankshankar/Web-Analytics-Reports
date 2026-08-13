from __future__ import annotations

import os
import uuid

import psycopg

from app.config import Settings, Site
from app.credentials import AdcCredential
from app.ga4 import GA4Reporter
from app.storage import Database
from app.sync import SyncEngine


def main() -> None:
    database_url=os.environ.get("DATABASE_URL","")
    if not database_url: raise SystemExit("DATABASE_URL is required")
    ids={key:uuid.uuid4() for key in ("organization","company","website","user","connection","property","stream","assignment")}
    public_id=f"failure_isolation_{ids['website'].hex[:12]}"
    with psycopg.connect(database_url) as connection:
        report_version=connection.execute("SELECT value_uuid FROM app.platform_identifiers WHERE key='report_bundle_v1'").fetchone()[0]
        primary_assignment=connection.execute("SELECT value_uuid FROM app.platform_identifiers WHERE key='first_assignment'").fetchone()[0]
        primary_before=connection.execute("SELECT max(completed_at) FROM analytics.sync_jobs WHERE assignment_id=%s AND status='succeeded'",(primary_assignment,)).fetchone()[0]
        connection.execute("INSERT INTO app.organizations(id,name) VALUES(%s,'Property Failure Isolation Organization')",(ids["organization"],))
        connection.execute("INSERT INTO app.companies(id,organization_id,name) VALUES(%s,%s,'Property Failure Isolation Company')",(ids["company"],ids["organization"]))
        connection.execute("INSERT INTO app.websites(id,company_id,canonical_domain,healthcare_eligibility) VALUES(%s,%s,%s,'prohibited')",(ids["website"],ids["company"],f"{ids['website'].hex}.invalid"))
        connection.execute("INSERT INTO app.users(id,email) VALUES(%s,%s)",(ids["user"],f"failure-{ids['user'].hex}@example.invalid"))
        connection.execute("INSERT INTO app.memberships(organization_id,user_id,role) VALUES(%s,%s,'agency_owner')",(ids["organization"],ids["user"]))
        connection.execute("INSERT INTO app.resource_identifiers(organization_id,resource_type,resource_id,public_id) VALUES(%s,'company',%s,%s),(%s,'website',%s,%s)",(ids["organization"],ids["company"],public_id+"_company",ids["organization"],ids["website"],public_id))
        connection.execute("INSERT INTO app.analytics_connections(id,organization_id,credential_type,credential_reference,status) VALUES(%s,%s,'service_account','application_default_credentials','approved')",(ids["connection"],ids["organization"]))
        connection.execute("INSERT INTO app.ga_properties(id,analytics_connection_id,external_property_id,display_name,timezone) VALUES(%s,%s,'0','Synthetic inaccessible property','America/New_York')",(ids["property"],ids["connection"]))
        connection.execute("INSERT INTO app.ga_data_streams(id,ga_property_id,external_stream_id,display_name,stream_type) VALUES(%s,%s,'0','Synthetic inaccessible stream','WEB_DATA_STREAM')",(ids["stream"],ids["property"]))
        connection.execute("INSERT INTO app.website_analytics_assignments(id,website_id,analytics_connection_id,ga_property_id,ga_stream_id,reporting_scope,effective_from,status) VALUES(%s,%s,%s,%s,%s,'{\"propertyId\":\"0\",\"streamId\":\"0\"}',current_date,'approved')",(ids["assignment"],ids["website"],ids["connection"],ids["property"],ids["stream"]))

    settings=Settings("demo",False,False,"","","","127.0.0.1",3000,database_url=database_url)
    database=Database(settings); credential=AdcCredential()
    site=Site(public_id,public_id+"_company","Synthetic inaccessible property",f"https://{ids['website'].hex}.invalid","test","America/New_York","0","0","","America/New_York","disabled","approved")
    failed=False
    try:
        SyncEngine(database,GA4Reporter(site,credential.get_authorized_client()),None,site).run("28d","2026-08-13T04:40:00Z",True,ids["assignment"])
    except Exception:
        failed=True
    assert failed
    with psycopg.connect(database_url) as connection:
        secondary=connection.execute("SELECT status,dead_lettered_at IS NOT NULL FROM analytics.sync_jobs WHERE assignment_id=%s",(ids["assignment"],)).fetchone()
        primary_after=connection.execute("SELECT max(completed_at),count(*) FILTER(WHERE status='failed') FROM analytics.sync_jobs WHERE assignment_id=%s",(primary_assignment,)).fetchone()
        assert secondary[0]=='failed' and secondary[1]
        assert primary_after[0]==primary_before and primary_after[1]==0
        connection.execute("DELETE FROM analytics.operator_alerts WHERE assignment_id=%s",(ids["assignment"],)); connection.execute("DELETE FROM analytics.sync_jobs WHERE assignment_id=%s",(ids["assignment"],)); connection.execute("DELETE FROM analytics.sync_runs WHERE assignment_id=%s",(ids["assignment"],)); connection.execute("DELETE FROM app.website_analytics_assignments WHERE id=%s",(ids["assignment"],)); connection.execute("DELETE FROM app.ga_data_streams WHERE id=%s",(ids["stream"],)); connection.execute("DELETE FROM app.ga_properties WHERE id=%s",(ids["property"],)); connection.execute("DELETE FROM app.analytics_connections WHERE id=%s",(ids["connection"],)); connection.execute("DELETE FROM app.resource_identifiers WHERE organization_id=%s",(ids["organization"],)); connection.execute("DELETE FROM app.websites WHERE id=%s",(ids["website"],)); connection.execute("DELETE FROM app.companies WHERE id=%s",(ids["company"],)); connection.execute("DELETE FROM app.memberships WHERE organization_id=%s",(ids["organization"],)); connection.execute("DELETE FROM app.users WHERE id=%s",(ids["user"],)); connection.execute("DELETE FROM app.organizations WHERE id=%s",(ids["organization"],))
    print({"status":"passed","failedProperty":"dead_lettered","primaryProperty":"unchanged_and_healthy","temporaryDataPersisted":False})
    if database._pool is not None: database._pool.close(timeout=1)


if __name__=="__main__": main()
