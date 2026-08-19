import json
from unittest.mock import MagicMock
import pytest
from app.ai.analyst import GrowthAnalyst, fallback_growth_briefing, build_user_prompt
from app.analytics.contracts import (
    AIReportOutput,
    GrowthAnalysisInput,
    MetricDelta,
    ChannelPerformance,
    PagePerformance,
    StrikingDistanceKeyword,
    LocalInteractionData,
)

@pytest.fixture
def sample_growth_input():
    return GrowthAnalysisInput(
        client_id="test-client",
        company_name="Test Company",
        domain="https://test.example.com",
        industry="healthcare",
        period_start="2026-07-22",
        period_end="2026-08-18",
        comparison_start="2026-06-24",
        comparison_end="2026-07-21",
        monthly_retainer_focus="Local patient acquisition",
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
                metric_name="active_users",
                display_name="Active Users",
                current_value=950,
                prior_value=800,
                absolute_change=150,
                percentage_change=18.8,
                direction="up",
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

def test_fallback_growth_briefing(sample_growth_input):
    briefing = fallback_growth_briefing(sample_growth_input)
    assert isinstance(briefing, AIReportOutput)
    assert len(briefing.executive_summary) == 3
    assert "1,200" in briefing.executive_summary[0]
    assert "Organic Search" in briefing.executive_summary[1]
    assert len(briefing.agency_action_plan) >= 2
    assert briefing.overall_sentiment == "Growth"

def test_growth_analyst_fallback_when_no_api_key(sample_growth_input):
    analyst = GrowthAnalyst(api_key="")
    briefing = analyst.analyze(sample_growth_input)
    assert isinstance(briefing, AIReportOutput)
    assert len(briefing.executive_summary) == 3

def test_growth_analyst_with_mock_client(sample_growth_input):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "executive_summary": [
                            "Win 1: Strong 20% organic session lift.",
                            "Win 2: Direct local conversions up 38%.",
                            "Focus 3: Page 2 striking queries primed for breakthrough."
                        ],
                        "traffic_and_inflow_insights": "Organic search drove over 58% of overall traffic.",
                        "seo_and_content_opportunities": "Targeting dentist near me with dedicated FAQ will move it to rank 3.",
                        "local_seo_insights": "Direct calls grew from 18 to 25.",
                        "agency_action_plan": [
                            {
                                "title": "On-page Optimization",
                                "description": "Update headers and metadata.",
                                "impact_area": "SEO",
                                "priority": "High"
                            }
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
    briefing = analyst.analyze(sample_growth_input)
    assert briefing.executive_summary[0] == "Win 1: Strong 20% organic session lift."
    assert briefing.agency_action_plan[0].title == "On-page Optimization"
