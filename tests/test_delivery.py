from io import BytesIO
from pypdf import PdfReader
import pytest
from app.analytics.contracts import (
    ActionItem,
    AIReportOutput,
    DataDiscovery,
    FullGrowthBriefing,
    GrowthAnalysisInput,
    MetricDelta,
    StrikingDistanceKeyword,
)
from app.delivery.email_template import render_growth_email_html
from app.delivery.pdf_builder import build_executive_pdf
from app.delivery.sender import ResendEmailSender, is_valid_email

@pytest.fixture
def sample_full_briefing():
    analytics = GrowthAnalysisInput(
        client_id="acme",
        company_name="Acme Dental Studio",
        domain="https://acme.example.com",
        industry="healthcare",
        period_start="2026-07-22",
        period_end="2026-08-18",
        comparison_start="2026-06-24",
        comparison_end="2026-07-21",
        core_metrics=[
            MetricDelta(
                metric_name="sessions",
                display_name="Total Sessions",
                current_value=1850,
                prior_value=1590,
                absolute_change=260,
                percentage_change=16.4,
                direction="up",
            ),
        ],
        striking_distance_keywords=[
            StrikingDistanceKeyword(
                query="dental implants cost",
                impressions=950,
                clicks=15,
                ctr=1.58,
                position=12.4,
                opportunity_score=8170.0,
            ),
        ],
    )
    insights = AIReportOutput(
        executive_summary=[
            "Total sessions grew +16.4% to 1,850.",
            "Organic search contributed the majority of qualified inquiries.",
            "High-intent local calls reached 42 direct inquiries.",
        ],
        traffic_and_inflow_insights="Traffic volume displayed steady growth over the 28-day cycle.",
        seo_and_content_opportunities="Striking distance keyword analysis indicates high upside on dental implants cost.",
        local_seo_insights="Local map pack performance remained strong.",
        agency_action_plan=[
            ActionItem(
                title="On-Page Content Expansion",
                description="Expand pricing guide and schema markup.",
                impact_area="SEO",
                priority="High",
            ),
        ],
    )
    return FullGrowthBriefing(
        client_id="acme",
        company_name="Acme Dental Studio",
        domain="https://acme.example.com",
        industry="healthcare",
        generated_at="August 19, 2026 at 15:00 UTC",
        period_label="Last 28 Days (2026-07-22 to 2026-08-18)",
        branding={
            "primary_color": "#1E3A8A",
            "secondary_color": "#3B82F6",
            "accent_color": "#F59E0B",
        },
        analytics=analytics,
        insights=insights,
    )

def test_render_growth_email_html(sample_full_briefing):
    html = render_growth_email_html(sample_full_briefing)
    assert "Acme Dental Studio" in html
    assert "Total Sessions" in html
    assert "1,850" in html
    assert "dental implants cost" in html
    assert "On-Page Content Expansion" in html

def test_render_growth_email_html_with_discoveries_and_escaping(sample_full_briefing):
    sample_full_briefing.insights.deep_discoveries = [
        DataDiscovery(
            title="High-ROI <Mobile> Breakthrough & Queries",
            source="GA4 & Search Console",
            insight="Mobile queries with <special> characters & symbols drove 70% of conversion volume.",
            recommended_action="Enhance <meta> tags & CTA buttons.",
        )
    ]
    html_out = render_growth_email_html(sample_full_briefing)
    assert "&lt;Mobile&gt;" in html_out
    assert "GA4 &amp; Search Console" in html_out
    assert "&lt;meta&gt;" in html_out

def test_build_executive_pdf(sample_full_briefing):
    pdf_bytes = build_executive_pdf(sample_full_briefing)
    assert len(pdf_bytes) > 2000
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) >= 1
    text = reader.pages[0].extract_text()
    assert "Acme Dental Studio" in text
    assert "EXECUTIVE SNAPSHOT" in text

def test_resend_sender_validation():
    assert is_valid_email("test@example.com") is True
    assert is_valid_email("invalid-email") is False

def test_resend_sender_simulated_when_unconfigured():
    sender = ResendEmailSender(api_key="", from_email="")
    assert sender.is_configured is False
    res = sender.send_briefing(
        to_recipients=["client@example.com"],
        subject="Test Report",
        html_content="<p>Test</p>",
    )
    assert res["status"] == "simulated_unconfigured"


def test_sender_with_comma_separated_recipients():
    sender = ResendEmailSender(api_key="", from_email="")
    res = sender.send_briefing(
        to_recipients="client1@example.com, client2@example.com",
        subject="Test Multi",
        html_content="<p>Test</p>",
        cc_recipients="agency@example.com, lead@example.com",
    )
    assert res["status"] == "simulated_unconfigured"
    assert res["to"] == ["client1@example.com", "client2@example.com"]
