from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.analytics.contracts import SourceAvailability
from app.cli import _persist_dispatch_provenance
from app.config import ClientConfig, Settings
from app.delivery.report_store import SentReportStore
from app.delivery.sender import ResendEmailSender
from app.sources.resend_email_metrics import (
    MAX_EMAIL_IDS_PER_REQUEST,
    RESEND_COUNT_METRICS,
    ResendEmailMetricsSource,
    ResendMetricsRequestError,
)
from app.sources.website_inquiries import WebsiteInquiryMetricsSource


WINDOW_START = "2026-08-01"
WINDOW_END = "2026-08-28"
REPORT_TIMEZONE = "America/New_York"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _totals(**overrides: int) -> dict:
    values = {name: 0 for name in RESEND_COUNT_METRICS}
    values.update(overrides)
    return {"object": "metrics", "totals": values}


def _record(
    store: SentReportStore,
    email_id: str,
    *,
    client_id: str = "client-a",
    report_type: str = "performance",
    sent_at: datetime = NOW,
    start: str = WINDOW_START,
    end: str = WINDOW_END,
    timezone_name: str = REPORT_TIMEZONE,
) -> None:
    store.record_sent_report(
        resend_email_id=email_id,
        client_id=client_id,
        report_type=report_type,
        reporting_window_start=start,
        reporting_window_end=end,
        timezone_name=timezone_name,
        sent_at=sent_at,
        technical_metadata={
            "delivery_kind": "client_report",
            "has_attachment": True,
            "provider_status": "sent",
        },
    )


def test_store_persists_safe_provenance_and_keeps_client_windows_isolated(tmp_path):
    path = tmp_path / "delivery.sqlite3"
    store = SentReportStore(path)
    _record(store, "email-a", client_id="client-a")
    _record(store, "email-b", client_id="client-b")

    selected = store.find_sent_reports(
        client_id="client-a",
        report_type="performance",
        reporting_window_start=WINDOW_START,
        reporting_window_end=WINDOW_END,
        timezone_name=REPORT_TIMEZONE,
    )
    assert selected.email_ids == ["email-a"]
    assert store.list_report_email_ids(
        client_id="client-a",
        report_type="performance",
        reporting_window_start="2026-08-02",
        reporting_window_end=WINDOW_END,
        timezone_name=REPORT_TIMEZONE,
    ) == []

    database_text = path.read_bytes()
    assert b"client@example.com" not in database_text
    assert b"RESEND_API_KEY" not in database_text

    with pytest.raises(ValueError, match="already bound"):
        _record(store, "email-a", client_id="client-b")


def test_store_is_idempotent_for_same_provider_id_and_provenance(tmp_path):
    store = SentReportStore(tmp_path / "delivery.sqlite3")
    _record(store, "email-a", sent_at=NOW)
    _record(store, "email-a", sent_at=NOW + timedelta(minutes=2))
    records = store.find_sent_reports(
        client_id="client-a",
        report_type="performance",
        reporting_window_start=WINDOW_START,
        reporting_window_end=WINDOW_END,
        timezone_name=REPORT_TIMEZONE,
    ).records
    assert len(records) == 1
    assert records[0].sent_at == "2026-09-03T12:00:00Z"


def test_cli_persists_only_safe_dispatch_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("REPORT_DELIVERY_STORE_PATH", str(tmp_path / "delivery.sqlite3"))
    client = ClientConfig(
        client_id="client-a",
        company_name="Client A",
        domain="https://client-a.example",
        timezone=REPORT_TIMEZONE,
    )
    _persist_dispatch_provenance(
        dispatch={"status": "sent", "id": "email-a", "to": ["client@example.com"]},
        client=client,
        report_type="performance",
        observation_start_date=WINDOW_START,
        observation_end_date=WINDOW_END,
        delivery_kind="client_report",
        has_attachment=True,
    )
    record = SentReportStore(tmp_path / "delivery.sqlite3").find_sent_reports(
        client_id="client-a",
        report_type="performance",
        reporting_window_start=WINDOW_START,
        reporting_window_end=WINDOW_END,
        timezone_name=REPORT_TIMEZONE,
    ).records[0]
    assert record.resend_email_id == "email-a"
    assert record.technical_metadata == {
        "delivery_kind": "client_report",
        "has_attachment": True,
        "provider_status": "sent",
    }


def test_sender_uses_only_global_internal_resend_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "global-internal-key")
    monkeypatch.setenv("REPORT_EMAIL_API_KEY", "legacy-website-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "reports@example.com")
    sender = ResendEmailSender()
    assert sender.api_key == "global-internal-key"
    assert sender.from_email == "reports@example.com"
    assert ResendEmailSender(api_key="", from_email="reports@example.com").is_configured is False


def test_sender_rejects_provider_success_without_email_id(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setattr("app.delivery.sender.urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    sender = ResendEmailSender(api_key="internal-key-1234567890", from_email="reports@example.com")
    with pytest.raises(RuntimeError, match="without returning an email ID"):
        sender.send_briefing(
            to_recipients=["client@example.com"],
            subject="Report",
            html_content="<p>Report</p>",
        )


def test_resend_metrics_scopes_ids_and_preserves_exact_window_and_timezone(tmp_path):
    store = SentReportStore(tmp_path / "delivery.sqlite3")
    _record(store, "email-a", client_id="client-a")
    _record(store, "email-b", client_id="client-b")
    calls: list[tuple[str, str]] = []

    def requester(url: str, api_key: str):
        calls.append((url, api_key))
        return _totals(sent=1, delivered=1)

    source = ResendEmailMetricsSource(
        api_key="internal-key",
        store=store,
        requester=requester,
        clock=lambda: NOW,
    )
    result = source.fetch_metrics("client-a", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    query = parse_qs(urlparse(calls[0][0]).query)
    assert result["status"] == SourceAvailability.AVAILABLE.value
    assert query["email_id[]"] == ["email-a"]
    assert query["start_date"] == [WINDOW_START]
    assert query["end_date"] == [WINDOW_END]
    assert query["timezone"] == [REPORT_TIMEZONE]
    assert query["dimensions"] == ["email"]
    assert calls[0][1] == "internal-key"


def test_resend_metrics_batches_at_most_100_ids(tmp_path):
    store = SentReportStore(tmp_path / "delivery.sqlite3")
    for index in range(201):
        _record(store, f"email-{index:03d}")
    batches: list[list[str]] = []

    def requester(url: str, _api_key: str):
        ids = parse_qs(urlparse(url).query)["email_id[]"]
        batches.append(ids)
        return _totals(sent=len(ids), delivered=len(ids))

    result = ResendEmailMetricsSource(
        api_key="internal-key",
        store=store,
        requester=requester,
        clock=lambda: NOW,
    ).fetch_metrics("client-a", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert [len(batch) for batch in batches] == [100, 100, 1]
    assert all(len(batch) <= MAX_EMAIL_IDS_PER_REQUEST for batch in batches)
    assert result["successful_batches"] == 3
    assert result["metrics"]["sent"] == 201
    assert result["metrics"]["delivered"] == 201


def test_resend_metrics_empty_selection_does_not_call_provider(tmp_path):
    calls = []
    source = ResendEmailMetricsSource(
        api_key="internal-key",
        store=SentReportStore(tmp_path / "delivery.sqlite3"),
        requester=lambda *_args: calls.append(True),
        clock=lambda: NOW,
    )
    result = source.fetch_metrics("client-a", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert result["status"] == SourceAvailability.EMPTY.value
    assert result["metrics"] == {}
    assert calls == []


def test_resend_metrics_cache_hit_is_explicit(tmp_path):
    store = SentReportStore(tmp_path / "delivery.sqlite3")
    _record(store, "email-a")
    calls = []

    def requester(_url: str, _api_key: str):
        calls.append(True)
        return _totals(sent=1, delivered=1)

    source = ResendEmailMetricsSource(
        api_key="internal-key",
        store=store,
        requester=requester,
        clock=lambda: NOW,
        cache_ttl_seconds=900,
    )
    first = source.fetch_metrics("client-a", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    second = source.fetch_metrics("client-a", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert len(calls) == 1


def test_resend_metrics_retention_exclusion_is_unavailable_not_zero(tmp_path):
    store = SentReportStore(tmp_path / "delivery.sqlite3")
    _record(store, "expired-email", sent_at=NOW - timedelta(days=31))
    calls = []
    source = ResendEmailMetricsSource(
        api_key="internal-key",
        store=store,
        requester=lambda *_args: calls.append(True),
        clock=lambda: NOW,
        retention_days=30,
    )
    result = source.fetch_metrics("client-a", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert result["status"] == SourceAvailability.UNAVAILABLE.value
    assert result["retention_clamped"] is True
    assert result["retention_excluded_count"] == 1
    assert result["metrics"] == {}
    assert calls == []


def test_resend_metrics_partial_batches_are_not_presented_as_complete(tmp_path):
    store = SentReportStore(tmp_path / "delivery.sqlite3")
    for index in range(101):
        _record(store, f"email-{index:03d}")
    calls = []

    def requester(url: str, _api_key: str):
        calls.append(url)
        if len(calls) == 2:
            raise ResendMetricsRequestError(500)
        return _totals(sent=100, delivered=99)

    result = ResendEmailMetricsSource(
        api_key="internal-key",
        store=store,
        requester=requester,
        clock=lambda: NOW,
    ).fetch_metrics("client-a", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert result["status"] == SourceAvailability.PARTIAL.value
    assert result["successful_batches"] == 1
    assert result["failed_batches"] == 1
    assert result["metrics"]["sent"] == 100
    assert result["metrics"]["delivered"] == 99


@pytest.mark.parametrize(
    ("requester", "expected_status"),
    [
        (lambda _url, _key: (_ for _ in ()).throw(ResendMetricsRequestError(401)), SourceAvailability.UNAVAILABLE.value),
        (lambda _url, _key: (_ for _ in ()).throw(ResendMetricsRequestError(500)), SourceAvailability.ERROR.value),
        (lambda _url, _key: {"totals": {}}, SourceAvailability.ERROR.value),
    ],
)
def test_resend_metrics_provider_failures_are_explicit(tmp_path, requester, expected_status):
    store = SentReportStore(tmp_path / "delivery.sqlite3")
    _record(store, "email-a")
    result = ResendEmailMetricsSource(
        api_key="internal-key",
        store=store,
        requester=requester,
        clock=lambda: NOW,
    ).fetch_metrics("client-a", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert result["status"] == expected_status
    assert result["metrics"] == {}


def test_resend_metrics_provider_errors_are_normalized_without_sensitive_details(tmp_path):
    store = SentReportStore(tmp_path / "delivery.sqlite3")
    _record(store, "email-a")
    secret = "internal-key-that-must-not-leak"
    recipient = "client@example.com"

    def requester(_url: str, _api_key: str):
        raise RuntimeError(f"Authorization: Bearer {secret}; to={recipient}")

    result = ResendEmailMetricsSource(
        api_key=secret,
        store=store,
        requester=requester,
        clock=lambda: NOW,
    ).fetch_metrics("client-a", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)

    serialized = json.dumps(result)
    assert result["status"] == SourceAvailability.ERROR.value
    assert "unusable provider response" in result["reason"]
    assert secret not in serialized
    assert recipient not in serialized
    assert "Authorization" not in serialized


def _website_client(**metrics_config) -> ClientConfig:
    return ClientConfig(
        client_id="website-client",
        company_name="Website Client",
        domain="https://website-client.example",
        website_inquiry_metrics=metrics_config,
    )


def test_website_metrics_missing_credential_is_not_configured_and_never_falls_back(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "internal-resend-key")
    reader_calls = []
    client = _website_client(
        enabled=True,
        provider="secret_manager",
        secret_manager_ref="projects/example/secrets/website-metrics/versions/latest",
        expected_client_id="website-client",
    )
    source = WebsiteInquiryMetricsSource(
        client=client,
        credential_resolver=lambda _reference: None,
        aggregate_reader=lambda **_kwargs: reader_calls.append(True),
    )
    result = source.fetch_metrics("website-client", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert result["status"] == SourceAvailability.NOT_CONFIGURED.value
    assert reader_calls == []


@pytest.mark.parametrize(
    "resolved_credential",
    [
        "not-json",
        json.dumps({"client_id": "other-client", "token": "website-token", "authorized": True}),
        json.dumps({"client_id": "website-client", "token": "website-token", "authorized": False}),
    ],
)
def test_website_metrics_malformed_or_unauthorized_credential_is_unavailable(resolved_credential):
    reader_calls = []
    client = _website_client(
        enabled=True,
        provider="secret_manager",
        secret_manager_ref="projects/example/secrets/website-metrics/versions/latest",
        expected_client_id="website-client",
    )
    source = WebsiteInquiryMetricsSource(
        client=client,
        credential_resolver=lambda _reference: resolved_credential,
        aggregate_reader=lambda **_kwargs: reader_calls.append(True),
    )
    result = source.fetch_metrics("website-client", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert result["status"] == SourceAvailability.UNAVAILABLE.value
    assert reader_calls == []


def test_website_metrics_valid_client_credential_is_separate_from_resend(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "internal-resend-key")
    passed = []
    client = _website_client(
        enabled=True,
        provider="secret_manager",
        secret_manager_ref="projects/example/secrets/website-metrics/versions/latest",
        expected_client_id="website-client",
    )

    def reader(**kwargs):
        passed.append(kwargs)
        return {
            "status": "available",
            "client_id": "website-client",
            "start_date": WINDOW_START,
            "end_date": WINDOW_END,
            "timezone": REPORT_TIMEZONE,
            "current_inquiries": 4,
            "prior_inquiries": 2,
        }

    source = WebsiteInquiryMetricsSource(
        client=client,
        credential_resolver=lambda _reference: {
            "client_id": "website-client",
            "token": "website-only-token",
            "authorized": True,
        },
        aggregate_reader=reader,
    )
    result = source.fetch_metrics("website-client", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert result["status"] == SourceAvailability.AVAILABLE.value
    assert result["current_inquiries"] == 4
    assert passed[0]["credential"].token == "website-only-token"
    assert passed[0]["credential"].client_id == "website-client"
    assert passed[0]["credential"].token != "internal-resend-key"


def test_website_metrics_cross_client_selection_is_rejected_without_reader_call():
    calls = []
    client = _website_client(
        enabled=True,
        provider="website_aggregates",
        aggregate_source="website_delivery_aggregate",
    )
    source = WebsiteInquiryMetricsSource(
        client=client,
        aggregate_reader=lambda **_kwargs: calls.append(True),
    )
    result = source.fetch_metrics("other-client", WINDOW_START, WINDOW_END, REPORT_TIMEZONE)
    assert result["status"] == SourceAvailability.UNAVAILABLE.value
    assert calls == []


def test_settings_keep_website_credentials_out_of_internal_resend_settings(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "internal-resend-key")
    monkeypatch.setenv("WEBSITE_METRICS_API_KEY", "website-key")
    settings = Settings.from_env()
    assert settings.resend_api_key == "internal-resend-key"
    assert not hasattr(settings, "website_metrics_api_key")
