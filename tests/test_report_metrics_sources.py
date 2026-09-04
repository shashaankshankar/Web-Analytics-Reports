from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from app.analytics.contracts import ReportDeliveryMetrics, SourceAvailability
from app.cli import _persist_dispatch_provenance
from app.config import ClientConfig
from app.delivery.report_store import SentReportStore
from app.delivery.sender import ResendEmailSender
from app.sources.resend_email_metrics import (
    RESEND_COUNT_METRICS,
    ResendEmailMetricsSource,
    ResendMetricsRequestError,
)
from app.sources.website_inquiries import WebsiteInquiryMetricsSource


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
START = "2026-08-01"
END = "2026-08-28"
TZ = "America/New_York"


def _record(
    store: SentReportStore,
    email_id: str,
    *,
    client_id: str = "client-a",
    report_type: str = "performance",
    sent_at: datetime = NOW - timedelta(days=1),
) -> None:
    store.record_sent_report(
        resend_email_id=email_id,
        client_id=client_id,
        report_type=report_type,
        reporting_window_start=START,
        reporting_window_end=END,
        timezone_name=TZ,
        sent_at=sent_at,
        technical_metadata={"delivery_kind": "client_report", "has_attachment": False},
    )


def _totals(**updates: int) -> dict[str, int]:
    totals = {name: 0 for name in RESEND_COUNT_METRICS}
    totals.update(updates)
    return totals


def _source(store: SentReportStore, requester, **kwargs) -> ResendEmailMetricsSource:
    return ResendEmailMetricsSource(
        api_key="internal-report-key",
        store=store,
        requester=requester,
        clock=lambda: NOW,
        **kwargs,
    )


def test_sent_report_store_persists_safe_provenance_and_isolates_clients():
    store = SentReportStore(":memory:")
    _record(store, "re-client-a", client_id="client-a")
    _record(store, "re-client-b", client_id="client-b")

    result = store.find_sent_reports(
        client_id="client-a",
        report_type="performance",
        reporting_window_start=START,
        reporting_window_end=END,
        timezone_name=TZ,
    )

    assert result.email_ids == ["re-client-a"]
    assert result.records[0].technical_metadata == {
        "delivery_kind": "client_report",
        "has_attachment": False,
    }
    with pytest.raises(ValueError, match="unsupported field"):
        store.record_sent_report(
            resend_email_id="re-sensitive",
            client_id="client-a",
            report_type="performance",
            reporting_window_start=START,
            reporting_window_end=END,
            timezone_name=TZ,
            sent_at=NOW,
            technical_metadata={"recipient": "not-stored@example.com"},
        )


def test_resend_metrics_uses_only_stored_ids_exact_window_timezone_and_internal_key():
    store = SentReportStore(":memory:")
    _record(store, "re-kept", client_id="client-a")
    _record(store, "re-other-client", client_id="client-b")
    calls: list[tuple[str, str]] = []

    def requester(url: str, api_key: str):
        calls.append((url, api_key))
        query = parse_qs(urlsplit(url).query)
        assert query["email_id[]"] == ["re-kept"]
        assert query["start_date"] == [START]
        assert query["end_date"] == [END]
        assert query["timezone"] == [TZ]
        assert query["dimensions"] == ["email"]
        assert "broadcast_id[]" not in query
        return {"totals": _totals(sent=1, delivered=1, unique_opened=1)}

    result = _source(store, requester).fetch_metrics(
        client_id="client-a",
        start_date=START,
        end_date=END,
        timezone=TZ,
        report_type="performance",
    )

    assert result["status"] == SourceAvailability.AVAILABLE.value
    assert result["tracked_report_count"] == 1
    assert result["metrics"]["sent"] == 1
    assert len(calls) == 1
    assert calls[0][1] == "internal-report-key"
    assert "re-other-client" not in str(result)


def test_resend_metrics_batches_at_most_100_ids():
    store = SentReportStore(":memory:")
    for index in range(205):
        _record(store, f"re-{index}")
    calls: list[tuple[str, str]] = []

    def requester(url: str, api_key: str):
        calls.append((url, api_key))
        query = parse_qs(urlsplit(url).query)
        ids = query["email_id[]"]
        assert 1 <= len(ids) <= 100
        return {"totals": _totals(sent=len(ids), delivered=len(ids))}

    result = _source(store, requester).fetch_metrics("client-a", START, END, TZ)

    assert result["status"] == SourceAvailability.AVAILABLE.value
    assert result["tracked_report_count"] == 205
    assert result["successful_batches"] == 3
    assert result["failed_batches"] == 0
    assert result["metrics"]["sent"] == 205
    assert len(calls) == 3


def test_resend_metrics_empty_window_does_not_call_provider():
    store = SentReportStore(":memory:")
    calls: list[str] = []

    def requester(url: str, api_key: str):
        calls.append(url)
        return {"totals": _totals()}

    result = _source(store, requester).fetch_metrics("client-a", START, END, TZ)

    assert result["status"] == SourceAvailability.EMPTY.value
    assert result["tracked_report_count"] == 0
    assert calls == []


def test_resend_metrics_cache_is_honest_and_invalidated_by_new_stored_id():
    store = SentReportStore(":memory:")
    _record(store, "re-first")
    calls: list[str] = []

    def requester(url: str, api_key: str):
        calls.append(url)
        ids = parse_qs(urlsplit(url).query)["email_id[]"]
        return {"totals": _totals(sent=len(ids), delivered=len(ids))}

    source = _source(store, requester)
    first = source.fetch_metrics("client-a", START, END, TZ)
    cached = source.fetch_metrics("client-a", START, END, TZ)
    _record(store, "re-second")
    refreshed = source.fetch_metrics("client-a", START, END, TZ)

    assert first["cache_hit"] is False
    assert cached["cache_hit"] is True
    assert refreshed["cache_hit"] is False
    assert refreshed["tracked_report_count"] == 2
    assert len(calls) == 2


def test_resend_metrics_reports_local_retention_without_querying_provider():
    store = SentReportStore(":memory:")
    _record(store, "re-expired", sent_at=NOW - timedelta(days=91))
    calls: list[str] = []

    def requester(url: str, api_key: str):
        calls.append(url)
        return {"totals": _totals(sent=1)}

    result = _source(store, requester, retention_days=90).fetch_metrics("client-a", START, END, TZ)

    assert result["status"] == SourceAvailability.UNAVAILABLE.value
    assert result["retention_clamped"] is True
    assert result["retention_excluded_count"] == 1
    assert calls == []


def test_resend_metrics_exposes_provider_retention_clamp_and_redacted_contract():
    store = SentReportStore(":memory:")
    _record(store, "re-provider-retention")

    def requester(url: str, api_key: str):
        return {
            "start_date": "2026-08-15",
            "end_date": END,
            "timezone": TZ,
            "totals": _totals(sent=1, delivered=1),
        }

    source = _source(store, requester)
    result = source.fetch_metrics("client-a", START, END, TZ)
    contract = source.to_contract(result)

    assert result["status"] == SourceAvailability.PARTIAL.value
    assert result["provider_retention_clamped"] is True
    assert "clamped" in result["reason"]
    assert isinstance(contract, ReportDeliveryMetrics)
    assert not hasattr(contract, "email_ids")


def test_resend_metrics_reports_partial_provider_batches_and_never_invents_missing_totals():
    store = SentReportStore(":memory:")
    for index in range(101):
        _record(store, f"re-{index}")
    calls = 0

    def requester(url: str, api_key: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            ids = parse_qs(urlsplit(url).query)["email_id[]"]
            return {"totals": {"sent": len(ids), "delivered": len(ids)}}
        raise ResendMetricsRequestError()

    result = _source(store, requester).fetch_metrics("client-a", START, END, TZ)

    assert result["status"] == SourceAvailability.PARTIAL.value
    assert result["successful_batches"] == 1
    assert result["failed_batches"] == 1
    assert result["metrics"]["sent"] == 100
    assert "delivered" in result["metrics"]
    assert result["metrics"].get("opened") is None
    assert result["failed_report_count"] == 1


def test_resend_metrics_distinguishes_provider_unavailable_from_provider_error():
    store = SentReportStore(":memory:")
    _record(store, "re-provider")

    unavailable = _source(
        store,
        lambda url, api_key: (_ for _ in ()).throw(ResendMetricsRequestError(403)),
    ).fetch_metrics("client-a", START, END, TZ)
    errored = _source(
        store,
        lambda url, api_key: (_ for _ in ()).throw(ResendMetricsRequestError(500)),
    ).fetch_metrics("client-a", START, END, TZ)

    assert unavailable["status"] == SourceAvailability.UNAVAILABLE.value
    assert errored["status"] == SourceAvailability.ERROR.value


def test_resend_sender_does_not_fall_back_to_legacy_or_website_credentials(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "internal-key-that-is-long-enough")
    monkeypatch.setenv("REPORT_EMAIL_API_KEY", "legacy-key-that-must-not-be-used")
    monkeypatch.setenv("WEBSITE_METRICS_API_KEY", "website-key-that-must-not-be-used")

    sender = ResendEmailSender(api_key="", from_email="reports@example.com")

    assert sender.api_key == ""
    assert sender.is_configured is False


def _client_with_website_config(**website_config) -> ClientConfig:
    return ClientConfig(
        client_id="client-a",
        company_name="Client A",
        domain="https://client-a.example.com",
        timezone=TZ,
        website_inquiry_metrics=website_config,
    )


def test_website_source_disabled_is_not_configured_and_never_reads_internal_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "internal-report-key")
    calls: list[object] = []
    client = _client_with_website_config(enabled=False)
    source = WebsiteInquiryMetricsSource(
        client,
        credential_resolver=lambda ref: calls.append(("resolver", ref)),
        aggregate_reader=lambda **kwargs: calls.append(("reader", kwargs)),
    )

    result = source.fetch_metrics("client-a", START, END, TZ)

    assert result["status"] == SourceAvailability.NOT_CONFIGURED.value
    assert calls == []


def test_website_source_missing_reference_is_not_configured_without_reader_call():
    client = _client_with_website_config(enabled=True, provider="secret_manager", secret_manager_ref="")
    calls: list[str] = []
    source = WebsiteInquiryMetricsSource(
        client,
        credential_resolver=lambda ref: calls.append(ref),
        aggregate_reader=lambda **kwargs: pytest.fail("reader must not be called"),
    )

    result = source.fetch_metrics("client-a", START, END, TZ)

    assert result["status"] == SourceAvailability.NOT_CONFIGURED.value
    assert calls == []


def test_website_source_rejects_malformed_reference_in_client_config():
    with pytest.raises(ValidationError, match="Secret Manager resource reference"):
        _client_with_website_config(
            enabled=True,
            provider="secret_manager",
            secret_manager_ref="raw-website-token",
        )


def test_website_source_missing_or_unauthorized_credential_never_falls_back():
    client = _client_with_website_config(
        enabled=True,
        provider="secret_manager",
        secret_manager_ref="projects/example/secrets/client-a-website/versions/latest",
    )
    reader_calls: list[object] = []
    missing = WebsiteInquiryMetricsSource(
        client,
        credential_resolver=lambda ref: None,
        aggregate_reader=lambda **kwargs: reader_calls.append(kwargs),
    ).fetch_metrics("client-a", START, END, TZ)
    unauthorized = WebsiteInquiryMetricsSource(
        client,
        credential_resolver=lambda ref: (_ for _ in ()).throw(PermissionError()),
        aggregate_reader=lambda **kwargs: reader_calls.append(kwargs),
    ).fetch_metrics("client-a", START, END, TZ)

    assert missing["status"] == SourceAvailability.NOT_CONFIGURED.value
    assert unauthorized["status"] == SourceAvailability.UNAVAILABLE.value
    assert reader_calls == []


def test_website_source_malformed_credential_is_unavailable_and_does_not_use_resend_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "internal-report-key")
    client = _client_with_website_config(
        enabled=True,
        provider="secret_manager",
        secret_manager_ref="projects/example/secrets/client-a-website/versions/latest",
    )
    reader_calls: list[object] = []
    source = WebsiteInquiryMetricsSource(
        client,
        credential_resolver=lambda ref: {"client_id": "client-a", "token": ""},
        aggregate_reader=lambda **kwargs: reader_calls.append(kwargs),
    )

    result = source.fetch_metrics("client-a", START, END, TZ)

    assert result["status"] == SourceAvailability.UNAVAILABLE.value
    assert reader_calls == []


def test_website_source_valid_credential_is_client_bound_and_uses_exact_window():
    client = _client_with_website_config(
        enabled=True,
        provider="secret_manager",
        secret_manager_ref="projects/example/secrets/client-a-website/versions/latest",
        expected_client_id="client-a",
    )
    calls: list[dict] = []

    def reader(**kwargs):
        calls.append(kwargs)
        assert kwargs["credential"].client_id == "client-a"
        assert kwargs["credential"].token == "website-only-token"
        return {
            "status": "available",
            "client_id": "client-a",
            "start_date": START,
            "end_date": END,
            "timezone": TZ,
            "current_inquiries": 3,
            "prior_inquiries": 2,
            "inquiry_events": {"form_submit": 3},
            "prior_inquiry_events": {"form_submit": 2},
        }

    source = WebsiteInquiryMetricsSource(
        client,
        credential_resolver=lambda ref: {"client_id": "client-a", "token": "website-only-token"},
        aggregate_reader=reader,
    )
    result = source.fetch_metrics("client-a", START, END, TZ)

    assert result["status"] == SourceAvailability.AVAILABLE.value
    assert result["current_inquiries"] == 3
    assert result["prior_inquiries"] == 2
    assert len(calls) == 1
    assert calls[0]["start_date"] == START
    assert calls[0]["end_date"] == END
    assert calls[0]["timezone"] == TZ


def test_website_source_does_not_copy_provider_diagnostics_into_client_contract():
    client = _client_with_website_config(enabled=True)
    secret = "website-provider-secret"
    source = WebsiteInquiryMetricsSource(
        client,
        aggregate_reader=lambda **kwargs: {
            "status": "error",
            "reason": f"Authorization: Bearer {secret}",
        },
    )

    result = source.fetch_metrics("client-a", START, END, TZ)

    assert result["status"] == SourceAvailability.ERROR.value
    assert result["reason"] == "Website inquiry aggregate returned an unusable response."
    assert secret not in str(result)


def test_cli_dispatch_provenance_records_provider_id_without_recipients(monkeypatch, tmp_path):
    store_path = tmp_path / "report-delivery.sqlite3"
    monkeypatch.setenv("REPORT_DELIVERY_STORE_PATH", str(store_path))
    client = ClientConfig(
        client_id="client-a",
        company_name="Client A",
        domain="https://client-a.example.com",
        timezone=TZ,
    )

    _persist_dispatch_provenance(
        dispatch={"status": "sent", "id": "re-recorded"},
        client=client,
        report_type="performance",
        observation_start_date=START,
        observation_end_date=END,
        delivery_kind="client_report",
        has_attachment=True,
    )

    record = SentReportStore(store_path).find_sent_reports(
        client_id="client-a",
        report_type="performance",
        reporting_window_start=START,
        reporting_window_end=END,
        timezone_name=TZ,
    ).records[0]
    assert record.resend_email_id == "re-recorded"
    assert record.technical_metadata == {
        "delivery_kind": "client_report",
        "has_attachment": True,
        "provider_status": "sent",
    }


def test_resend_metrics_website_inquiry_delivery_mode_never_uses_internal_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "internal-report-key")
    source = ResendEmailMetricsSource(mode="website_inquiry_delivery")
    assert source.api_key == ""
    assert not source.is_configured()
    result = source.fetch_metrics("client-a", START, END, TZ)
    assert result["status"] == SourceAvailability.NOT_CONFIGURED.value
    assert "credential" in result["reason"].lower()


def test_resend_metrics_website_inquiry_delivery_mode_aggregate_query_with_domain():
    calls: list[tuple[str, str]] = []

    def mock_requester(url: str, api_key: str):
        calls.append((url, api_key))
        return {
            "totals": _totals(sent=10, delivered=9, unique_opened=5, bounced_permanent=0),
            "domain": "thehouseofdentalwp.com",
        }

    source = ResendEmailMetricsSource(
        api_key="client-website-token",
        mode="website_inquiry_delivery",
        expected_sending_domain="thehouseofdentalwp.com",
        requester=mock_requester,
    )
    assert source.is_configured()

    result = source.fetch_metrics("thehouseofdental", START, END, TZ)
    assert result["status"] == SourceAvailability.AVAILABLE.value
    assert result["metrics"]["sent"] == 10
    assert result["metrics"]["delivered"] == 9
    assert result["metrics"]["delivery_rate"] == 90.0
    assert len(calls) == 1
    url, api_key = calls[0]
    assert api_key == "client-website-token"
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert query["domain"] == ["thehouseofdentalwp.com"]
    assert "email_id[]" not in query
    assert "dimensions" not in query


def test_resend_metrics_website_inquiry_delivery_mode_batches_explicit_email_ids():
    calls: list[tuple[str, str]] = []

    def mock_requester(url: str, api_key: str):
        calls.append((url, api_key))
        return {"totals": _totals(sent=1, delivered=1)}

    source = ResendEmailMetricsSource(
        api_key="client-website-token",
        mode="website_inquiry_delivery",
        requester=mock_requester,
    )
    result = source.fetch_metrics("client-a", START, END, TZ, email_ids=["inq-1", "inq-2"])
    assert result["status"] == SourceAvailability.AVAILABLE.value
    assert len(calls) == 1
    url, _ = calls[0]
    query = parse_qs(urlsplit(url).query)
    assert query["dimensions"] == ["email"]
    assert query["email_id[]"] == ["inq-1", "inq-2"]


def test_resend_metrics_website_inquiry_delivery_mode_detects_domain_mismatch():
    def mock_requester(url: str, api_key: str):
        return {
            "totals": _totals(sent=5, delivered=5),
            "domain": "unauthorized-domain.com",
        }

    source = ResendEmailMetricsSource(
        api_key="client-website-token",
        mode="website_inquiry_delivery",
        expected_sending_domain="thehouseofdentalwp.com",
        requester=mock_requester,
    )
    result = source.fetch_metrics("thehouseofdental", START, END, TZ)
    assert result["status"] == SourceAvailability.ERROR.value
    assert result.get("domain_mismatch") is True
    assert "domain" in result["reason"].lower()


def test_website_source_delegates_to_resend_inquiry_delivery_when_no_reader_injected():
    calls: list[tuple[str, str]] = []

    def mock_requester(url: str, api_key: str):
        calls.append((url, api_key))
        return {
            "totals": _totals(sent=8, delivered=7, bounced=0),
            "domain": "thehouseofdentalwp.com",
        }

    client = _client_with_website_config(
        enabled=True,
        provider="secret_manager",
        secret_manager_ref="projects/example/secrets/website-metrics/versions/latest",
        expected_client_id="client-a",
        expected_website_sending_domain="thehouseofdentalwp.com",
    )
    source = WebsiteInquiryMetricsSource(
        client=client,
        credential_resolver=lambda ref: '{"client_id": "client-a", "token": "client-secret-token"}',
        requester=mock_requester,
    )
    assert source.is_configured()
    result = source.fetch_metrics("client-a", START, END, TZ)
    assert result["status"] == SourceAvailability.AVAILABLE.value
    assert result["current_inquiries"] == 7
    assert result["delivery_metrics"]["delivered"] == 7
    assert len(calls) == 1
    assert calls[0][1] == "client-secret-token"

