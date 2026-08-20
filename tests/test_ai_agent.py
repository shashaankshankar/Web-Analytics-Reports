import json
from unittest.mock import MagicMock
import pytest

from app.ai.agent import ExploratoryGrowthAgent, fallback_discoveries
from app.ai.tools import MultiSourceAnalyticsToolkit
from app.analytics.contracts import (
    DataDiscovery,
    GrowthAnalysisInput,
    LocalInteractionData,
    MetricDelta,
)
from app.config import ClientConfig


@pytest.fixture
def sample_client():
    return ClientConfig(
        client_id="sample-dental",
        company_name="Sample Dental Clinic",
        domain="https://sample.dental",
        industry="dental",
        monthly_retainer_focus="Cosmetic dentistry & local implants",
    )


@pytest.fixture
def sample_input():
    return GrowthAnalysisInput(
        client_id="sample-dental",
        company_name="Sample Dental Clinic",
        domain="https://sample.dental",
        industry="dental",
        period_start="2026-07-22",
        period_end="2026-08-18",
        comparison_start="2026-06-24",
        comparison_end="2026-07-21",
    )


def test_toolkit_schemas(sample_client):
    toolkit = MultiSourceAnalyticsToolkit(
        client=sample_client,
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        mock_data=True,
    )
    defs = toolkit.get_tool_definitions()
    assert len(defs) == 5
    tool_names = [d["function"]["name"] for d in defs]
    assert "query_ga4_dimensions" in tool_names
    assert "query_gsc_search_queries" in tool_names
    assert "query_gbp_local_reputation" in tool_names
    assert "query_device_conversion_breakdown" in tool_names
    assert "query_top_referrers_and_landing_pages" in tool_names


def test_toolkit_execution(sample_client):
    toolkit = MultiSourceAnalyticsToolkit(
        client=sample_client,
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        mock_data=True,
    )
    ga4_res = json.loads(toolkit.execute_tool("query_ga4_dimensions", {"dimensions": ["deviceCategory"], "metrics": ["sessions"]}))
    assert "data" in ga4_res
    assert len(ga4_res["data"]) > 0

    gsc_res = json.loads(toolkit.execute_tool("query_gsc_search_queries", {"min_impressions": 5}))
    assert "queries" in gsc_res

    gbp_res = json.loads(toolkit.execute_tool("query_gbp_local_reputation", {}))
    assert "phone_calls" in gbp_res

    dev_res = json.loads(toolkit.execute_tool("query_device_conversion_breakdown", {}))
    assert "devices" in dev_res

    ref_res = json.loads(toolkit.execute_tool("query_top_referrers_and_landing_pages", {"limit": 5}))
    assert "referrers" in ref_res


def test_exploratory_agent_fallback_when_no_key(sample_client, sample_input):
    agent = ExploratoryGrowthAgent(api_key="")
    toolkit = MultiSourceAnalyticsToolkit(
        client=sample_client,
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        mock_data=True,
    )
    discoveries = agent.explore(sample_client, sample_input, toolkit)
    assert isinstance(discoveries, list)
    assert len(discoveries) >= 2
    assert all(isinstance(d, DataDiscovery) for d in discoveries)


def test_exploratory_agent_tool_calling_flow(sample_client, sample_input):
    toolkit = MultiSourceAnalyticsToolkit(
        client=sample_client,
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        mock_data=True,
    )

    # Simulate Step 1: Agent calls tool query_ga4_dimensions
    step1_response = MagicMock()
    step1_response.status_code = 200
    step1_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_ga4_1",
                            "type": "function",
                            "function": {
                                "name": "query_ga4_dimensions",
                                "arguments": json.dumps({"dimensions": ["deviceCategory"], "metrics": ["sessions", "conversions"]}),
                            },
                        }
                    ],
                }
            }
        ]
    }

    # Simulate Step 2: Agent synthesizes discovery
    step2_response = MagicMock()
    step2_response.status_code = 200
    step2_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "discoveries": [
                            {
                                "title": "Mobile Conversion Dominance in Winter Park",
                                "source": "GA4 Multi-Dimension Slicing",
                                "insight": "Mobile visitors generated 72% of all booked consultation appointments.",
                                "recommended_action": "Enable sticky appointment CTA bar on mobile screens."
                            }
                        ]
                    }),
                }
            }
        ]
    }

    mock_http = MagicMock()
    mock_http.post.side_effect = [step1_response, step2_response]

    agent = ExploratoryGrowthAgent(
        api_key="sk-or-test-key",
        model="openai/gpt-4o-mini",
        base_url="https://openrouter.ai/api/v1",
        http_client=mock_http,
    )

    discoveries = agent.explore(sample_client, sample_input, toolkit)
    assert len(discoveries) == 1
    assert discoveries[0].title == "Mobile Conversion Dominance in Winter Park"
    assert discoveries[0].source == "GA4 Multi-Dimension Slicing"
