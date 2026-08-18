import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import TenantContext
from app.config import Settings, load_site
from app.main import create_app
from app.storage import Database


class _Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _RlsConnection:
    def __init__(self, row):
        self.row = row
        self.statements = []
        self.parameters = []

    def execute(self, statement, params=None):
        self.statements.append(statement)
        self.parameters.append(params)
        if "SELECT cp.public_id company_id" in statement:
            return _Result(self.row)
        return _Result()


class _RlsDatabase(Database):
    def __init__(self, row):
        self.connection_stub = _RlsConnection(row)

    @contextmanager
    def connection(self):
        yield self.connection_stub

    @contextmanager
    def tenant_connection(self, context):
        self.connection_stub.execute("SET LOCAL ROLE measurement_tenant")
        self.connection_stub.execute(
            "SELECT set_config('app.organization_id',%s,true),set_config('app.user_id',%s,true),set_config('app.role',%s,true)",
            (context.organization_id, context.user_id, context.role),
        )
        yield self.connection_stub


def _context():
    return TenantContext("org-1", "user-1", "operator@example.com", "agency_owner")


def _site_context_row(*, stream_measurement_id="G-CANONICAL", assignment_measurement_id="G-FALLBACK"):
    return {
        "company_id": "company-1",
        "company": "Example Dental",
        "site_id": "website-1",
        "canonical_domain": "example-dental.com",
        "governance_status": "approved",
        "property_id": "123456789",
        "stream_id": "987654321",
        "property_timezone": "America/New_York",
        "stream_measurement_id": stream_measurement_id,
        "assignment_measurement_id": assignment_measurement_id,
    }


def test_website_site_context_resolves_tenant_and_ga4_contract_under_rls():
    database = _RlsDatabase(_site_context_row())

    result = database.website_site_context(_context(), "website-1")

    assert result == {**_site_context_row(), "measurement_id": "G-CANONICAL"}
    assert any("SET LOCAL ROLE measurement_tenant" in statement for statement in database.connection_stub.statements)
    assert any("set_config('app.organization_id'" in statement for statement in database.connection_stub.statements)
    query = next(statement for statement in database.connection_stub.statements if "SELECT cp.public_id company_id" in statement)
    assert "w.canonical_domain" in query
    assert "governance_status" in query
    assert "gp.external_property_id property_id" in query
    assert "gds.external_stream_id stream_id" in query
    assert "gds.measurement_id stream_measurement_id" in query
    assert "gp.timezone property_timezone" in query
    assert database.connection_stub.parameters[1] == ("org-1", "user-1", "agency_owner")


def test_stream_measurement_id_is_canonical_with_assignment_fallback():
    canonical = _RlsDatabase(_site_context_row(stream_measurement_id="G-STREAM"))
    fallback = _RlsDatabase(_site_context_row(stream_measurement_id=None, assignment_measurement_id="G-JSON"))

    assert canonical.website_site_context(_context(), "website-1")["measurement_id"] == "G-STREAM"
    assert fallback.website_site_context(_context(), "website-1")["measurement_id"] == "G-JSON"


def test_unauthorized_website_site_context_fails_closed():
    database = _RlsDatabase(None)

    with pytest.raises(PermissionError, match="website_not_authorized"):
        database.website_site_context(_context(), "other-website")


class _AccessReporter:
    def verify_access(self, timeout):
        return {"property": "properties/verified", "rowCount": 0, "dateRange": {}}


class _WebsiteDatabase:
    configured = False

    def close(self):
        pass

    def __init__(self):
        self.sites = {
            "website-1": {"site_id": "website-1", "company_id": "company-1", "company": "One", "canonical_domain": "one.example", "property_id": "111", "stream_id": "11", "measurement_id": "G-ONE", "property_timezone": "UTC", "governance_status": "approved"},
            "website-2": {"site_id": "website-2", "company_id": "company-2", "company": "Two", "canonical_domain": "two.example", "property_id": "222", "stream_id": "22", "measurement_id": "G-TWO", "property_timezone": "UTC", "governance_status": "approved"},
        }

    def authorize_context(self, email, organization_id=None):
        return _context()

    def website_authorized(self, context, website_id):
        return website_id in self.sites

    def website_site_context(self, context, website_id):
        if website_id not in self.sites:
            raise PermissionError("website_not_authorized")
        return self.sites[website_id]


def _api_settings():
    site = load_site()
    return Settings("live", True, True, site.property_id, site.stream_id, "x" * 32, "127.0.0.1", 3000, database_url="postgresql://configured", operator_email="operator@example.com")


def test_ga4_access_verifier_cache_is_isolated_per_property():
    database = _WebsiteDatabase()
    app = create_app(_api_settings(), _AccessReporter(), database)
    with TestClient(app) as client:
        first = client.get("/api/websites/website-1/measurement-health", headers={"Authorization": "Bearer " + "x" * 32})
        second = client.get("/api/websites/website-2/measurement-health", headers={"Authorization": "Bearer " + "x" * 32})
        cache = getattr(app.state, "ga4_access_verifiers", None)

    assert first.status_code == 200
    assert second.status_code == 200
    assert cache is not None
    assert set(cache.keys()) == {"111", "222"}
    assert cache["111"] is not cache["222"]


def test_unauthorized_website_api_access_returns_403():
    with TestClient(create_app(_api_settings(), _AccessReporter(), _WebsiteDatabase())) as client:
        response = client.get("/api/websites/other-website/measurement-health", headers={"Authorization": "Bearer " + "x" * 32})

    assert response.status_code == 403


def test_recurring_reports_are_disabled_by_default():
    migration = Path(__file__).parents[1] / "infra" / "postgres" / "004_phase5_reporting_oauth.sql"
    sql = migration.read_text()

    assert re.search(r"enabled\s+boolean\s+NOT NULL\s+DEFAULT\s+false", sql, re.IGNORECASE)
    recurring_definition = sql.split("CREATE TABLE IF NOT EXISTS app.recurring_reports", 1)[1].split("CREATE TABLE IF NOT EXISTS analytics.report_deliveries", 1)[0]
    assert "enabled boolean NOT NULL DEFAULT true" not in recurring_definition.lower()
