from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.auth import TenantContext
from app.config import Settings, load_site
from app.ga4 import GA4AccessVerifier, GA4Reporter
from app.main import create_app


class ProbeResponse:
    row_count = 1


class ProbeClient:
    def __init__(self):
        self.calls = []

    def run_report(self, **kwargs):
        self.calls.append(kwargs)
        return ProbeResponse()


def test_ga4_access_probe_is_single_small_read_only_request():
    site = load_site()
    client = ProbeClient()
    reporter = GA4Reporter(site, client)
    reporter.today = lambda: date(2026, 8, 14)

    evidence = reporter.verify_access()

    request = client.calls[0]["request"]
    assert len(client.calls) == 1
    assert client.calls[0]["retry"] is None and client.calls[0]["timeout"] == 5.0
    assert request.property == f"properties/{site.property_id}"
    assert request.limit == 1 and not request.dimensions
    assert [metric.name for metric in request.metrics] == ["activeUsers"]
    assert evidence["dateRange"] == {"start": "2026-08-13", "end": "2026-08-13"}


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 14, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


class AccessReporter:
    def __init__(self, failure=None):
        self.calls = 0
        self.failure = failure

    def verify_access(self, timeout):
        self.calls += 1
        if self.failure:
            raise self.failure
        return {"property": "properties/549721844", "rowCount": 0}


def test_access_evidence_is_cached_and_can_become_stale_without_refresh():
    clock = Clock()
    reporter = AccessReporter()
    verifier = GA4AccessVerifier(reporter, ttl_seconds=60, clock=clock)

    verified = verifier.check()
    cached = verifier.check()
    clock.value += timedelta(seconds=61)
    stale = verifier.check(refresh=False)

    assert verified["state"] == "verified" and verified["cache"]["hit"] is False
    assert cached["state"] == "verified" and cached["cache"]["hit"] is True
    assert stale["state"] == "stale" and stale["lastVerifiedAt"] == verified["lastVerifiedAt"]
    assert reporter.calls == 1


def test_access_probe_errors_are_bounded_redacted_and_backed_off():
    clock = Clock()
    reporter = AccessReporter(RuntimeError("secret provider payload"))
    verifier = GA4AccessVerifier(reporter, error_retry_seconds=30, clock=clock)

    first = verifier.check()
    retry_blocked = verifier.check()
    clock.value += timedelta(seconds=31)
    second_attempt = verifier.check()

    assert first["state"] == "error" and first["errorCode"] == "verification_failed"
    assert "secret provider payload" not in str(first)
    assert retry_blocked["cache"]["hit"] is True
    assert second_attempt["state"] == "error" and reporter.calls == 2


class HealthDatabase:
    configured = True

    def close(self):
        pass

    def authorize_context(self, email, organization_id=None):
        return TenantContext("org-1", "user-1", email, "agency_owner")

    def website_authorized(self, context, website_id):
        return context.organization_id == "org-1" and website_id == "website_house_of_dental"

    def sync_status(self, context=None, website_id=None):
        return {"status": "ok", "lastSuccessfulSync": "2026-08-13T12:00:00+00:00", "quality": {}}

    def latest_measurement_health(self, context, website_id):
        return None

    def latest_snapshot(self, context, website_id, view, period):
        return {"websiteId": website_id, "period": period, "expectedEvents": []}


def api_settings(live=True):
    site = load_site()
    return Settings(
        "live" if live else "demo",
        live,
        live,
        site.property_id,
        site.stream_id,
        "x" * 32,
        "127.0.0.1",
        3000,
        database_url="postgresql://configured",
        operator_email="operator@example.com",
    )


def test_measurement_health_separates_configured_assignment_from_cached_access():
    reporter = AccessReporter()
    with TestClient(create_app(api_settings(), reporter, HealthDatabase())) as client:
        first = client.get("/api/websites/website_house_of_dental/measurement-health", headers={"Authorization": "Bearer " + "x" * 32})
        second = client.get("/api/websites/website_house_of_dental/measurement-health", headers={"Authorization": "Bearer " + "x" * 32})

    first_body = first.json()
    second_body = second.json()
    assert first.status_code == second.status_code == 200
    assert first_body["assignment"]["state"] == "configured"
    assert first_body["ga4AccessHealth"]["state"] == "verified"
    assert second_body["ga4AccessHealth"]["cache"]["hit"] is True
    assert first_body["checks"][0]["accessState"] == "verified"
    assert reporter.calls == 1


def test_measurement_health_is_unverified_when_live_access_is_disabled():
    reporter = AccessReporter()
    with TestClient(create_app(api_settings(live=False), reporter, HealthDatabase())) as client:
        response = client.get("/api/websites/website_house_of_dental/measurement-health", headers={"Authorization": "Bearer " + "x" * 32})

    body = response.json()
    assert response.status_code == 200
    assert body["assignment"]["state"] == "configured"
    assert body["ga4AccessHealth"]["state"] == "unverified"
    assert body["ga4AccessHealth"]["reason"] == "live_reporting_disabled"
    assert reporter.calls == 0
