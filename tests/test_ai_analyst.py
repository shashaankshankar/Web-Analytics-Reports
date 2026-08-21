import json
from unittest.mock import MagicMock
import pytest
from app.ai.analyst import (
    GrowthAnalyst,
    fallback_growth_briefing,
    fallback_weekly_briefing,
    build_weekly_user_prompt,
    build_performance_user_prompt,
)
from app.analytics.contracts import (
    AIReportOutput,
    ChannelPerformance,
    GrowthAnalysisInput,
    LocalInteractionData,
    MetricDelta,
    PagePerformance,
    ReportType,
    StrikingDistanceKeyword,
    WeeklyDigestOutput,
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

def test_fallback_growth_briefing():
    sample_growth_input = make_growth_input()
    briefing = fallback_growth_briefing(sample_growth_input)
    assert isinstance(briefing, AIReportOutput)
    assert len(briefing.executive_summary) == 3
    assert "1,200" in briefing.executive_summary[0]
    assert "Organic Search" in briefing.executive_summary[1]
    assert briefing.biggest_win != ""
    assert len(briefing.agency_action_plan) >= 2
    assert briefing.overall_sentiment == "Growth"

def test_fallback_weekly_briefing():
    sample_growth_input = make_growth_input(report_type=ReportType.WEEKLY, period_days=7)
    weekly = fallback_weekly_briefing(sample_growth_input)
    assert isinstance(weekly, WeeklyDigestOutput)
    assert "1,200" in weekly.biggest_win
    assert "Organic Search" in weekly.acquisition_insight
    assert len(weekly.next_actions) <= 2
    assert weekly.overall_sentiment == "Growth"

def test_growth_analyst_fallback_when_no_api_key():
    sample_growth_input = make_growth_input()
    analyst = GrowthAnalyst(api_key="")
    briefing = analyst.analyze(sample_growth_input)
    assert isinstance(briefing, AIReportOutput)
    assert len(briefing.executive_summary) == 3


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

def test_growth_analyst_weekly_with_mock_client():
    sample_growth_input = make_growth_input(report_type=ReportType.WEEKLY, period_days=7)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "biggest_win": "Organic sessions grew 20% to 1,200.",
                        "needs_attention": None,
                        "acquisition_insight": "Search remains the primary qualified driver.",
                        "search_opportunity": "Target dentist near me to capture top-3 map pack.",
                        "local_insight": "Direct calls rose to 25.",
                        "next_actions": [
                            {"title": "Header optimization", "description": "Update H2 tags.", "impact_area": "SEO", "priority": "High", "evidence": "Pos 11.2 rank"}
                        ],
                        "overall_sentiment": "Growth"
                    })
                }
            }
        ]
    }
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response
    analyst = GrowthAnalyst(api_key="sk-test-key", http_client=mock_http)
    weekly = analyst.analyze_weekly(sample_growth_input)
    assert weekly.biggest_win == "Organic sessions grew 20% to 1,200."
    assert len(weekly.next_actions) == 1

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
                        "overall_sentiment": "Growth"
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
