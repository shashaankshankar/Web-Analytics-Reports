from datetime import date
import pytest
from pydantic import ValidationError
from app.analytics.contracts import (
    GrowthAnalysisInput,
    MetricDelta,
    ReportMode,
    ReportType,
    SourceAvailability,
    StrikingDistanceKeyword,
)
from app.analytics.metrics import (
    calculate_percentage_change,
    determine_direction,
    calculate_date_ranges,
    filter_striking_distance_keywords,
    calculate_search_movers,
    normalize_conversion_events,
    classify_ga4_events,
    aggregate_growth_metrics,
    derive_form_progression_and_abandonment,
)
from app.config import ClientConfig
from app.analytics.periods import ReportWindowError, select_report_window

def test_growth_analysis_input_rejects_removed_monthly_focus():
    with pytest.raises(ValidationError, match="monthly_retainer_focus"):
        GrowthAnalysisInput(
            client_id="legacy-goals",
            company_name="Legacy Goals Co",
            domain="https://example.com",
            industry="general",
            period_start="2026-07-22",
            period_end="2026-08-18",
            comparison_start="2026-06-24",
            comparison_end="2026-07-21",
            monthly_retainer_focus="Legacy focus",
        )

def test_growth_analysis_input_still_ignores_unrelated_extra_fields():
    growth_input = GrowthAnalysisInput(
        client_id="extra-fields",
        company_name="Extra Fields Co",
        domain="https://example.com",
        industry="general",
        period_start="2026-07-22",
        period_end="2026-08-18",
        comparison_start="2026-06-24",
        comparison_end="2026-07-21",
        unrelated_field="ignored",
    )
    assert not hasattr(growth_input, "unrelated_field")

def test_calculate_percentage_change():
    assert calculate_percentage_change(120, 100) == 20.0
    assert calculate_percentage_change(80, 100) == -20.0
    assert calculate_percentage_change(100, 100) == 0.0
    assert calculate_percentage_change(100, 0) is None  # Zero prior edge case

def test_determine_direction():
    assert determine_direction(105, 100) == "up"
    assert determine_direction(95, 100) == "down"
    assert determine_direction(100, 100) == "flat"

def test_calculate_date_ranges_28d():
    fixed_today = date(2026, 8, 19)
    start, end, prior_start, prior_end = calculate_date_ranges(days=28, today=fixed_today)
    assert end == "2026-08-18"
    assert start == "2026-07-22"
    assert prior_end == "2026-07-21"
    assert prior_start == "2026-06-24"

def test_calculate_date_ranges_7d():
    fixed_today = date(2026, 8, 19)
    start, end, prior_start, prior_end = calculate_date_ranges(days=7, today=fixed_today)
    assert end == "2026-08-18"
    assert start == "2026-08-12"
    assert prior_end == "2026-08-11"
    assert prior_start == "2026-08-05"


def test_report_window_fails_when_requested_current_period_predates_measurement_start():
    with pytest.raises(ReportWindowError, match="ends before measurement_start_date"):
        select_report_window(
            "2026-07-22",
            "2026-08-18",
            "2026-06-24",
            "2026-07-21",
            measurement_start_date="2026-08-19",
        )


def test_report_window_selects_initial_baseline_for_pre_measurement_comparison():
    plan = select_report_window(
        "2026-07-24",
        "2026-08-20",
        "2026-06-26",
        "2026-07-23",
        measurement_start_date="2026-08-12",
        current_covered=True,
    )
    assert plan.mode == ReportMode.INITIAL_BASELINE
    assert plan.observation_start == "2026-08-12"
    assert plan.observation_end == "2026-08-20"
    assert plan.comparison_suppressed is True


def test_report_window_selects_normal_comparison_after_full_post_measurement_windows():
    plan = select_report_window(
        "2026-08-26",
        "2026-09-22",
        "2026-07-29",
        "2026-08-25",
        measurement_start_date="2026-07-01",
        current_covered=True,
        comparison_covered=True,
    )
    assert plan.mode == ReportMode.COMPARISON
    assert plan.observation_start == "2026-08-26"
    assert plan.comparison_suppressed is False

def test_filter_striking_distance_keywords():
    raw_queries = [
        {"query": "page 1 top", "position": 2.1, "impressions": 1000, "clicks": 200, "ctr": 0.2},
        {"query": "striking keyword high imp", "position": 11.5, "impressions": 1200, "clicks": 20, "ctr": 0.016},
        {"query": "striking keyword low imp", "position": 14.0, "impressions": 5, "clicks": 0, "ctr": 0.0},  # filtered by imp < 10
        {"query": "page 3 deep", "position": 25.0, "impressions": 800, "clicks": 0, "ctr": 0.0},  # filtered by pos > 20
        {"query": "striking keyword medium imp", "position": 9.2, "impressions": 600, "clicks": 15, "ctr": 0.025},
    ]
    results = filter_striking_distance_keywords(raw_queries, min_position=8.0, max_position=20.0, min_impressions=10)
    assert len(results) == 2
    assert results[0].query == "striking keyword high imp"
    assert results[0].opportunity_score > results[1].opportunity_score

def test_calculate_search_movers():
    current_q = [
        {"query": "invisalign dentist", "position": 6.2, "impressions": 500, "clicks": 25},
        {"query": "teeth whitening cost", "position": 12.0, "impressions": 300, "clicks": 10},
    ]
    prior_q = [
        {"query": "invisalign dentist", "position": 9.5, "impressions": 420, "clicks": 15},
        {"query": "teeth whitening cost", "position": 11.8, "impressions": 280, "clicks": 9},
    ]
    movers = calculate_search_movers(current_q, prior_q)
    assert len(movers) == 2
    top_mover = movers[0]
    assert top_mover.query == "invisalign dentist"
    assert top_mover.position_change == -3.3  # position improved from 9.5 to 6.2
    assert top_mover.mover_type == "ranking_gain"


@pytest.mark.parametrize(
    "prior_status,prior_queries,prior_truncated",
    [
        (SourceAvailability.EMPTY, [], False),
        (SourceAvailability.UNAVAILABLE, [], False),
        (None, None, False),
        (SourceAvailability.AVAILABLE, [], True),
    ],
)
def test_search_movers_suppress_missing_empty_unavailable_or_truncated_prior(
    prior_status, prior_queries, prior_truncated
):
    current = [{"query": "dentist near me", "position": 11.0, "impressions": 100, "clicks": 4}]
    assert calculate_search_movers(
        current,
        prior_queries,
        prior_status=prior_status,
        prior_truncated=prior_truncated,
    ) == []


def test_search_movers_do_not_treat_absent_prior_query_as_zero_baseline():
    current = [{"query": "new query", "position": 12.0, "impressions": 100, "clicks": 4}]
    prior = [{"query": "different query", "position": 4.0, "impressions": 100, "clicks": 4}]
    assert calculate_search_movers(
        current,
        prior,
        prior_status=SourceAvailability.AVAILABLE,
    ) == []

def test_normalize_conversion_events():
    curr_events = {"generate_lead": 18, "phone_click": 12}
    prior_events = {"generate_lead": 15, "phone_click": 10}
    events = normalize_conversion_events(curr_events, prior_events)
    assert len(events) == 1
    lead_ev = next(e for e in events if e.event_name == "generate_lead")
    assert lead_ev.display_name == "Primary Leads"
    assert lead_ev.count_change == 3
    assert lead_ev.percentage_change == 20.0
    assert lead_ev.direction == "up"


def test_normalize_conversion_events_keeps_prior_only_events_for_comparisons():
    groups = classify_ga4_events(
        {"generate_lead": 0},
        {"generate_lead": 3, "appointment_request": 2},
    )
    assert [event.event_name for event in groups["primary_leads"]] == ["generate_lead"]
    appointment = next(event for event in groups["customer_actions"] if event.event_name == "appointment_request")
    assert appointment.current_count == 0
    assert appointment.prior_count == 2
    assert appointment.count_change == -2
    assert appointment.direction == "down"


def test_classify_ga4_events_excludes_automatic_events_from_action_groups():
    groups = classify_ga4_events({
        "generate_lead": 2,
        "phone_click": 3,
        "form_start": 4,
        "page_view": 100,
        "scroll": 20,
        "user_engagement": 40,
    })
    assert [item.event_name for item in groups["primary_leads"]] == ["generate_lead"]
    assert [item.event_name for item in groups["customer_actions"]] == ["phone_click"]
    assert [item.event_name for item in groups["funnel_activity"]] == ["form_start"]
    assert "page_view" in groups["automatic_events_excluded"]
    assert "page_view" not in {item.event_name for item in groups["customer_actions"]}

def test_aggregate_growth_metrics():
    client = ClientConfig(
        client_id="acme",
        company_name="Acme Corp",
        domain="https://acme.example.com",
        industry="b2b_saas",
        goals=["Increase qualified traffic", "Improve signup conversion"],
    )
    ga4_data = {
        "summary": {"activeUsers": 500, "sessions": 700, "engagementRate": 0.65, "bounceRate": 0.35, "conversions": 28},
        "prior_summary": {"activeUsers": 400, "sessions": 550, "engagementRate": 0.60, "bounceRate": 0.40, "conversions": 20},
        "channels": [{"channel": "Organic Search", "sessions": 450, "activeUsers": 350, "conversions": 18, "priorSessions": 350, "sessionChange": 100}],
        "pages": [{"pagePath": "/signup", "sessions": 120, "activeUsers": 100, "priorSessions": 90, "sessionChange": 30}],
        "events": {"generate_lead": 28},
        "prior_events": {"generate_lead": 20},
    }
    gsc_queries = [
        {"query": "b2b saas tool", "position": 12.0, "impressions": 800, "clicks": 10, "ctr": 0.012},
    ]
    gbp_data = {
        "phone_calls": 5, "prior_phone_calls": 3,
        "direction_requests": 0, "prior_direction_requests": 0,
        "website_clicks": 20, "prior_website_clicks": 15,
    }
    growth_input = aggregate_growth_metrics(
        client=client,
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        ga4_data=ga4_data,
        gsc_queries=gsc_queries,
        gbp_data=gbp_data,
        report_type=ReportType.PERFORMANCE_28D,
        period_days=28,
    )
    assert isinstance(growth_input, GrowthAnalysisInput)
    assert len(growth_input.core_metrics) == 5
    sess_metric = next(m for m in growth_input.core_metrics if m.metric_name == "sessions")
    assert sess_metric.current_value == 700
    assert sess_metric.prior_value == 550
    assert sess_metric.direction == "up"
    cr_metric = next(m for m in growth_input.core_metrics if m.metric_name == "conversion_rate")
    assert cr_metric.current_value == 4.0  # 28 / 700 = 4.0%
    assert cr_metric.prior_value == 3.64  # 20 / 550 = 3.64%
    assert cr_metric.direction == "up"
    eng_metric = next(m for m in growth_input.core_metrics if m.metric_name == "engagement_rate")
    assert eng_metric.percentage_points_change == 5.0
    assert eng_metric.is_percentage_rate is True
    assert growth_input.top_pages[0].is_high_intent is True
    assert growth_input.local_seo.phone_calls_change == 2
    assert growth_input.local_seo.phone_calls_direction == "up"
    assert growth_input.goals == ["Increase qualified traffic", "Improve signup conversion"]


def test_aggregate_growth_metrics_includes_gbp_profile_performance_keywords_reviews_and_calls():
    client = ClientConfig(
        client_id="gbp-contract",
        company_name="GBP Contract Co",
        domain="https://gbp-contract.example.com",
    )
    growth_input = aggregate_growth_metrics(
        client=client,
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        ga4_data={
            "summary": {"activeUsers": 1, "sessions": 2, "engagementRate": 0.5, "conversions": 1},
            "prior_summary": {"activeUsers": 1, "sessions": 1, "engagementRate": 0.5, "conversions": 1},
        },
        gsc_queries=[],
        gbp_data={
            "profile_status": "available",
            "profile_summary": {
                "title": "GBP Contract Co",
                "primary_phone": "+1 555 0100",
                "address": {"locality": "Winter Park"},
                "regular_hours": {"periods": []},
                "primary_category": {"displayName": "Dentist"},
                "services": [{"structuredServiceItem": {"description": "Whitening"}}],
            },
            "performance_status": "available",
            "performance_metrics": {
                "CALL_CLICKS": {"total": 5, "series": []},
                "BUSINESS_CONVERSATIONS": {"total": 3, "series": []},
            },
            "prior_performance_metrics": {
                "CALL_CLICKS": {"total": 2, "series": []},
                "BUSINESS_CONVERSATIONS": {"total": 4, "series": []},
            },
            "available_performance_metrics": ["BUSINESS_CONVERSATIONS", "CALL_CLICKS"],
            "search_keywords_status": "available",
            "monthly_search_keywords": [{
                "search_keyword": "dentist winter park",
                "insights_value": 22,
                "insights_value_type": "value",
            }],
            "reviews_status": "available",
            "reviews": [{"review_id": "r1", "reply_status": "NOT_REPLIED", "comment": "Review"}],
            "review_inventory_complete": True,
            "review_response_summary": {
                "review_count": 1,
                "unreplied_count": 1,
                "reply_coverage_percent": 0.0,
                "complete": True,
            },
            "business_calls_status": "available",
            "business_calls": {"aggregate_metrics": {"answered_calls": 7, "missed_calls": 2}},
            "answered_calls": 7,
            "prior_answered_calls": 5,
            "missed_calls": 2,
            "prior_missed_calls": 3,
        },
    )

    assert growth_input.local_seo.profile["primary_phone"] == "+1 555 0100"
    assert {metric.metric_name for metric in growth_input.local_seo.performance_metric_deltas} == {
        "gbp_business_conversations",
        "gbp_call_clicks",
    }
    clicks = next(metric for metric in growth_input.local_seo.performance_metric_deltas if metric.metric_name == "gbp_call_clicks")
    assert clicks.current_value == 5
    assert clicks.prior_value == 2
    assert clicks.absolute_change == 3
    assert growth_input.local_seo.answered_calls_change == 2
    assert growth_input.local_seo.missed_calls_change == -1
    assert growth_input.local_seo.monthly_search_keywords[0]["search_keyword"] == "dentist winter park"
    assert growth_input.local_seo.review_inventory_complete is True
    assert growth_input.local_seo.review_response_summary["unreplied_count"] == 1
    assert growth_input.local_seo.business_calls["aggregate_metrics"]["answered_calls"] == 7


def test_aggregate_growth_metrics_preserves_unavailable_search_comparison():
    client = ClientConfig(
        client_id="search-state",
        company_name="Search State Co",
        domain="https://search-state.example.com",
    )
    growth_input = aggregate_growth_metrics(
        client=client,
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        ga4_data={
            "summary": {"activeUsers": 1, "sessions": 2, "engagementRate": 0.5, "bounceRate": 0.5, "conversions": 1},
            "prior_summary": {"activeUsers": 1, "sessions": 1, "engagementRate": 0.5, "bounceRate": 0.5, "conversions": 1},
        },
        gsc_queries=[{"query": "dentist near me", "position": 11.0, "impressions": 100, "clicks": 4, "ctr": 0.04}],
        prior_gsc_queries=None,
        prior_gsc_status=SourceAvailability.UNAVAILABLE,
        gbp_data={},
    )
    assert growth_input.search_comparison_status == SourceAvailability.UNAVAILABLE
    assert growth_input.search_movers == []
    assert growth_input.search_comparison_diagnostics


def test_aggregate_growth_metrics_initial_baseline_has_no_prior_values_or_deltas():
    client = ClientConfig(
        client_id="baseline-client",
        company_name="Baseline Client",
        domain="https://baseline.example.com",
        measurement_start_date="2026-08-12",
    )
    growth_input = aggregate_growth_metrics(
        client=client,
        start_date="2026-08-12",
        end_date="2026-08-20",
        prior_start_date="2026-06-26",
        prior_end_date="2026-07-23",
        ga4_data={
            "summary": {
                "activeUsers": 11,
                "sessions": 19,
                "engagementRate": 0.5,
                "bounceRate": 0.5,
                "conversions": 2,
            },
            "prior_summary": {},
            "channels": [{"channel": "Organic Search", "sessions": 10, "activeUsers": 8, "conversions": 1}],
            "pages": [{"pagePath": "/", "sessions": 19, "activeUsers": 11}],
            "events": {"appointment_request": 2},
            "prior_events": {},
        },
        gsc_queries=[{"query": "dentist winter park", "position": 11.0, "impressions": 20, "clicks": 2, "ctr": 0.1}],
        prior_gsc_queries=None,
        gbp_data={},
        report_mode=ReportMode.INITIAL_BASELINE,
        measurement_start_date="2026-08-12",
        comparison_suppressed=True,
        comparison_suppression_reason="The comparison period is before measurement began.",
    )
    assert growth_input.report_mode == ReportMode.INITIAL_BASELINE
    assert all(metric.prior_value is None and metric.absolute_change is None for metric in growth_input.core_metrics)
    assert growth_input.top_channels[0].prior_sessions is None
    assert growth_input.top_channels[0].session_change is None
    assert growth_input.top_pages[0].prior_sessions is None
    assert growth_input.top_pages[0].session_change is None
    assert growth_input.conversion_events[0].prior_count is None
    assert growth_input.conversion_events[0].count_change is None
    assert growth_input.page_gainers == []
    assert growth_input.page_decliners == []
    assert growth_input.search_movers == []
    assert growth_input.comparison_suppressed is True


def test_form_progression_and_abandonment_derivation():
    current_events = {
        "form_start": 100,
        "form_step_1": 80,
        "form_step_2": 50,
        "form_step_3": 30,
        "form_submit": 20,
    }
    prior_events = {
        "form_start": 90,
        "form_step_1": 70,
        "form_step_2": 40,
        "form_step_3": 25,
        "form_submit": 15,
    }
    abandonment = derive_form_progression_and_abandonment(current_events, prior_events)
    assert abandonment is not None
    assert abandonment["total_starts"] == 100
    assert abandonment["total_completions"] == 20
    assert abandonment["overall_completion_rate"] == 20.0
    assert abandonment["overall_abandonment_rate"] == 80.0

    steps = abandonment["steps"]
    assert len(steps) == 5
    # Step 0: form_start -> 100, dropoff 20, dropoff_rate 20.0%
    assert steps[0]["step_id"] == "form_start"
    assert steps[0]["current_count"] == 100
    assert steps[0]["dropoff_count"] == 20
    assert steps[0]["dropoff_rate"] == 20.0
    assert steps[0]["progression_rate"] == 80.0

    # Step 1: form_step_1 -> 80, dropoff 30, dropoff_rate 37.5%
    assert steps[1]["step_id"] == "form_step_1"
    assert steps[1]["current_count"] == 80
    assert steps[1]["dropoff_count"] == 30
    assert steps[1]["dropoff_rate"] == 37.5
    assert steps[1]["progression_rate"] == 62.5

    # Step 2: form_step_2 -> 50, dropoff 20, dropoff_rate 40.0%
    assert steps[2]["step_id"] == "form_step_2"
    assert steps[2]["current_count"] == 50
    assert steps[2]["dropoff_count"] == 20
    assert steps[2]["dropoff_rate"] == 40.0

    # Step 3: form_step_3 -> 30, dropoff 10, dropoff_rate 33.33%
    assert steps[3]["step_id"] == "form_step_3"
    assert steps[3]["current_count"] == 30
    assert steps[3]["dropoff_count"] == 10

    # Step 4: form_submit -> 20, dropoff None
    assert steps[4]["step_id"] == "form_submit"
    assert steps[4]["current_count"] == 20
    assert steps[4]["dropoff_count"] is None

    # Highest dropoff count was Step 1 -> Step 2 (30 users dropped off)
    assert abandonment["highest_dropoff_stage"] == "Step 1 -> Step 2"
    assert abandonment["dropoff_by_final_step"] == {
        "form_start": 20,
        "form_step_1": 30,
        "form_step_2": 20,
        "form_step_3": 10,
    }


def test_aggregate_growth_metrics_includes_abandonment_summary():
    client = ClientConfig(
        client_id="acme",
        company_name="Acme Corp",
        domain="https://acme.example.com",
        industry="healthcare",
        goals=["Improve conversion"],
    )
    ga4_data = {
        "summary": {"activeUsers": 500, "sessions": 700, "engagementRate": 0.65, "bounceRate": 0.35, "conversions": 20},
        "prior_summary": {"activeUsers": 400, "sessions": 550, "engagementRate": 0.60, "bounceRate": 0.40, "conversions": 15},
        "channels": [],
        "pages": [],
        "events": {
            "form_start": 100,
            "form_step_1": 75,
            "form_step_2": 50,
            "form_step_3": 35,
            "generate_lead": 20,
        },
        "prior_events": {
            "form_start": 80,
            "form_step_1": 60,
            "form_step_2": 40,
            "form_step_3": 25,
            "generate_lead": 15,
        },
    }
    growth_input = aggregate_growth_metrics(
        client=client,
        start_date="2026-08-01",
        end_date="2026-08-28",
        prior_start_date="2026-07-04",
        prior_end_date="2026-07-31",
        ga4_data=ga4_data,
        gsc_queries=[],
        gbp_data={},
    )
    assert growth_input.abandonment_summary is not None
    assert growth_input.abandonment_summary["total_starts"] == 100
    assert growth_input.abandonment_summary["total_completions"] == 20
    assert growth_input.raw_summary_stats["abandonment_summary"] == growth_input.abandonment_summary
    # Funnel activity contains step events
    step_events = {item.event_name for item in growth_input.funnel_activity}
    assert "form_start" in step_events
    assert "form_step_1" in step_events
    assert "form_step_2" in step_events
    assert "form_step_3" in step_events

