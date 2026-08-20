from io import BytesIO
from pypdf import PdfReader
import pytest
from app.analytics.contracts import (
    ActionItem,
    AIReportOutput,
    ConversionEventSummary,
    DataDiscovery,
    FullGrowthBriefing,
    GrowthAnalysisInput,
    LocalInteractionData,
    MetricDelta,
    ReportType,
    StrikingDistanceKeyword,
    WeeklyDigestOutput,
)
from app.delivery.email_template import render_growth_email_html
from app.delivery.pdf_builder import build_executive_pdf
from app.delivery.sender import ResendEmailSender, is_valid_email
from app.delivery.weekly_digest_template import render_weekly_digest_html

@pytest.fixture
def sample_full_briefing():
    analytics = GrowthAnalysisInput(
        client_id="acme",
        company_name="Acme Dental Studio",
        domain="https://acme.example.com",
        industry="healthcare",
        report_type=ReportType.PERFORMANCE_28D,
        period_days=28,
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
            MetricDelta(
                metric_name="conversion_rate",
                display_name="Conversion Rate",
                current_value=2.59,
                prior_value=2.45,
                absolute_change=0.14,
                percentage_points_change=0.14,
                is_percentage_rate=True,
                direction="up",
                unit="percentage",
            ),
        ],
        conversion_events=[
            ConversionEventSummary(
                event_name="generate_lead",
                display_name="Lead Submissions",
                current_count=32,
                prior_count=25,
                count_change=7,
                percentage_change=28.0,
                direction="up",
            )
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
        local_seo=LocalInteractionData(
            phone_calls=42,
            prior_phone_calls=35,
            phone_calls_change=7,
            phone_calls_direction="up",
        ),
    )
    insights = AIReportOutput(
        executive_summary=[
            "Total sessions grew +16.4% to 1,850.",
            "Organic search contributed the majority of qualified inquiries.",
            "High-intent local calls reached 42 direct inquiries.",
        ],
        biggest_win="Organic search traffic grew 22% adding 184 visits.",
        watch_item="Implants page conversion rate dipped slightly below site average.",
        traffic_and_inflow_insights="Traffic volume displayed steady growth over the 28-day cycle.",
        conversion_insights="Conversions totaled 48 inquiries across leads and calls.",
        seo_and_content_opportunities="Striking distance keyword analysis indicates high upside on dental implants cost.",
        local_seo_insights="Local map pack performance remained strong.",
        agency_action_plan=[
            ActionItem(
                title="On-Page Content Expansion",
                description="Expand pricing guide and schema markup.",
                impact_area="SEO",
                priority="High",
                evidence="Position 12.4 for implants query",
            ),
        ],
    )
    return FullGrowthBriefing(
        client_id="acme",
        company_name="Acme Dental Studio",
        domain="https://acme.example.com",
        industry="healthcare",
        generated_at="August 19, 2026 at 15:00 UTC",
        period_label="28-Day Period (2026-07-22 to 2026-08-18)",
        report_type=ReportType.PERFORMANCE_28D,
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
    assert "Biggest Win" in html
    assert "Area to Improve" in html
    assert "dental implants cost" in html
    assert "On-Page Content Expansion" in html
    assert "Detailed Executive PDF Report Attached" in html

def test_render_weekly_digest_html(sample_full_briefing):
    sample_full_briefing.report_type = ReportType.WEEKLY
    sample_full_briefing.period_label = "Weekly Period (2026-08-12 to 2026-08-18)"
    sample_full_briefing.weekly_insights = WeeklyDigestOutput(
        biggest_win="Weekly patient inquiries jumped 35%.",
        needs_attention="Mobile bounce rate increased 2.1%.",
        acquisition_insight="Organic Google search accounted for 65% of volume.",
        search_opportunity="Dental cleaning near me is ranking on position 9.",
        local_insight="Phone calls reached 11 direct inquiries.",
        next_actions=[
            ActionItem(title="Update Mobile CTA", description="Simplify form fields.", impact_area="Conversion", priority="High")
        ],
    )
    html = render_weekly_digest_html(sample_full_briefing)
    assert "Weekly Growth Digest" in html
    assert "Week at a Glance" in html
    assert "Weekly patient inquiries jumped 35%." in html
    assert "Area to Improve" in html
    assert "Update Mobile CTA" in html

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
    assert "KEY INQUIRY ACTIONS" in text

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
        idempotency_key="test-key-123",
    )
    assert res["status"] == "simulated_unconfigured"
    assert res["to"] == ["client1@example.com", "client2@example.com"]
    assert res["idempotency_key"] == "test-key-123"

