import re
from io import BytesIO
from pypdf import PdfReader
import pytest
from app.analytics.contracts import (
    ActionItem,
    LocalInteractionData,
    MetricDelta,
    ReportMode,
    ReportType,
    ReportDeliveryMetrics,
    SourceAvailability,
    WeeklyDigestOutput,
    WebsiteInquiryMetrics,
)
from app.delivery.email_components import (
    is_light_color,
    render_action_row,
    render_bar_rows,
    render_delta_chip,
    render_kpi_cells,
)
from app.delivery.email_template import render_growth_email_html
from app.delivery.discovery_copy import build_client_discovery_copy, build_client_discovery_copies
from app.delivery.pdf_builder import build_executive_pdf
from app.delivery.sender import ResendEmailSender, is_valid_email
from app.delivery.weekly_digest_template import render_weekly_digest_html
from tests.fakes import (
    CURRENT_END,
    CURRENT_START,
    fake_completed_discovery_audit,
    fake_discovery,
    fake_full_briefing,
)

def pdf_text_of(pdf_bytes) -> str:
    """Extracted PDF text with wrapping collapsed.

    Line breaks are a layout decision; these assertions are about whether the
    content reached the client artifact at all.
    """
    raw = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf_bytes)).pages)
    return re.sub(r"\s+", " ", raw)


@pytest.fixture
def approved_full_briefing():
    return fake_full_briefing()

def test_render_growth_email_html(approved_full_briefing):
    html = render_growth_email_html(approved_full_briefing)
    assert "Test Company" in html
    assert "Total Sessions" in html
    assert "700" in html
    assert "Biggest Win" in html
    assert "Area to Improve" in html
    assert "The configured search topic is available for future prioritization." in html
    assert "High-Opportunity Google Searches" in html
    # Ledger layout: a branded KPI strip over numbered, hairline-ruled sections.
    assert "background-color: #1E3A8A; padding: 30px 36px 28px 36px" in html
    assert ">01</td>" in html and ">02</td>" in html
    # A mid-tone brand secondary must not become a card background behind dark text.
    assert "background-color: #3B82F6" not in html
    assert "background-color: #F7F4EE" in html
    assert "Key searches where your website currently ranks on page 2" not in html
    assert "Review Configured Page Data" in html
    assert "Current Goals" in html
    assert "Improve qualified inquiries" in html
    assert "Detailed Executive PDF Report Attached" in html
    assert "weekly-action-copy" not in html
    assert "weekly-action-check" not in html


def test_render_reports_include_deterministic_gbp_evidence(approved_full_briefing):
    approved_full_briefing.analytics.local_seo = LocalInteractionData(
        profile_status="available",
        profile={
            "title": "Test Company",
            "primary_phone": "+1 555 0100",
            "address": {"addressLines": ["1 Test Street"], "locality": "Winter Park", "postalCode": "32789"},
            "website_uri": "https://test.example.com",
            "regular_hours": {"periods": [{
                "openDay": "MONDAY", "openTime": {"hours": 8},
                "closeDay": "MONDAY", "closeTime": {"hours": 17},
            }]},
            "primary_category": {"displayName": "Dentist"},
            "services": [{"structuredServiceItem": {"description": "Teeth whitening"}}],
        },
        performance_status="available",
        performance_metrics={"CALL_CLICKS": {"total": 5, "series": []}},
        available_performance_metrics=["CALL_CLICKS"],
        performance_metric_deltas=[MetricDelta(
            metric_name="gbp_call_clicks",
            display_name="Call Button Clicks",
            current_value=5,
            prior_value=2,
            absolute_change=3,
            percentage_change=150.0,
            direction="up",
        )],
        search_keywords_status="available",
        monthly_search_keywords=[{
            "search_keyword": "dentist winter park",
            "insights_value": 22,
            "insights_value_type": "value",
        }, {
            "search_keyword": "cosmetic dentist",
            "insights_threshold": 10,
            "insights_value_type": "threshold",
        }],
        reviews_status="available",
        reviews=[{
            "star_rating": "FIVE",
            "reply_status": "NOT_REPLIED",
            "update_time": "2026-08-01T00:00:00Z",
            "comment": "A source-backed review comment.",
        }],
        review_inventory_complete=True,
        review_response_summary={
            "review_count": 1,
            "unreplied_count": 1,
            "reply_coverage_percent": 0.0,
            "complete": True,
        },
        business_calls_status="available",
        answered_calls=7,
        missed_calls=2,
    )

    html_out = render_growth_email_html(approved_full_briefing)
    pdf_text = pdf_text_of(build_executive_pdf(approved_full_briefing))
    for client_surface in (html_out, pdf_text):
        assert "Profile details" in client_surface or "PROFILE DETAILS" in client_surface
        assert "Call Button Clicks" in client_surface
        assert "dentist winter park" in client_surface
        assert "Privacy threshold" in client_surface
        assert "Managed reviews" in client_surface or "MANAGED REVIEWS" in client_surface
        assert "Answered calls" in client_surface

def test_render_weekly_digest_html(approved_full_briefing):
    approved_full_briefing.report_type = ReportType.WEEKLY
    approved_full_briefing.period_label = f"Weekly Period ({CURRENT_START} to {CURRENT_END})"
    approved_full_briefing.weekly_insights = WeeklyDigestOutput(
        biggest_win="The current weekly source snapshot is available.",
        needs_attention="Continue monitoring the configured conversion path.",
        acquisition_insight="The configured acquisition channels are available.",
        conversion_insight="The current snapshot recorded one consultation inquiry; configured key conversions are reported separately.",
        search_opportunity="Review the configured search topic in the next cycle.",
        local_insight="GBP action metrics are unavailable from this connector.",
    )
    html = render_weekly_digest_html(approved_full_briefing)
    assert "Weekly Growth Digest" in html
    # Headline metrics live in the branded KPI strip rather than a card grid.
    assert "Total Sessions" in html
    assert 'class="kpi-val"' in html
    assert "The current weekly source snapshot is available." in html
    assert "The configured acquisition channels are available." in html
    assert "The current snapshot recorded one consultation inquiry" in html
    assert "Customer Inquiries &amp; Key Actions" in html
    assert "Area to Improve" in html
    assert "Recommended Next Actions" not in html
    assert "Review Configured Page Data" not in html
    assert "Current Goals" in html
    assert "Improve qualified inquiries" in html
    # KPIs scale down rather than stacking, so they stay on one row on phones.
    assert ".kpi-val { font-size: 28px !important" in html
    assert ".kpi { display: block" not in html


def test_delivery_sections_are_separate_redacted_surfaces(approved_full_briefing):
    approved_full_briefing.report_delivery_metrics = ReportDeliveryMetrics(
        status=SourceAvailability.PARTIAL,
        client_id="test-client",
        report_type="performance",
        start_date=CURRENT_START,
        end_date=CURRENT_END,
        timezone="America/New_York",
        metrics={"sent": 4, "delivered": 3, "delivery_rate": 75.0},
        tracked_report_count=4,
        successful_report_count=3,
        failed_report_count=1,
    )
    approved_full_briefing.analytics.website_inquiry_metrics = WebsiteInquiryMetrics(
        status=SourceAvailability.AVAILABLE,
        current_inquiries=5,
        prior_inquiries=3,
        inquiry_events={"email_delivered": 5},
        prior_inquiry_events={"email_delivered": 3},
    )

    html_out = render_growth_email_html(approved_full_briefing)
    pdf_text = pdf_text_of(build_executive_pdf(approved_full_briefing))
    for surface in (html_out, pdf_text):
        assert "Analytics Report Delivery" in surface or "ANALYTICS REPORT DELIVERY" in surface
        assert "Website Inquiry Delivery" in surface or "WEBSITE INQUIRY DELIVERY" in surface
        assert "Some tracked activity was unavailable" in surface
        assert "Inquiry messages delivered" in surface
        assert "re-client-provider-id" not in surface
        assert "recipient@example.com" not in surface
        assert "Authorization" not in surface


def test_monthly_html_limits_recommended_actions_to_three_strongest(approved_full_briefing):
    approved_full_briefing.insights.agency_action_plan = [
        ActionItem(title="Medium action kept", description="Medium action description.", priority="Medium"),
        ActionItem(title="Low action omitted", description="Low action description.", priority="Low"),
        ActionItem(title="High action one", description="High action description.", priority="High"),
        ActionItem(title="High action two", description="High action description.", priority="High"),
        ActionItem(title="Medium action omitted", description="Another medium action.", priority="Medium"),
    ]

    html = render_growth_email_html(approved_full_briefing)

    assert "High action one" in html
    assert "High action two" in html
    assert "Medium action kept" in html
    assert "Low action omitted" not in html
    assert "Medium action omitted" not in html
    assert html.count("Top Priority") == 2
    assert html.count("Recommended Next Step") == 1


def test_weekly_baseline_caveat_is_visible(approved_full_briefing):
    approved_full_briefing.report_type = ReportType.WEEKLY
    approved_full_briefing.report_mode = ReportMode.INITIAL_BASELINE
    approved_full_briefing.observation_window_start = "2026-08-12"
    approved_full_briefing.observation_window_end = CURRENT_END
    approved_full_briefing.weekly_insights = WeeklyDigestOutput(
        biggest_win="The current observation is available.",
        acquisition_insight="The current channel snapshot is available.",
        conversion_insight="No additional customer action detail was supplied.",
    )
    html = render_weekly_digest_html(approved_full_briefing)
    assert "Initial Measurement Baseline" in html
    assert "is not a week-over-week change report" in html

def test_render_growth_email_html_with_discoveries_and_escaping(approved_full_briefing):
    discovery = fake_discovery(
        "mobile-content",
        category="content",
        metric_name="service_content",
        label="configured mobile service content",
        value=None,
        unit="text",
        title="Mobile <Opportunity> & Growth",
        insight="Mobile <special> visitors deserve a clearer inquiry path.",
        recommended_action="Enhance <service> details & inquiry buttons.",
    )
    approved_full_briefing.insights.deep_discoveries = [discovery]
    approved_full_briefing.exploration_audit = fake_completed_discovery_audit(approved_full_briefing.insights.deep_discoveries)
    html_out = render_growth_email_html(approved_full_briefing)
    assert "Where We're Focusing Next" in html_out
    assert "What we noticed:" in html_out
    assert "Recommended next step:" in html_out
    assert "&lt;special&gt;" in html_out
    assert "&lt;service&gt;" in html_out
    for internal_term in ("GA4", "GSC", "Evidence:", "Interpretation:", "verifier", "deterministic", "candidate"):
        assert internal_term.lower() not in html_out.lower()
    assert "position" not in html_out.lower()
    assert "MOBILE &lt;OPPORTUNITY&gt; &amp; GROWTH" in html_out


def test_client_copy_layer_returns_only_stored_model_text(approved_full_briefing):
    unscoped = fake_discovery(
        "site-conversions",
        category="conversion",
        metric_name="conversions",
        label="configured conversions",
        value=0,
        unit="count",
    )
    scoped = fake_discovery(
        "mobile-conversions",
        category="conversion",
        metric_name="conversions",
        label="mobile configured conversions",
        value=0,
        unit="count",
    )

    audit = fake_completed_discovery_audit([unscoped, scoped])
    audit_kwargs = {
        "audit": audit,
        "client_id": "test-client",
        "period_start": CURRENT_START,
        "period_end": CURRENT_END,
    }
    unscoped_copy = build_client_discovery_copy(unscoped, **audit_kwargs)
    scoped_copy = build_client_discovery_copy(scoped, **audit_kwargs)
    assert unscoped_copy is not None and scoped_copy is not None
    assert unscoped_copy.title == unscoped.title
    assert scoped_copy.what_we_noticed == scoped.insight
    assert scoped_copy.recommended_next_step == scoped.recommended_action

    approved_full_briefing.insights.deep_discoveries = [scoped]
    approved_full_briefing.exploration_audit = fake_completed_discovery_audit([scoped])
    html_out = render_growth_email_html(approved_full_briefing)
    pdf_text = pdf_text_of(build_executive_pdf(approved_full_briefing))
    for client_surface in (html_out, pdf_text):
        assert scoped.insight in client_surface
        assert scoped.recommended_action in client_surface
        assert "Lead Submissions" in client_surface


def test_client_discovery_selection_deduplicates_and_caps_distinct_themes(approved_full_briefing):
    discoveries = [
        fake_discovery("seo-primary", category="seo", metric_name="position", label="configured service search position", value=80, unit="position", title="CONFIGURED SERVICE SEARCH VISIBILITY"),
        fake_discovery("seo-overlap", category="seo", metric_name="position", label="configured service search page position", value=70, unit="position", title="CONFIGURED SERVICE SEARCH PAGE VISIBILITY"),
        fake_discovery("seo-distinct", category="seo", metric_name="position", label="technical content topic position", value=60, unit="position", title="TECHNICAL CONTENT FOCUS"),
        fake_discovery("mobile-share", category="acquisition", metric_name="session_share", label="mobile session share", value=59, unit="percent", claim_type="share", title="MOBILE AUDIENCE FOCUS"),
    ]
    audit = fake_completed_discovery_audit(discoveries)
    copies = build_client_discovery_copies(
        discoveries,
        audit=audit,
        client_id="test-client",
        period_start=CURRENT_START,
        period_end=CURRENT_END,
    )
    assert len(copies) == 3
    assert copies[0].title == "CONFIGURED SERVICE SEARCH VISIBILITY"
    assert all(copy.title != "CONFIGURED SERVICE SEARCH PAGE VISIBILITY" for copy in copies)

    approved_full_briefing.insights.deep_discoveries = discoveries
    approved_full_briefing.exploration_audit = audit
    html_out = render_growth_email_html(approved_full_briefing)
    pdf_text = pdf_text_of(build_executive_pdf(approved_full_briefing))
    assert html_out.count("What we noticed:") == 3
    assert pdf_text.count("What we noticed:") == 3


def test_verifier_unavailable_renders_honest_empty_state(approved_full_briefing):
    discovery = fake_discovery(
        "content-unverified",
        category="content",
        metric_name="service_content",
        label="configured service content",
        value=None,
        unit="text",
        insight="MODEL INTERPRETATION MUST NOT APPEAR",
        recommended_action="MODEL ACTION MUST NOT APPEAR",
        verification_status="verified",
    )

    approved_full_briefing.insights.deep_discoveries = [discovery]
    approved_full_briefing.exploration_audit = fake_completed_discovery_audit([discovery]).model_copy(
        update={
            "status": "deterministic_only_verifier_unavailable",
            "verifier_status": "provider_error",
        }
    )
    html_out = render_growth_email_html(approved_full_briefing)
    pdf_text = pdf_text_of(build_executive_pdf(approved_full_briefing))
    for client_surface in (html_out, pdf_text):
        assert "MODEL INTERPRETATION MUST NOT APPEAR" not in client_surface
        assert "MODEL ACTION MUST NOT APPEAR" not in client_surface
        assert "No additional opportunities were identified" in client_surface


def test_average_rating_copy_is_neutral_without_evaluative_language(approved_full_briefing):
    discovery = fake_discovery(
        "rating-neutral",
        category="local",
        metric_name="average_rating",
        label="average_rating",
        value=4.2,
        unit="rating",
        insight="The configured profile rating is available as source context.",
        recommended_action="Review the configured profile context during the next cycle.",
    )
    audit = fake_completed_discovery_audit([discovery])
    copy = build_client_discovery_copy(
        discovery,
        audit=audit,
        client_id="test-client",
        period_start=CURRENT_START,
        period_end=CURRENT_END,
    )
    assert copy is not None
    assert copy.title == discovery.title
    assert copy.what_we_noticed == discovery.insight
    assert copy.recommended_next_step == discovery.recommended_action
    assert "positive" not in " ".join((copy.title, copy.what_we_noticed, copy.recommended_next_step)).lower()


def test_deep_insights_empty_state_is_client_friendly_in_html_and_pdf(approved_full_briefing):
    approved_full_briefing.exploration_audit = fake_completed_discovery_audit([])

    html_out = render_growth_email_html(approved_full_briefing)
    pdf_text = pdf_text_of(build_executive_pdf(approved_full_briefing))
    expected = "No additional opportunities were identified from this period, and no recommendations were added."
    assert expected in html_out
    assert expected in pdf_text
    assert "evidence-backed exploratory" not in html_out.lower()
    assert "evidence-backed exploratory" not in pdf_text.lower()


def test_build_executive_pdf_uses_client_discovery_copy_and_hides_audit_fields(approved_full_briefing):
    discovery = fake_discovery(
        "search-visibility",
        category="seo",
        metric_name="position",
        label="configured search position",
        value=69.3,
        unit="position",
        title="Configured Search Visibility",
        insight="Configured search visibility is an important area to develop.",
        recommended_action="Create a clearer content path for the configured search topic.",
    )
    approved_full_briefing.insights.deep_discoveries = [discovery]
    approved_full_briefing.exploration_audit = fake_completed_discovery_audit(
        approved_full_briefing.insights.deep_discoveries
    )

    pdf_text = pdf_text_of(build_executive_pdf(approved_full_briefing))
    assert "WHERE WE'RE FOCUSING NEXT" in pdf_text
    assert "What we noticed:" in pdf_text
    assert "Recommended next step:" in pdf_text
    assert "CONFIGURED SEARCH VISIBILITY" in pdf_text
    assert "69.3" not in pdf_text
    assert "configured search position" not in pdf_text.lower()
    for internal_term in ("gsc", "ga4", "evidence:", "interpretation:", "verifier", "deterministic", "candidate"):
        assert internal_term not in pdf_text.lower()

def test_build_executive_pdf(approved_full_briefing):
    pdf_bytes = build_executive_pdf(approved_full_briefing)
    assert len(pdf_bytes) > 2000
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) >= 1
    assert "Test Company" in (reader.pages[0].extract_text() or "")
    pdf_text = pdf_text_of(pdf_bytes)
    assert "EXECUTIVE OVERVIEW" in pdf_text
    assert "CUSTOMER INQUIRIES & KEY ACTIONS" in pdf_text
    assert "The configured search topic is available for future prioritization." in pdf_text
    assert "Continue monitoring the configured conversion path." in pdf_text
    assert "The current event snapshot includes the configured lead event." in pdf_text
    assert "GBP action metrics are unavailable from this connector." in pdf_text
    assert "CURRENT GOALS" in pdf_text
    assert "Improve qualified inquiries" in pdf_text
    assert "configured search topic" in pdf_text
    assert "SEARCH TERM" in pdf_text
    assert "Prepared by Vector Studios - Confidential" in pdf_text
    assert "Page 1 of" in pdf_text
    assert "\x7f" not in pdf_text


def test_monthly_pdf_limits_recommended_actions_to_three_strongest(approved_full_briefing):
    approved_full_briefing.insights.agency_action_plan = [
        ActionItem(title="Medium PDF action kept", description="Medium PDF action description.", priority="Medium"),
        ActionItem(title="Low PDF action omitted", description="Low PDF action description.", priority="Low"),
        ActionItem(title="High PDF action one", description="High PDF action description.", priority="High"),
        ActionItem(title="High PDF action two", description="High PDF action description.", priority="High"),
        ActionItem(title="Medium PDF action omitted", description="Another medium PDF action.", priority="Medium"),
    ]

    pdf_text = pdf_text_of(build_executive_pdf(approved_full_briefing))

    assert "High PDF action one" in pdf_text
    assert "High PDF action two" in pdf_text
    assert "Medium PDF action kept" in pdf_text
    assert "Low PDF action omitted" not in pdf_text
    assert "Medium PDF action omitted" not in pdf_text


def test_comparison_report_uses_28_day_title_and_empty_search_state(approved_full_briefing):
    html = render_growth_email_html(approved_full_briefing)
    assert "28-Day Performance Report" in html
    assert "Monthly Intelligence Briefing" not in html

    approved_full_briefing.analytics.striking_distance_keywords = []
    empty_search_html = render_growth_email_html(approved_full_briefing)
    assert "Search &amp; Content Topics to Validate" in empty_search_html
    assert "High-Opportunity Google Searches" not in empty_search_html

    pdf_text = pdf_text_of(build_executive_pdf(approved_full_briefing))
    assert "28-Day Performance Report" in pdf_text
    assert "SEARCH & CONTENT TOPICS TO VALIDATE" in pdf_text


def test_baseline_rendering_shows_current_values_without_prior_or_growth_deltas(approved_full_briefing):
    briefing = approved_full_briefing.model_copy(deep=True)
    baseline_end = briefing.analytics.period_end
    briefing.report_mode = ReportMode.INITIAL_BASELINE
    briefing.measurement_start_date = "2026-08-12"
    briefing.observation_window_start = "2026-08-12"
    briefing.observation_window_end = baseline_end
    briefing.period_label = (
        f"Initial Measurement Baseline (observed 2026-08-12 to {baseline_end}; "
        "measurement begins 2026-08-12)"
    )
    briefing.comparison_suppressed = True
    briefing.analytics.report_mode = ReportMode.INITIAL_BASELINE
    briefing.analytics.measurement_start_date = "2026-08-12"
    briefing.analytics.comparison_suppressed = True
    for metric in briefing.analytics.core_metrics:
        metric.prior_value = None
        metric.absolute_change = None
        metric.percentage_change = None
        metric.percentage_points_change = None
        metric.direction = "unavailable"
    for event in briefing.analytics.conversion_events:
        event.prior_count = None
        event.count_change = None
        event.percentage_change = None
        event.direction = "unavailable"

    html = render_growth_email_html(briefing)
    assert "Initial Measurement Baseline" in html
    assert "no growth deltas or prior-period values are shown" in html
    assert "28-Day Performance Report" not in html
    assert "baseline" in html
    pdf_bytes = build_executive_pdf(briefing)
    pdf_text = pdf_text_of(pdf_bytes)
    assert "Initial Measurement Baseline" in pdf_text
    assert "Current values are shown without prior-period values or growth deltas." in pdf_text

def test_resend_sender_validation():
    assert is_valid_email("test@example.com") is True
    assert is_valid_email("invalid-email") is False

def test_resend_sender_simulated_when_unconfigured():
    sender = ResendEmailSender(api_key="", from_email="")
    assert sender.is_configured is False
    with pytest.raises(RuntimeError, match="refusing to treat the report as sent"):
        sender.send_briefing(
            to_recipients=["client@example.com"],
            subject="Test Report",
            html_content="<p>Test</p>",
        )

def test_sender_with_comma_separated_recipients():
    sender = ResendEmailSender(api_key="", from_email="")
    with pytest.raises(RuntimeError, match="refusing to treat the report as sent"):
        sender.send_briefing(
            to_recipients="client1@example.com, client2@example.com",
            subject="Test Multi",
            html_content="<p>Test</p>",
            cc_recipients="agency@example.com, lead@example.com",
            idempotency_key="test-key-123",
        )


def test_is_light_color_safe_none_and_empty():
    assert is_light_color(None) is False
    assert is_light_color("") is False
    assert is_light_color("#FFFFFF") is True
    assert is_light_color("#000000") is False
    assert is_light_color("fff") is True
    assert is_light_color("000") is False
    assert is_light_color("not_a_hex") is False


def test_render_delta_chip_suppress_and_unavailable():
    metric = MetricDelta(
        metric_name="sessions",
        display_name="Total Sessions",
        current_value=1000,
        prior_value=800,
        absolute_change=200,
        percentage_change=25.0,
        direction="up",
    )
    # Normal comparison
    chip_normal = render_delta_chip(metric, "#0A0A0B", suppress_comparison=False)
    assert "&#9650;" in chip_normal
    assert "25.0%" in chip_normal
    assert "baseline" not in chip_normal

    # Suppressed comparison
    chip_suppressed = render_delta_chip(metric, "#0A0A0B", suppress_comparison=True)
    assert "baseline" in chip_suppressed
    assert "&#9650;" not in chip_suppressed
    assert "25.0%" not in chip_suppressed

    # Metric direction unavailable
    metric_unavail = metric.model_copy(update={"direction": "unavailable"})
    chip_unavail = render_delta_chip(metric_unavail, "#0A0A0B", suppress_comparison=False)
    assert "baseline" in chip_unavail
    assert "&#9650;" not in chip_unavail
    assert "25.0%" not in chip_unavail


def test_render_kpi_cells_suppress_comparison():
    metric = MetricDelta(
        metric_name="sessions",
        display_name="Total Sessions",
        current_value=1000,
        prior_value=800,
        percentage_change=25.0,
        direction="up",
    )
    cells_suppressed = render_kpi_cells([metric], "#0A0A0B", suppress_comparison=True)
    assert "baseline" in cells_suppressed
    assert "25.0%" not in cells_suppressed

    cells_normal = render_kpi_cells([metric], "#0A0A0B", suppress_comparison=False)
    assert "25.0%" in cells_normal
    assert "baseline" not in cells_normal


def test_render_bar_rows_at_and_over_100_percent():
    rows = [
        ("Peak", 100),
        ("Half", 50),
        ("Zero", 0),
    ]
    html = render_bar_rows(rows, "#1E3A8A", "#F7F4EE")
    # For 100% percent, only one <td> is emitted inside the inner table
    assert '<tr><td width="100%" bgcolor="#1E3A8A" style="background-color: #1E3A8A; height: 10px; font-size: 0; line-height: 0;">&nbsp;</td></tr>' in html
    # For 50% percent, two <td> cells are emitted
    assert '<td width="50%" bgcolor="#1E3A8A" style="background-color: #1E3A8A; height: 10px; font-size: 0; line-height: 0;">&nbsp;</td>' in html
    assert '<td style="height: 10px; font-size: 0; line-height: 0;">&nbsp;</td>' in html


def test_render_action_row_column_width_160():
    action = ActionItem(
        title="Action Title",
        description="Action description.",
        priority="medium",
    )
    row_html = render_action_row(action)
    assert 'width="160"' in row_html
    assert 'width="132"' not in row_html
    assert "Recommended Next Step" in row_html


def test_email_template_exec_list_margin_and_conversion_insights(approved_full_briefing):
    # Verify exec_list has style="margin-top: 12px;"
    html = render_growth_email_html(approved_full_briefing)
    assert 'style="margin-top: 12px;"' in html

    # Verify Section 02 renders conversion_insights even when stat_tiles is empty
    approved_full_briefing.analytics.conversion_events = []
    approved_full_briefing.insights.conversion_insights = "Important conversion commentary without stat tiles."
    html_without_tiles = render_growth_email_html(approved_full_briefing)
    assert "Customer Inquiries &amp; Key Actions" in html_without_tiles
    assert "Important conversion commentary without stat tiles." in html_without_tiles

    # Verify Section 02 is omitted when conv_body is completely empty
    approved_full_briefing.insights.conversion_insights = ""
    html_empty_conv = render_growth_email_html(approved_full_briefing)
    assert "Customer Inquiries &amp; Key Actions" not in html_empty_conv


def test_weekly_digest_suppress_comparison_and_empty_body(approved_full_briefing):
    approved_full_briefing.report_type = ReportType.WEEKLY
    approved_full_briefing.period_label = f"Weekly Period ({CURRENT_START} to {CURRENT_END})"
    approved_full_briefing.weekly_insights = WeeklyDigestOutput(
        biggest_win="Win",
        needs_attention="Attention",
        acquisition_insight="Acq",
        conversion_insight="Conv",
        search_opportunity="Search",
        local_insight="Local",
    )
    approved_full_briefing.comparison_suppressed = True
    html = render_weekly_digest_html(approved_full_briefing)
    assert "baseline" in html
    assert "Baseline period. Change comparisons begin with the next digest." in html


def test_html_tag_balance_in_rendered_templates(approved_full_briefing):
    from html.parser import HTMLParser

    class TagBalanceChecker(HTMLParser):
        VOID_TAGS = {"meta", "link", "img", "br", "hr", "input", "area", "base", "col", "embed", "param", "source", "track", "wbr"}
        def __init__(self):
            super().__init__()
            self.stack = []
            self.errors = []
        def handle_starttag(self, tag, attrs):
            if tag.lower() not in self.VOID_TAGS:
                self.stack.append(tag.lower())
        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in self.VOID_TAGS:
                return
            if not self.stack:
                self.errors.append(f"Unexpected closing tag </{tag}>")
                return
            expected = self.stack.pop()
            if expected != tag:
                self.errors.append(f"Mismatched tag: expected </{expected}>, got </{tag}>")

    # Check Growth Report HTML
    growth_html = render_growth_email_html(approved_full_briefing)
    growth_checker = TagBalanceChecker()
    growth_checker.feed(growth_html)
    assert not growth_checker.errors, f"Growth HTML has tag errors: {growth_checker.errors}"
    assert not growth_checker.stack, f"Growth HTML has unclosed tags: {growth_checker.stack}"

    # Check Weekly Digest HTML
    approved_full_briefing.report_type = ReportType.WEEKLY
    approved_full_briefing.weekly_insights = WeeklyDigestOutput(
        biggest_win="Win",
        needs_attention="Attention",
        acquisition_insight="Acq",
        conversion_insight="Conv",
        search_opportunity="Search",
        local_insight="Local",
    )
    weekly_html = render_weekly_digest_html(approved_full_briefing)
    weekly_checker = TagBalanceChecker()
    weekly_checker.feed(weekly_html)
    assert not weekly_checker.errors, f"Weekly HTML has tag errors: {weekly_checker.errors}"
    assert not weekly_checker.stack, f"Weekly HTML has unclosed tags: {weekly_checker.stack}"


def test_pdf_flow_and_dynamic_pagination(approved_full_briefing):
    # Comparison report
    comp_pdf = build_executive_pdf(approved_full_briefing)
    comp_reader = PdfReader(BytesIO(comp_pdf))
    assert len(comp_reader.pages) >= 2, f"Expected at least 2 pages for comparison PDF, got {len(comp_reader.pages)}"
    comp_full_text = "\n".join(p.extract_text() or "" for p in comp_reader.pages)
    assert "EXECUTIVE OVERVIEW" in (comp_reader.pages[0].extract_text() or "")
    assert "CUSTOMER INQUIRIES & KEY ACTIONS" in comp_full_text
    assert "WHERE VISITORS CAME FROM & WHAT THEY VIEWED" in comp_full_text
    assert "RECOMMENDED NEXT ACTIONS" in comp_full_text
    assert "CURRENT GOALS" in comp_full_text

    # Verify no page ends with an orphan section label
    for page_idx, page in enumerate(comp_reader.pages):
        lines = [line.strip() for line in (page.extract_text() or "").split("\n") if line.strip()]
        # The last line before footer should not be a section number or title
        content_lines = [l for l in lines if not l.startswith("Prepared by") and not "(c)" in l]
        if content_lines:
            assert not content_lines[-1].isdigit(), f"Page {page_idx + 1} ended with an orphan section number"

    # Baseline report
    baseline = approved_full_briefing.model_copy(deep=True)
    baseline.report_mode = ReportMode.INITIAL_BASELINE
    baseline.comparison_suppressed = True
    baseline.analytics.report_mode = ReportMode.INITIAL_BASELINE
    baseline.analytics.comparison_suppressed = True
    for metric in baseline.analytics.core_metrics:
        metric.prior_value = None
        metric.direction = "unavailable"
    for event in baseline.analytics.conversion_events:
        event.prior_count = None
        event.direction = "unavailable"

    base_pdf = build_executive_pdf(baseline)
    base_reader = PdfReader(BytesIO(base_pdf))
    assert len(base_reader.pages) >= 2, f"Expected at least 2 pages for baseline PDF, got {len(base_reader.pages)}"
    base_p1 = base_reader.pages[0].extract_text() or ""
    assert "INITIAL MEASUREMENT BASELINE" in base_p1 or "Initial Measurement Baseline" in base_p1
    assert "EXECUTIVE OVERVIEW" in base_p1

    # Expanded report with GBP data spans additional pages cleanly
    gbp_b = approved_full_briefing.model_copy(deep=True)
    gbp_b.analytics.local_seo = LocalInteractionData(
        profile_status="available",
        profile={
            "title": "Test Company",
            "primary_phone": "+1 555 0100",
            "address": {"addressLines": ["1 Test Street"], "locality": "Winter Park", "postalCode": "32789"},
            "website_uri": "https://test.example.com",
            "regular_hours": {"periods": [{"openDay": "MONDAY", "openTime": {"hours": 8}, "closeDay": "MONDAY", "closeTime": {"hours": 17}}]},
            "primary_category": {"displayName": "Dentist"},
            "services": [{"structuredServiceItem": {"description": "Teeth whitening"}}],
        },
        performance_status="available",
        performance_metrics={"CALL_CLICKS": {"total": 5, "series": []}},
        available_performance_metrics=["CALL_CLICKS"],
        search_keywords_status="available",
        monthly_search_keywords=[{"search_keyword": "dentist winter park", "insights_value": 22, "insights_value_type": "value"}],
        reviews_status="available",
        reviews=[{"star_rating": "FIVE", "reply_status": "NOT_REPLIED", "update_time": "2026-08-01T00:00:00Z", "comment": "A source-backed review comment."}],
        review_inventory_complete=True,
        review_response_summary={"review_count": 1, "unreplied_count": 1, "reply_coverage_percent": 0.0, "complete": True},
        business_calls_status="available",
        answered_calls=7,
        missed_calls=2,
    )
    gbp_pdf = build_executive_pdf(gbp_b)
    gbp_reader = PdfReader(BytesIO(gbp_pdf))
    assert len(gbp_reader.pages) >= 3, f"Expected at least 3 pages for full GBP PDF, got {len(gbp_reader.pages)}"
    gbp_full_text = "\n".join(p.extract_text() or "" for p in gbp_reader.pages)
    assert "PROFILE DETAILS" in gbp_full_text
    assert "MANAGED REVIEWS AND REPLY STATUS" in gbp_full_text
    assert "BUSINESS CALLS INSIGHTS" in gbp_full_text



def test_pdf_delta_chip_suppression_and_unavailable(approved_full_briefing):
    # Test comparison suppressed
    b = approved_full_briefing.model_copy(deep=True)
    b.comparison_suppressed = True
    pdf_text = pdf_text_of(build_executive_pdf(b))
    assert "baseline" in pdf_text

    # Test direction unavailable
    b2 = approved_full_briefing.model_copy(deep=True)
    b2.analytics.core_metrics[0].direction = "unavailable"
    pdf_text2 = pdf_text_of(build_executive_pdf(b2))
    assert "baseline" in pdf_text2

    # Test metric prior_value None
    b3 = approved_full_briefing.model_copy(deep=True)
    b3.analytics.core_metrics[0].prior_value = None
    pdf_text3 = pdf_text_of(build_executive_pdf(b3))
    assert "baseline" in pdf_text3


def test_pdf_delivery_health_empty_notes(approved_full_briefing):
    b = approved_full_briefing.model_copy(deep=True)
    b.report_delivery_metrics = ReportDeliveryMetrics(status="empty")
    b.analytics.website_inquiry_metrics = WebsiteInquiryMetrics(status="empty")

    pdf_text = pdf_text_of(build_executive_pdf(b))
    assert "No tracked delivery activity was available for this window." in pdf_text
    assert "No website inquiry-notification activity was available for this window." in pdf_text


def test_pdf_conversion_insights_ungated(approved_full_briefing):
    # Without conversion_events, conversion_insights still renders
    b = approved_full_briefing.model_copy(deep=True)
    b.analytics.conversion_events = []
    b.insights.conversion_insights = "Important conversion commentary without events."
    pdf_text = pdf_text_of(build_executive_pdf(b))
    assert "CUSTOMER INQUIRIES & KEY ACTIONS" in pdf_text
    assert "Important conversion commentary without events." in pdf_text

    # When both are empty, Section 02 is omitted
    b.insights.conversion_insights = ""
    pdf_text_empty = pdf_text_of(build_executive_pdf(b))
    assert "CUSTOMER INQUIRIES & KEY ACTIONS" not in pdf_text_empty


def test_pdf_insights_none_guards(approved_full_briefing):
    b = approved_full_briefing.model_copy(deep=True)
    b.insights.conversion_insights = None
    b.insights.seo_and_content_opportunities = None
    b.insights.local_seo_insights = None
    pdf_bytes = build_executive_pdf(b)
    assert len(pdf_bytes) > 2000


