import json
from unittest.mock import MagicMock

from app.ai.analyst import GrowthAnalyst, build_performance_user_prompt
from app.ai.privacy import sanitize_for_ai
from app.ai.tools import MultiSourceAnalyticsToolkit
from app.analytics.contracts import GrowthAnalysisInput, LocalInteractionData, MetricDelta
from tests.fakes import FakeGA4Extractor, FakeGBPExtractor, fake_client


SENSITIVE_VALUES = (
    "Patient Jane Doe",
    "review-secret",
    "profile-secret",
    "recipient@example.com",
    "+1 407-555-0199",
    "123 Secret Street",
    "re_sensitive_123",
    "Bearer top-secret-token",
)


def _growth_input() -> GrowthAnalysisInput:
    return GrowthAnalysisInput(
        client_id="test-client",
        company_name="Test Company",
        domain="https://test.example.com",
        industry="healthcare",
        period_start="2026-07-22",
        period_end="2026-08-18",
        comparison_start="2026-06-24",
        comparison_end="2026-07-21",
        goals=["Improve qualified inquiries"],
        core_metrics=[MetricDelta(
            metric_name="sessions",
            display_name="Total Sessions",
            current_value=120,
            prior_value=100,
            absolute_change=20,
            percentage_change=20,
            direction="up",
        )],
        local_seo=LocalInteractionData(
            phone_calls=5,
            profile={
                "title": "Dr. Ada Lovelace Dental",
                "primary_phone": "+1 407-555-0199",
                "address": {"addressLines": ["123 Secret Street"]},
                "private_location_id": "locations/private-123",
                "regular_hours": {"periods": []},
            },
            reviews=[{
                "reviewer": {
                    "displayName": "Patient Jane Doe",
                    "profileId": "profile-secret",
                },
                "review_id": "review-secret",
                "comment": "Patient Jane Doe shared a private message.",
            }],
            review_response_summary={
                "review_count": 42,
                "reply_coverage_percent": 75.0,
                "reply_status_counts": {"NOT_REPLIED": 2},
            },
            reviews_status="available",
        ),
        raw_summary_stats={
            "events": {
                "phone_click": 3,
                "email_click": 2,
                "appointment_request": 4,
            },
            "delivery_metrics": {
                "status": "partial",
                "metrics": {"delivered": 4, "open_rate": 0.5},
                "records": [{
                    "recipient": "recipient@example.com",
                    "resend_email_id": "re_sensitive_123",
                }],
            },
            "delivery_records": [{
                "recipient": "recipient@example.com",
                "resend_email_id": "re_sensitive_123",
            }],
            "source_diagnostics": {
                "gsc": "Authorization: Bearer top-secret-token to=recipient@example.com",
            },
        },
    )


def test_recursive_ai_payload_removes_identifiers_and_delivery_details():
    original = {
        "review": {
            "reviewer": {"displayName": "Patient Jane Doe"},
            "review_id": "review-secret",
            "comment": "Patient Jane Doe shared a private message.",
        },
        "delivery_metrics": {
            "status": "partial",
            "metrics": {"delivered": 4, "open_rate": 0.5},
            "records": [{"recipient": "recipient@example.com", "email_id": "re_sensitive_123"}],
        },
        "delivery_records": [{"recipient": "recipient@example.com"}],
        "aggregates": {"phone_calls": 5, "conversion_rate": 2.5},
        "error": "Authorization: Bearer top-secret-token",
    }

    safe = sanitize_for_ai(original)
    serialized = json.dumps(safe)

    assert "review" not in safe
    assert "delivery_records" not in safe
    assert "records" not in safe["delivery_metrics"]
    assert safe["delivery_metrics"]["status"] == "partial"
    assert safe["delivery_metrics"]["metrics"] == {"delivered": 4, "open_rate": 0.5}
    assert safe["aggregates"] == {"phone_calls": 5, "conversion_rate": 2.5}
    assert all(value not in serialized for value in SENSITIVE_VALUES)
    assert original["review"]["reviewer"]["displayName"] == "Patient Jane Doe"


def test_analyst_prompt_and_outbound_payload_are_privacy_safe():
    data = _growth_input()
    prompt = build_performance_user_prompt(data)

    for value in SENSITIVE_VALUES:
        assert value not in prompt
    assert '"phone_calls": 5' in prompt
    assert '"review_count": 42' in prompt
    assert '"reply_coverage_percent": 75.0' in prompt
    assert '"appointment_request": 4' in prompt

    response = MagicMock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": json.dumps({
        "executive_summary": ["Current data is available."],
        "biggest_win": "Current data is available.",
        "watch_item": None,
        "traffic_and_inflow_insights": "Current traffic is available.",
        "conversion_insights": "Recorded actions are available.",
        "seo_and_content_opportunities": "No additional search detail was supplied.",
        "local_seo_insights": "Local profile context is available.",
        "agency_action_plan": [],
        "overall_sentiment": "Moderate",
    })}}]}
    http_client = MagicMock()
    http_client.post.return_value = response

    GrowthAnalyst(api_key="sk-test", http_client=http_client).analyze(data)
    outbound = json.dumps(http_client.post.call_args.kwargs["json"])
    for value in SENSITIVE_VALUES:
        assert value not in outbound
    user_prompt = http_client.post.call_args.kwargs["json"]["messages"][1]["content"]
    assert '"phone_calls": 5' in user_prompt
    assert '"delivered": 4' in user_prompt


class _SensitiveGSC:
    def fetch_search_analytics(self, start_date, end_date, row_limit=1000, **_kwargs):
        rows = [
            {"query": "dentist near me", "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 5.0},
            {"query": "patient Jane Doe appointment", "clicks": 9, "impressions": 90, "ctr": 0.1, "position": 6.0},
            {"query": "jane@example.com", "clicks": 8, "impressions": 80, "ctr": 0.1, "position": 7.0},
            {"query": "clinic contact", "patient_id": "patient-secret", "clicks": 7, "impressions": 70, "ctr": 0.1, "position": 8.0},
        ]
        return {
            "source": "gsc",
            "status": "available",
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
        }


def test_gsc_tool_suppresses_patient_rows_and_retains_safe_aggregate_evidence():
    toolkit = MultiSourceAnalyticsToolkit(
        client=fake_client(),
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        ga4_extractor=FakeGA4Extractor(),
        gsc_extractor=_SensitiveGSC(),
        gbp_extractor=FakeGBPExtractor(),
    )

    serialized = toolkit.execute_tool("query_gsc_search_queries", {"query_regex": ""})
    payload = json.loads(serialized)

    assert [row["query"] for row in payload["queries"]] == ["dentist near me"]
    assert [row["query"] for row in payload["prior_queries"]] == ["dentist near me"]
    assert all(value not in serialized for value in SENSITIVE_VALUES)
    record = toolkit.evidence_bundle().records[0]
    assert [row["query"] for row in record.current.rows] == ["dentist near me"]
