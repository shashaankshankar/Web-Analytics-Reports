from pathlib import Path
import pytest
from app.cli import generate_report
from app.analytics.contracts import ReportType

def test_generate_report_e2e_dental_performance(tmp_path):
    briefing = generate_report(
        client_slug="example-dental",
        report_type="performance",
        days=28,
        send_email=False,
        output_dir=tmp_path,
        mock_data=True,
    )
    assert briefing.client_id == "example-dental"
    assert briefing.company_name == "Apex Dental Studio"
    assert briefing.report_type == ReportType.PERFORMANCE_28D
    assert len(briefing.insights.executive_summary) == 3
    
    html_file = tmp_path / "example-dental_briefing.html"
    pdf_file = tmp_path / "example-dental_growth_report.pdf"
    assert html_file.is_file()
    assert pdf_file.is_file()
    assert html_file.stat().st_size > 500
    assert pdf_file.stat().st_size > 2000

def test_generate_report_e2e_dental_weekly(tmp_path):
    briefing = generate_report(
        client_slug="example-dental",
        report_type="weekly",
        days=7,
        send_email=False,
        output_dir=tmp_path,
        mock_data=True,
    )
    assert briefing.client_id == "example-dental"
    assert briefing.company_name == "Apex Dental Studio"
    assert briefing.report_type == ReportType.WEEKLY
    assert briefing.weekly_insights is not None
    
    html_file = tmp_path / "example-dental_briefing.html"
    assert html_file.is_file()
    html_content = html_file.read_text(encoding="utf-8")
    assert "Weekly Growth Digest" in html_content
    assert "Week at a Glance" in html_content

def test_generate_report_e2e_saas(tmp_path):
    briefing = generate_report(
        client_slug="example-saas",
        report_type="performance",
        days=28,
        send_email=False,
        output_dir=tmp_path,
        mock_data=True,
    )
    assert briefing.client_id == "example-saas"
    assert briefing.company_name == "CloudMetrics SaaS"
    
    html_file = tmp_path / "example-saas_briefing.html"
    pdf_file = tmp_path / "example-saas_growth_report.pdf"
    assert html_file.is_file()
    assert pdf_file.is_file()

def test_generate_report_with_exploratory_agent(tmp_path):
    briefing = generate_report(
        client_slug="example-dental",
        report_type="performance",
        days=28,
        send_email=False,
        output_dir=tmp_path,
        mock_data=True,
        explore_deep_insights=True,
    )
    assert briefing.client_id == "example-dental"
    assert len(briefing.insights.deep_discoveries) >= 2
    
    html_file = tmp_path / "example-dental_briefing.html"
    pdf_file = tmp_path / "example-dental_growth_report.pdf"
    assert html_file.is_file()
    assert pdf_file.is_file()
    
    html_content = html_file.read_text(encoding="utf-8")
    assert "Key Opportunities &amp; Discoveries" in html_content
    assert briefing.insights.deep_discoveries[0].title in html_content

def test_sources_graceful_when_unconfigured():
    from app.sources.ga4 import GA4Extractor
    from app.sources.gsc import SearchConsoleExtractor
    from app.sources.gbp import GoogleBusinessProfileExtractor
    
    ga4 = GA4Extractor(property_id="")
    assert ga4.is_configured() is False
    assert ga4.run_report("2026-07-01", "2026-07-28", [], []) == {"rows": [], "row_count": 0}
    
    gsc = SearchConsoleExtractor(site_url="")
    assert gsc.is_configured() is False
    assert gsc.fetch_search_analytics("2026-07-01", "2026-07-28") == []
    current, prior = gsc.fetch_comparative_search_analytics("2026-07-01", "2026-07-07", "2026-06-24", "2026-06-30")
    assert current == [] and prior == []
    
    gbp = GoogleBusinessProfileExtractor(location_id="")
    assert gbp.is_configured() is False
    assert gbp.fetch_local_insights("2026-07-01", "2026-07-28")["phone_calls"] == 0

def test_generate_report_dry_run():
    briefing = generate_report(
        client_slug="example-dental",
        mock_data=True,
        dry_run=True,
    )
    assert briefing.client_id == "example-dental"
    assert len(briefing.analytics.core_metrics) == 5

