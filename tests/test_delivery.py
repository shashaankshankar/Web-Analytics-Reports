from io import BytesIO
from pypdf import PdfReader
import pytest
from app.analytics.contracts import (
    ActionItem,
    LocalInteractionData,
    MetricDelta,
    ReportMode,
    ReportType,
    WeeklyDigestOutput,
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
    assert "font-size: 14px; color: #45464D; line-height: 1.6; background: #F7F9FB" in html
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
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(build_executive_pdf(approved_full_briefing))).pages
    )
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
    assert "Week at a Glance" in html
    assert "The current weekly source snapshot is available." in html
    assert "The configured acquisition channels are available." in html
    assert "The current snapshot recorded one consultation inquiry" in html
    assert "Customer Inquiries &amp; Key Actions" in html
    assert "Area to Improve" in html
    assert "Recommended Next Actions" not in html
    assert "Review Configured Page Data" not in html
    assert "Current Goals" in html
    assert "Improve qualified inquiries" in html
    assert ".weekly-brand-badge" in html


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
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(build_executive_pdf(approved_full_briefing))).pages
    )
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
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(build_executive_pdf(approved_full_briefing))).pages
    )
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
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(build_executive_pdf(approved_full_briefing))).pages
    )
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
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(build_executive_pdf(approved_full_briefing))).pages
    )
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

    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(build_executive_pdf(approved_full_briefing))).pages
    )
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
    text = reader.pages[0].extract_text()
    assert "Test Company" in text
    assert "EXECUTIVE SNAPSHOT" in text
    assert "KEY INQUIRY ACTIONS" in text
    pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "The configured search topic is available for future prioritization." in pdf_text
    assert "Continue monitoring the configured conversion path." in pdf_text
    assert "The current event snapshot includes the configured lead event." in pdf_text
    assert "GBP action metrics are unavailable from this connector." in pdf_text
    assert "CURRENT GOALS" in pdf_text
    assert "Improve qualified inquiries" in pdf_text
    assert "configured search topic" in pdf_text
    assert "Search Term" in pdf_text
    assert "Executive Insights | Test Company" in pdf_text
    assert "\x7f" not in pdf_text


def test_monthly_pdf_limits_recommended_actions_to_three_strongest(approved_full_briefing):
    approved_full_briefing.insights.agency_action_plan = [
        ActionItem(title="Medium PDF action kept", description="Medium PDF action description.", priority="Medium"),
        ActionItem(title="Low PDF action omitted", description="Low PDF action description.", priority="Low"),
        ActionItem(title="High PDF action one", description="High PDF action description.", priority="High"),
        ActionItem(title="High PDF action two", description="High PDF action description.", priority="High"),
        ActionItem(title="Medium PDF action omitted", description="Another medium PDF action.", priority="Medium"),
    ]

    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(build_executive_pdf(approved_full_briefing))).pages
    )

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

    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(build_executive_pdf(approved_full_briefing))).pages
    )
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
    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf_bytes)).pages)
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
