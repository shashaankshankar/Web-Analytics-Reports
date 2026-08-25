import sys

import pytest

import app.cli as cli
from app.analytics.contracts import ReportType, SourceAvailability
from app.analytics.metrics import calculate_date_ranges
from tests.fakes import (
    FakeGA4Extractor,
    FakeGBPExtractor,
    FakeGSCExtractor,
    fake_client,
    fake_exploration_result,
    fake_report_output,
    fake_weekly_output,
)


class FakeAnalyst:
    def __init__(self, **_kwargs):
        pass

    def analyze(self, _data):
        from tests.fakes import fake_baseline_report_output
        return fake_baseline_report_output() if _data.report_mode.value == "initial_baseline" else fake_report_output()

    def analyze_weekly(self, _data):
        return fake_weekly_output()


class FakeExplorer:
    def __init__(self, **_kwargs):
        pass

    def explore(self, client, _data, _toolkit):
        return fake_exploration_result(
            client,
            report_mode=_data.report_mode,
            measurement_start_date=_data.measurement_start_date,
            observation_start=_data.period_start,
            observation_end=_data.period_end,
            comparison_suppression_reason=_data.comparison_suppression_reason,
        )


class FakeExplorerVerifierOutage(FakeExplorer):
    def explore(self, client, data, toolkit):
        result = super().explore(client, data, toolkit)
        result.audit.status = "deterministic_only_verifier_unavailable"
        result.audit.verifier_status = "provider_error"
        return result


class SenderDouble:
    def __init__(self, audit_status="failed"):
        self.is_configured = True
        self.audit_status = audit_status
        self.calls = []

    def send_briefing(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return {"status": self.audit_status, "id": "audit-test"}
        return {"status": "sent", "id": "client-test"}


@pytest.fixture
def patched_pipeline(monkeypatch):
    client = fake_client()
    monkeypatch.setattr(cli, "load_client_config_by_slug", lambda _slug: client)
    monkeypatch.setattr(cli, "GA4Extractor", FakeGA4Extractor)
    monkeypatch.setattr(cli, "SearchConsoleExtractor", FakeGSCExtractor)
    monkeypatch.setattr(cli, "GoogleBusinessProfileExtractor", FakeGBPExtractor)
    monkeypatch.setattr(cli, "GrowthAnalyst", FakeAnalyst)
    monkeypatch.setattr(cli, "ExploratoryGrowthAgent", FakeExplorer)
    return client


def test_generate_report_e2e_performance_uses_only_test_fakes(tmp_path, patched_pipeline):
    briefing = cli.generate_report(
        client_slug="test-client",
        report_type="performance",
        days=28,
        send_email=False,
        output_dir=tmp_path,
    )
    assert briefing.client_id == "test-client"
    assert briefing.report_type == ReportType.PERFORMANCE_28D
    assert briefing.exploration_audit is None
    assert len(briefing.insights.executive_summary) == 3
    assert (tmp_path / "test-client_performance_briefing.html").is_file()
    assert (tmp_path / "test-client_performance_growth_report.pdf").is_file()
    assert (tmp_path / "test-client_briefing.html").is_file()
    assert (tmp_path / "test-client_growth_report.pdf").is_file()


def test_generate_report_weekly_has_no_deep_insights_section(tmp_path, patched_pipeline):
    briefing = cli.generate_report(
        client_slug="test-client",
        report_type="weekly",
        days=7,
        send_email=False,
        output_dir=tmp_path,
    )
    assert briefing.report_type == ReportType.WEEKLY
    assert briefing.weekly_insights is not None
    html = (tmp_path / "test-client_weekly_briefing.html").read_text(encoding="utf-8")
    assert "Weekly Growth Digest" in html
    assert "Key Opportunities &amp; Discoveries" not in html


def test_generate_report_explicit_explore_saves_internal_audit(tmp_path, patched_pipeline):
    briefing = cli.generate_report(
        client_slug="test-client",
        report_type="performance",
        days=28,
        send_email=False,
        output_dir=tmp_path,
        explore_deep_insights=True,
    )
    assert briefing.exploration_audit is not None
    assert briefing.exploration_audit.enabled is True
    audit_files = list(tmp_path.glob("*deep_insights_audit.*"))
    assert {path.suffix for path in audit_files} == {".json", ".html"}
    html = (tmp_path / f"test-client_performance_{briefing.analytics.period_start}_{briefing.analytics.period_end}.html").read_text(encoding="utf-8")
    assert "Where We're Focusing Next" in html
    assert "No additional opportunities were identified from this period, and no recommendations were added." in html
    assert "No evidence-backed exploratory finding was available" not in html


def test_generate_weekly_and_performance_baselines_do_not_overwrite_each_other(tmp_path, patched_pipeline):
    patched_pipeline.measurement_start_date = "2026-08-12"
    weekly = cli.generate_report(
        client_slug="test-client",
        report_type="weekly",
        days=7,
        send_email=False,
        output_dir=tmp_path,
    )
    performance = cli.generate_report(
        client_slug="test-client",
        report_type="performance",
        days=28,
        send_email=False,
        output_dir=tmp_path,
    )

    weekly_stem = f"test-client_weekly_initial_baseline_{weekly.analytics.period_start}_{weekly.analytics.period_end}"
    performance_stem = f"test-client_initial_baseline_{performance.analytics.period_start}_{performance.analytics.period_end}"
    weekly_html = (tmp_path / f"{weekly_stem}.html").read_text(encoding="utf-8")
    performance_html = (tmp_path / f"{performance_stem}.html").read_text(encoding="utf-8")

    assert "Weekly Growth Digest" in weekly_html
    assert "Initial Measurement Baseline" in performance_html
    assert weekly_stem != performance_stem


def test_generate_report_initial_baseline_uses_observation_start_and_audits_suppressed_comparison(
    tmp_path, monkeypatch
):
    client = fake_client(deep_insights=True)
    client.measurement_start_date = "2026-08-12"
    _, expected_period_end, _, _ = calculate_date_ranges(days=28, timezone_str=client.timezone)
    monkeypatch.setattr(cli, "load_client_config_by_slug", lambda _slug: client)
    monkeypatch.setattr(cli, "GA4Extractor", FakeGA4Extractor)
    monkeypatch.setattr(cli, "SearchConsoleExtractor", FakeGSCExtractor)
    monkeypatch.setattr(cli, "GoogleBusinessProfileExtractor", FakeGBPExtractor)
    monkeypatch.setattr(cli, "GrowthAnalyst", FakeAnalyst)
    monkeypatch.setattr(cli, "ExploratoryGrowthAgent", FakeExplorer)

    briefing = cli.generate_report(
        client_slug="test-client",
        report_type="performance",
        days=28,
        send_email=False,
        output_dir=tmp_path,
        explore_deep_insights=True,
    )

    assert briefing.report_mode.value == "initial_baseline"
    assert briefing.analytics.period_start == "2026-08-12"
    assert briefing.analytics.period_end == expected_period_end
    assert briefing.observation_window_end == expected_period_end
    assert briefing.analytics.core_metrics[0].prior_value is None
    assert briefing.analytics.core_metrics[0].absolute_change is None
    assert briefing.exploration_audit.report_mode.value == "initial_baseline"
    assert briefing.exploration_audit.evidence.comparison_suppressed is True
    assert briefing.exploration_audit.evidence.records == []

    report_stem = f"test-client_initial_baseline_{briefing.analytics.period_start}_{briefing.analytics.period_end}"
    html = (tmp_path / f"{report_stem}.html").read_text(encoding="utf-8")
    assert "Initial Measurement Baseline" in html
    assert "no growth deltas or prior-period values are shown" in html
    assert "28-Day Performance Report" not in html
    audit = (tmp_path / f"{report_stem}_deep_insights_audit.json").read_text(encoding="utf-8")
    assert '"report_mode": "initial_baseline"' in audit
    assert '"comparison_suppressed": true' in audit


def test_generate_report_refuses_non_performance_explore(tmp_path, patched_pipeline):
    with pytest.raises(RuntimeError, match="performance reports only"):
        cli.generate_report(
            client_slug="test-client",
            report_type="weekly",
            output_dir=tmp_path,
            explore_deep_insights=True,
        )


def test_generate_report_refuses_unavailable_ga4(monkeypatch, tmp_path):
    client = fake_client()
    monkeypatch.setattr(cli, "load_client_config_by_slug", lambda _slug: client)
    monkeypatch.setattr(cli, "GA4Extractor", lambda _property_id: FakeGA4Extractor(status=SourceAvailability.UNAVAILABLE.value))
    with pytest.raises(RuntimeError, match="GA4 source is unavailable"):
        cli.generate_report(client_slug="test-client", output_dir=tmp_path)


def test_source_adapters_keep_unavailable_empty_and_error_distinct():
    from app.sources.ga4 import GA4Extractor
    from app.sources.gbp import GoogleBusinessProfileExtractor
    from app.sources.gsc import SearchConsoleExtractor

    ga4 = GA4Extractor(property_id="")
    ga4_result = ga4.run_report("2026-07-01", "2026-07-28", [], [])
    assert ga4_result["status"] == SourceAvailability.UNAVAILABLE.value
    assert ga4_result["rows"] == []

    gsc = SearchConsoleExtractor(site_url="")
    gsc_result = gsc.fetch_search_analytics("2026-07-01", "2026-07-28")
    assert gsc_result["status"] == SourceAvailability.UNAVAILABLE.value
    current, prior = gsc.fetch_comparative_search_analytics("2026-07-01", "2026-07-07", "2026-06-24", "2026-06-30")
    assert current["status"] == SourceAvailability.UNAVAILABLE.value
    assert prior["status"] == SourceAvailability.UNAVAILABLE.value

    gbp = GoogleBusinessProfileExtractor(location_id="")
    gbp_result = gbp.fetch_local_insights("2026-07-01", "2026-07-28")
    assert gbp_result["status"] == SourceAvailability.UNAVAILABLE.value
    assert gbp_result["phone_calls"] is None


def test_cli_has_no_runtime_mock_argument(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["app.cli", "generate", "--client", "test-client", "--mock"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2


def test_generate_report_dry_run_does_not_write_artifacts(tmp_path, patched_pipeline):
    briefing = cli.generate_report(client_slug="test-client", output_dir=tmp_path, dry_run=True)
    assert briefing.client_id == "test-client"
    assert list(tmp_path.iterdir()) == []


def test_audit_delivery_precedes_and_gates_client_delivery(monkeypatch, tmp_path, patched_pipeline):
    sender = SenderDouble(audit_status="failed")
    monkeypatch.setattr(cli, "is_production_dispatch_allowed", lambda _client_id: True)
    monkeypatch.setattr(cli, "ResendEmailSender", lambda: sender)

    with pytest.raises(RuntimeError, match="audit email was not accepted"):
        cli.generate_report(
            client_slug="test-client",
            report_type="performance",
            send_email=True,
            output_dir=tmp_path,
            explore_deep_insights=True,
        )

    assert len(sender.calls) == 1
    assert sender.calls[0]["to_recipients"] == ["audit@test.example.com"]
    assert "Deep Insights Audit" in sender.calls[0]["subject"]


def test_verifier_outage_allows_local_generation_but_blocks_email(monkeypatch, tmp_path, patched_pipeline):
    sender = SenderDouble(audit_status="sent")
    monkeypatch.setattr(cli, "ExploratoryGrowthAgent", FakeExplorerVerifierOutage)
    monkeypatch.setattr(cli, "is_production_dispatch_allowed", lambda _client_id: True)
    monkeypatch.setattr(cli, "ResendEmailSender", lambda: sender)

    with pytest.raises(RuntimeError, match="verifier was unavailable"):
        cli.generate_report(
            client_slug="test-client",
            report_type="performance",
            send_email=True,
            output_dir=tmp_path,
            explore_deep_insights=True,
        )
    assert sender.calls == []
    assert list(tmp_path.glob("*_performance_*.html"))
    assert list(tmp_path.glob("*_performance_*.pdf"))
    assert list(tmp_path.glob("*_performance_*_deep_insights_audit.json"))
