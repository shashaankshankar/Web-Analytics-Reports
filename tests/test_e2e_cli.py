from pathlib import Path
import pytest
from app.cli import generate_report

def test_generate_report_e2e_dental(tmp_path):
    briefing = generate_report(
        client_slug="example-dental",
        days=28,
        send_email=False,
        output_dir=tmp_path,
        mock_data=True,
    )
    assert briefing.client_id == "example-dental"
    assert briefing.company_name == "Apex Dental Studio"
    assert len(briefing.insights.executive_summary) == 3
    
    html_file = tmp_path / "example-dental_briefing.html"
    pdf_file = tmp_path / "example-dental_growth_report.pdf"
    assert html_file.is_file()
    assert pdf_file.is_file()
    assert html_file.stat().st_size > 500
    assert pdf_file.stat().st_size > 2000

def test_generate_report_e2e_saas(tmp_path):
    briefing = generate_report(
        client_slug="example-saas",
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
    
    gbp = GoogleBusinessProfileExtractor(location_id="")
    assert gbp.is_configured() is False
    assert gbp.fetch_local_insights("2026-07-01", "2026-07-28")["phone_calls"] == 0
