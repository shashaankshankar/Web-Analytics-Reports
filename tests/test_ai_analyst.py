import json

import pytest
from unittest.mock import MagicMock

from app.ai.analyst import (
    AnalysisUnavailableError,
    GrowthAnalyst,
    build_performance_user_prompt,
    build_weekly_user_prompt,
)
from app.analytics.contracts import (
    ChannelPerformance,
    GrowthAnalysisInput,
    LocalInteractionData,
    MetricDelta,
    PagePerformance,
    ReportType,
    StrikingDistanceKeyword,
)


def make_growth_input(report_type: ReportType = ReportType.PERFORMANCE_28D, period_days: int = 28):
    return GrowthAnalysisInput(
        client_id="test-client",
        company_name="Test Company",
        domain="https://test.example.com",
        industry="healthcare",
        report_type=report_type,
        period_days=period_days,
        period_start="2026-07-22" if period_days == 28 else "2026-08-12",
        period_end="2026-08-18",
        comparison_start="2026-06-24" if period_days == 28 else "2026-08-05",
        comparison_end="2026-07-21" if period_days == 28 else "2026-08-11",
        goals=["Local patient acquisition", "Improve consultation inquiries"],
        core_metrics=[
            MetricDelta(
                metric_name="sessions",
                display_name="Total Sessions",
                current_value=1200,
                prior_value=1000,
                absolute_change=200,
                percentage_change=20.0,
                direction="up",
            ),
            MetricDelta(
                metric_name="conversion_rate",
                display_name="Conversion Rate",
                current_value=3.5,
                prior_value=3.0,
                absolute_change=0.5,
                percentage_points_change=0.5,
                is_percentage_rate=True,
                direction="up",
                unit="percentage",
            ),
        ],
        top_channels=[
            ChannelPerformance(
                channel="Organic Search",
                sessions=700,
                active_users=550,
                prior_sessions=600,
                session_change=100,
                percentage_change=16.7,
            )
        ],
        top_pages=[
            PagePerformance(
                page_path="/contact",
                sessions=200,
                active_users=180,
                prior_sessions=150,
                session_change=50,
                is_high_intent=True,
            )
        ],
        striking_distance_keywords=[
            StrikingDistanceKeyword(
                query="dentist near me",
                impressions=850,
                clicks=12,
                ctr=1.41,
                position=11.2,
                opportunity_score=8330.0,
            )
        ],
        local_seo=LocalInteractionData(
            phone_calls=25,
            prior_phone_calls=18,
            direction_requests=40,
            prior_direction_requests=30,
            average_rating=4.9,
        ),
    )


def test_growth_analyst_refuses_to_fabricate_when_provider_unavailable():
    with pytest.raises(AnalysisUnavailableError, match="credentials are unavailable"):
        GrowthAnalyst(api_key="").analyze(make_growth_input())


def test_analyst_prompts_propagate_goals_as_json_arrays():
    sample_growth_input = make_growth_input()

    weekly_prompt = build_weekly_user_prompt(sample_growth_input)
    weekly_dataset = weekly_prompt.split("Dataset:\n", 1)[1].split("\n\nInstructions:", 1)[0]
    weekly_payload = json.loads(weekly_dataset)

    performance_prompt = build_performance_user_prompt(sample_growth_input)
    performance_dataset = performance_prompt.split("Dataset:\n", 1)[1].split("\n\nImportant Guidelines:", 1)[0]
    performance_payload = json.loads(performance_dataset)

    expected_goals = ["Local patient acquisition", "Improve consultation inquiries"]
    assert weekly_payload["client_profile"]["goals"] == expected_goals
    assert performance_payload["client_profile"]["goals"] == expected_goals
    assert "monthly_retainer_focus" not in weekly_prompt
    assert "monthly_retainer_focus" not in performance_prompt
    assert "raw_summary_stats" in performance_payload
    assert "Key Conversions" in build_weekly_user_prompt(sample_growth_input)
    assert "small sample" in build_performance_user_prompt(sample_growth_input).lower()


def test_baseline_performance_prompt_suppresses_prior_period_and_names_observed_window():
    payload = make_growth_input().model_dump()
    payload.update(
        {
            "report_mode": "initial_baseline",
            "period_start": "2026-08-12",
            "period_end": "2026-08-20",
            "measurement_start_date": "2026-08-12",
            "observed_days": 9,
            "comparison_suppressed": True,
            "comparison_suppression_reason": "The comparison period is before measurement began.",
        }
    )
    baseline_input = GrowthAnalysisInput(**payload)
    prompt = build_performance_user_prompt(baseline_input)
    dataset = json.loads(prompt.split("Dataset:\n", 1)[1].split("\n\nImportant Guidelines:", 1)[0])
    assert dataset["period"]["mode"] == "initial_baseline"
    assert dataset["period"]["prior"] is None
    assert "initial measurement baseline" in prompt.lower()
    assert "do not mention prior periods" in prompt.lower()


def test_baseline_analyst_rejects_movement_language():
    payload = make_growth_input().model_dump()
    payload.update(
        {
            "report_mode": "initial_baseline",
            "period_start": "2026-08-12",
            "period_end": "2026-08-20",
            "measurement_start_date": "2026-08-12",
            "observed_days": 9,
            "comparison_suppressed": True,
        }
    )
    baseline_input = GrowthAnalysisInput(**payload)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({
            "executive_summary": ["Sessions increased during the observation window.", "Current channels are available.", "Current events are available."],
            "biggest_win": "Current data is available.",
            "watch_item": None,
            "traffic_and_inflow_insights": "Current traffic is available.",
            "conversion_insights": "Current events are available.",
            "seo_and_content_opportunities": "Current search data is available.",
            "local_seo_insights": "GBP action metrics are unavailable.",
            "agency_action_plan": [],
            "overall_sentiment": "Moderate",
        })}}]
    }
    with pytest.raises(AnalysisUnavailableError, match="comparison or movement"):
        GrowthAnalyst(api_key="sk-test", http_client=MagicMock(post=MagicMock(return_value=mock_response))).analyze(baseline_input)


def test_growth_analyst_weekly_with_provider_response():
    sample_growth_input = make_growth_input(report_type=ReportType.WEEKLY, period_days=7)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "biggest_win": "The configured acquisition snapshot is available.",
                        "needs_attention": None,
                        "acquisition_insight": "Search remains the primary configured channel.",
                        "conversion_insight": "Recorded customer actions are available in the current snapshot.",
                        "search_opportunity": None,
                        "local_insight": "GBP action metrics are unavailable from this connector.",
                        "next_actions": [
                            {"title": "Header optimization", "description": "Update H2 tags.", "impact_area": "SEO", "priority": "High", "evidence": "Configured page data"}
                        ],
                        "overall_sentiment": "Growth",
                    })
                }
            }
        ]
    }
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response
    weekly = GrowthAnalyst(api_key="sk-test-key", http_client=mock_http).analyze_weekly(sample_growth_input)
    assert weekly.biggest_win == "The configured acquisition snapshot is available."
    assert len(weekly.next_actions) == 1
    response_format = mock_http.post.call_args.kwargs["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "weekly_digest"


def test_growth_analyst_openrouter_payload():
    sample_growth_input = make_growth_input()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "executive_summary": ["Point 1", "Point 2", "Point 3"],
                        "biggest_win": "Win 1",
                        "watch_item": "Watch 1",
                        "traffic_and_inflow_insights": "Traffic info",
                        "conversion_insights": "Conversion info",
                        "seo_and_content_opportunities": "SEO info",
                        "local_seo_insights": "Local info",
                        "agency_action_plan": [
                            {"title": "Action 1", "description": "Desc", "impact_area": "SEO", "priority": "High", "evidence": "Ev"}
                        ],
                        "overall_sentiment": "Growth",
                    })
                }
            }
        ]
    }
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response
    analyst = GrowthAnalyst(
        api_key="sk-or-v1-test",
        model="openai/gpt-5.6-luna",
        reasoning_effort="medium",
        base_url="https://openrouter.ai/api/v1",
        http_client=mock_http,
    )
    briefing = analyst.analyze(sample_growth_input)
    assert briefing.executive_summary[0] == "Point 1"
    assert briefing.biggest_win == "Win 1"
    called_url, kwargs = mock_http.post.call_args
    assert called_url[0] == "https://openrouter.ai/api/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-or-v1-test"
    assert kwargs["json"]["model"] == "openai/gpt-5.6-luna"
    assert kwargs["json"]["reasoning"] == {"effort": "medium"}
    response_format = kwargs["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "performance_report"
