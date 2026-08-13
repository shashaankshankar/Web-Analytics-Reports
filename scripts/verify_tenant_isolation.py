from __future__ import annotations

import os
import uuid

import psycopg
from psycopg.errors import InsufficientPrivilege


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    temporary = {
        "organization": uuid.uuid4(),
        "company": uuid.uuid4(),
        "website": uuid.uuid4(),
        "user": uuid.uuid4(),
    }
    connection = psycopg.connect(database_url)
    try:
        primary = connection.execute("SELECT value_uuid FROM app.platform_identifiers WHERE key='first_website'").fetchone()
        if not primary:
            raise RuntimeError("first_website_not_seeded")
        primary_organization = connection.execute("SELECT c.organization_id FROM app.websites w JOIN app.companies c ON c.id=w.company_id WHERE w.id=%s", (primary[0],)).fetchone()[0]
        primary_user = connection.execute("SELECT user_id FROM app.memberships WHERE organization_id=%s ORDER BY user_id LIMIT 1", (primary_organization,)).fetchone()[0]
        primary_report = connection.execute("""
            INSERT INTO app.recurring_reports(website_id,name,period_key,cadence,timezone,recipient_secret_reference,enabled,next_run_at,created_by)
            VALUES(%s,'Isolation proof report','28d','monthly','America/New_York','proof',false,now(),%s) RETURNING id
        """, (primary[0],primary_user)).fetchone()[0]
        connection.execute("INSERT INTO app.organizations(id,name) VALUES(%s,'Isolation Test Organization')", (temporary["organization"],))
        connection.execute("INSERT INTO app.companies(id,organization_id,name) VALUES(%s,%s,'Isolation Test Company')", (temporary["company"],temporary["organization"]))
        connection.execute("INSERT INTO app.websites(id,company_id,canonical_domain,healthcare_eligibility) VALUES(%s,%s,'isolation.invalid','prohibited')", (temporary["website"],temporary["company"]))
        connection.execute("INSERT INTO app.users(id,email) VALUES(%s,'isolation-test@example.invalid')", (temporary["user"],))
        connection.execute("INSERT INTO app.memberships(organization_id,user_id,role) VALUES(%s,%s,'client_viewer')", (temporary["organization"],temporary["user"]))
        connection.execute("INSERT INTO app.resource_identifiers(organization_id,resource_type,resource_id,public_id) VALUES(%s,'company',%s,'isolation_company'),(%s,'website',%s,'isolation_website')", (temporary["organization"],temporary["company"],temporary["organization"],temporary["website"]))

        connection.execute("SET LOCAL ROLE measurement_tenant")
        connection.execute("SELECT set_config('app.organization_id',%s,true)", (str(primary_organization),))
        primary_companies = connection.execute("SELECT count(*) FROM app.companies").fetchone()[0]
        leaked_secondary = connection.execute("SELECT count(*) FROM app.websites WHERE id=%s", (temporary["website"],)).fetchone()[0]
        denied_write = False
        try:
            with connection.transaction():
                connection.execute("INSERT INTO app.annotations(website_id,annotation_date,annotation_type,note) VALUES(%s,current_date,'major_outage','must be denied')", (temporary["website"],))
        except InsufficientPrivilege:
            denied_write = True
        denied_report_write = False
        try:
            with connection.transaction():
                connection.execute("INSERT INTO app.recurring_reports(website_id,name,period_key,cadence,timezone,recipient_secret_reference,enabled,next_run_at) VALUES(%s,'must fail','28d','monthly','UTC','none',false,now())", (temporary["website"],))
        except InsufficientPrivilege:
            denied_report_write = True
        connection.execute("RESET ROLE")

        connection.execute("SET LOCAL ROLE measurement_tenant")
        connection.execute("SELECT set_config('app.organization_id',%s,true)", (str(temporary["organization"]),))
        secondary_companies = connection.execute("SELECT count(*) FROM app.companies").fetchone()[0]
        leaked_primary = connection.execute("SELECT count(*) FROM app.websites WHERE id=%s", (primary[0],)).fetchone()[0]
        leaked_primary_report = connection.execute("SELECT count(*) FROM app.recurring_reports WHERE id=%s", (primary_report,)).fetchone()[0]

        assert primary_companies == 1
        assert secondary_companies == 1
        assert leaked_secondary == 0
        assert leaked_primary == 0
        assert denied_write
        assert denied_report_write
        assert leaked_primary_report == 0
        print({
            "status": "passed",
            "apiBoundary": "covered_by_pytest",
            "serviceBoundary": "covered_by_scoped_storage_tests",
            "databaseBoundary": "two_organization_rls_passed",
            "crossTenantReads": "denied",
            "crossTenantWrites": "denied",
            "phase5ControlPlaneIsolation": "denied",
            "temporaryDataPersisted": False,
        })
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
