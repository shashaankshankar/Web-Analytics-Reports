import copy
import json
from unittest.mock import MagicMock

from app.ai.agent import AGENT_SYSTEM_PROMPT, ExploratoryGrowthAgent, format_goals_context
from app.ai.tools import MultiSourceAnalyticsToolkit
from app.analytics.contracts import DiscoveryProposal, GrowthAnalysisInput, NumericClaim, ReportMode, SourceAvailability
from tests.fakes import FakeGA4Extractor, FakeGBPExtractor, FakeGSCExtractor, fake_client


def sample_input():
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
    )


def toolkit_for(client=None, ga4=None, gsc=None, gbp=None, report_mode=ReportMode.COMPARISON, expected_totals=None):
    return MultiSourceAnalyticsToolkit(
        client=client or fake_client(),
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        report_mode=report_mode,
        measurement_start_date="2026-08-12" if report_mode == ReportMode.INITIAL_BASELINE else None,
        comparison_suppression_reason="The comparison period is before measurement began." if report_mode == ReportMode.INITIAL_BASELINE else None,
        expected_totals=expected_totals,
        ga4_extractor=ga4 or FakeGA4Extractor(),
        gsc_extractor=gsc or FakeGSCExtractor(),
        gbp_extractor=gbp or FakeGBPExtractor(),
    )


def response(content=None, tool_calls=None, status_code=200):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}}]
    }
    return mock_response


def valid_discovery():
    return {
        "discoveries": [
            {
                "proposal_id": "proposal-001",
                "candidate_id": "cand-ev-001-device-mobile-sessions",
                "title": "Mobile Visits Deserve Attention",
                "what_we_noticed": "Mobile visitors are an important part of the observed audience.",
                "recommended_next_step": "Review the mobile inquiry experience in the next optimization cycle.",
                "numeric_claims": [],
                "rank": 1,
            }
        ]
    }


def test_toolkit_schemas_have_no_runtime_mock_surface():
    toolkit = toolkit_for()
    definitions = toolkit.get_tool_definitions()
    assert len(definitions) == 5
    names = [item["function"]["name"] for item in definitions]
    assert names == [
        "query_ga4_dimensions",
        "query_gsc_search_queries",
        "query_gbp_local_reputation",
        "query_device_conversion_breakdown",
        "query_top_referrers_and_landing_pages",
    ]
    for definition in definitions:
        function = definition["function"]
        assert function["strict"] is True
        parameters = function["parameters"]
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])
    assert "mock_data" not in json.dumps(definitions)


def test_materialize_repairs_unique_numeric_claim_span_without_rewriting_copy():
    toolkit = toolkit_for()
    json.loads(toolkit.execute_tool("query_device_conversion_breakdown", {}))
    candidate = next(item for item in toolkit.evidence_bundle().candidates if item.candidate_id == "cand-ev-001-device-mobile-sessions")
    claim_text = f"{int(candidate.value)} visits"
    what_we_noticed = f"Mobile visitors totaled {claim_text} during the current period."
    proposal = DiscoveryProposal(
        proposal_id="proposal-span-repair",
        candidate_id=candidate.candidate_id,
        title="Mobile visits deserve attention",
        what_we_noticed=what_we_noticed,
        recommended_next_step="Review the mobile inquiry experience during the next optimization cycle.",
        numeric_claims=[NumericClaim(
            field="what_we_noticed",
            claim_text=claim_text,
            span_start=0,
            span_end=1,
            value=candidate.value,
            metric_name=candidate.metric_name,
            unit="visits",
            operation=candidate.operation,
            evidence_id=candidate.evidence_id,
            fact_ids=candidate.fact_ids,
            period=candidate.period,
            formula=candidate.formula,
        )],
    )
    discovery = ExploratoryGrowthAgent._materialize(
        proposal,
        candidate,
        toolkit.evidence_bundle().period_start,
        toolkit.evidence_bundle().period_end,
    )
    start = what_we_noticed.index(claim_text)
    assert discovery.client_card is not None
    assert discovery.client_card.what_we_noticed == what_we_noticed
    assert discovery.numeric_claims[0].span_start == start
    assert discovery.numeric_claims[0].span_end == start + len(claim_text)


def test_toolkit_retains_current_prior_evidence_and_explicit_gbp_limitation():
    toolkit = toolkit_for()
    device = json.loads(toolkit.execute_tool("query_device_conversion_breakdown", {}))
    assert device["status"] == SourceAvailability.AVAILABLE.value
    assert device["prior"]["start_date"] == "2026-06-24"
    assert device["prior"]["end_date"] == "2026-07-21"
    assert any(fact["fact_id"] == "device-mobile-sessions" for fact in device["facts"])
    assert any(fact["formula"] for fact in device["facts"] if fact.get("formula"))

    gbp = json.loads(toolkit.execute_tool("query_gbp_local_reputation", {}))
    assert gbp["status"] == SourceAvailability.AVAILABLE.value
    assert gbp["current"]["payload"]["phone_calls"] is None
    assert "profile metadata only" in " ".join(gbp["limitations"])


def test_device_breakdown_withholds_rows_when_their_total_conflicts_with_headline():
    toolkit = toolkit_for(expected_totals={"sessions": 699, "conversions": 28})
    device = json.loads(toolkit.execute_tool("query_device_conversion_breakdown", {}))
    assert device["status"] == SourceAvailability.AVAILABLE.value
    assert device["candidate_eligibility_blocked"] is True
    assert device["eligible_candidates"] == []
    assert any("do not reconcile" in limitation for limitation in device["limitations"])
    assert any("integrity" in diagnostic for diagnostic in toolkit.evidence_bundle().diagnostics)


def test_unavailable_source_returns_no_synthetic_rows():
    toolkit = toolkit_for(
        ga4=FakeGA4Extractor(status=SourceAvailability.UNAVAILABLE.value),
        gsc=FakeGSCExtractor(status=SourceAvailability.UNAVAILABLE.value),
        gbp=FakeGBPExtractor(status=SourceAvailability.UNAVAILABLE.value),
    )
    result = json.loads(toolkit.execute_tool("query_ga4_dimensions", {"dimensions": ["deviceCategory"], "metrics": ["sessions"]}))
    assert result["status"] == SourceAvailability.UNAVAILABLE.value
    assert result["data"] == []
    assert result["facts"] == []
    assert "fallback" not in json.dumps(result).lower()


def test_exploratory_agent_without_provider_returns_no_findings():
    client = fake_client()
    toolkit = toolkit_for(client)
    result = ExploratoryGrowthAgent(api_key="").explore(client, sample_input(), toolkit)
    assert result.discoveries == []
    assert result.audit.status == "provider_unavailable"
    assert result.audit.evidence.records == []


def test_format_goals_context_is_readable_and_honest_when_empty():
    assert format_goals_context(["Local SEO", "Improve inquiries"]) == "1. Local SEO\n2. Improve inquiries"
    assert format_goals_context([]) == "No specific client goals are configured."


def test_exploratory_agent_requires_deterministic_evidence_and_verifier():
    client = fake_client()
    toolkit = toolkit_for(client)
    tool_call = {
        "id": "call-device-1",
        "type": "function",
        "function": {"name": "query_device_conversion_breakdown", "arguments": "{}"},
    }
    verifier_content = json.dumps({
        "decisions": [{
            "proposal_id": "proposal-001",
            "candidate_id": "cand-ev-001-device-mobile-sessions",
            "approved": True,
            "reason": "The bounded prose aligns with the selected candidate.",
        }]
    })
    mock_http = MagicMock()
    mock_http.post.side_effect = [
        response(tool_calls=[tool_call]),
        response(content=json.dumps(valid_discovery())),
        response(content=verifier_content),
    ]

    result = ExploratoryGrowthAgent(
        api_key="sk-or-test",
        model="openai/gpt-5.6-luna",
        base_url="https://openrouter.ai/api/v1",
        http_client=mock_http,
    ).explore(client, sample_input(), toolkit)

    assert len(result.discoveries) == 1
    assert result.discoveries[0].title == "Mobile Visits Deserve Attention"
    assert result.discoveries[0].client_card is not None
    assert result.discoveries[0].client_card.what_we_noticed == "Mobile visitors are an important part of the observed audience."
    assert result.audit.status == "completed"
    assert result.audit.verifier_status == "completed"
    assert result.audit.deterministic_decisions[0].proposal_id == "proposal-001"
    assert result.audit.verifier_decisions[0].proposal_id == "proposal-001"
    assert len(mock_http.post.call_args_list) == 3
    explorer_payload = mock_http.post.call_args_list[0].kwargs["json"]
    assert explorer_payload["response_format"]["type"] == "json_schema"
    assert explorer_payload["response_format"]["json_schema"]["strict"] is True
    assert explorer_payload["response_format"]["json_schema"]["name"] == "exploration_result"
    verifier_payload = mock_http.post.call_args_list[2].kwargs["json"]
    assert "approve/reject" in verifier_payload["messages"][0]["content"]
    assert verifier_payload["response_format"]["type"] == "json_schema"
    assert verifier_payload["response_format"]["json_schema"]["strict"] is True
    assert verifier_payload["response_format"]["json_schema"]["name"] == "verification_result"
    verifier_card = verifier_payload["messages"][1]["content"]
    assert "client_card" in verifier_card
    assert "what_we_noticed" in verifier_card
    assert "recommended_next_step" in verifier_card


def test_unsupported_numeric_card_claim_is_rejected_before_verifier():
    client = fake_client()
    toolkit = toolkit_for(client)
    tool_call = {
        "id": "call-device-1",
        "type": "function",
        "function": {"name": "query_device_conversion_breakdown", "arguments": "{}"},
    }
    invalid = valid_discovery()
    invalid["discoveries"][0]["what_we_noticed"] = "Mobile visitors totaled 999 during the current period."
    verifier_content = json.dumps({
        "decisions": [{
            "proposal_id": "proposal-001",
            "candidate_id": "cand-ev-001-device-mobile-sessions",
            "approved": True,
            "reason": "The candidate remains authoritative.",
        }]
    })
    mock_http = MagicMock()
    mock_http.post.side_effect = [response(tool_calls=[tool_call]), response(content=json.dumps(invalid)), response(content=verifier_content)]

    result = ExploratoryGrowthAgent(api_key="sk-or-test", http_client=mock_http).explore(client, sample_input(), toolkit)
    assert result.discoveries == []
    assert result.audit.deterministic_decisions[0].status == "rejected"
    assert any("numeric" in reason.lower() for reason in result.audit.deterministic_decisions[0].reasons)
    assert len(mock_http.post.call_args_list) == 2


def test_initial_baseline_tool_evidence_has_no_prior_response():
    toolkit = toolkit_for(report_mode=ReportMode.INITIAL_BASELINE)
    result = json.loads(toolkit.execute_tool("query_device_conversion_breakdown", {}))
    assert result["prior"] is None
    assert all(fact.get("prior_value") is None for fact in result["facts"])
    assert "current observation" in toolkit.get_tool_definitions()[0]["function"]["description"]


def test_initial_baseline_movement_wording_is_rejected_for_current_candidate():
    client = fake_client()
    toolkit = toolkit_for(client, report_mode=ReportMode.INITIAL_BASELINE)
    tool_call = {
        "id": "call-device-baseline",
        "type": "function",
        "function": {"name": "query_device_conversion_breakdown", "arguments": "{}"},
    }
    proposal = valid_discovery()
    proposal["discoveries"][0]["what_we_noticed"] = "Mobile visits increased during the observation window."
    verifier_content = json.dumps({
        "decisions": [{
            "proposal_id": "proposal-001",
            "candidate_id": "cand-ev-001-device-mobile-sessions",
            "approved": True,
            "reason": "The candidate is current-period only.",
        }]
    })
    mock_http = MagicMock()
    mock_http.post.side_effect = [response(tool_calls=[tool_call]), response(content=json.dumps(proposal)), response(content=verifier_content)]

    baseline_payload = sample_input().model_dump()
    baseline_payload.update(
        {
            "report_mode": ReportMode.INITIAL_BASELINE,
            "measurement_start_date": "2026-08-12",
            "comparison_suppressed": True,
            "comparison_suppression_reason": "The comparison period is before measurement began.",
        }
    )
    result = ExploratoryGrowthAgent(api_key="sk-or-test", http_client=mock_http).explore(
        client, GrowthAnalysisInput(**baseline_payload), toolkit
    )
    assert result.discoveries == []
    assert any("movement" in reason.lower() for reason in result.audit.deterministic_decisions[0].reasons)


def test_unrelated_tool_failure_does_not_suppress_healthy_candidate():
    client = fake_client()
    toolkit = toolkit_for(client, gsc=FakeGSCExtractor(status=SourceAvailability.ERROR.value))
    tool_call = {
        "id": "call-device-1",
        "type": "function",
        "function": {"name": "query_gsc_search_queries", "arguments": "{}"},
    }
    mock_http = MagicMock()
    healthy_tool_call = {
        "id": "call-device-2",
        "type": "function",
        "function": {"name": "query_device_conversion_breakdown", "arguments": "{}"},
    }
    verifier_content = json.dumps({
        "decisions": [{
            "proposal_id": "proposal-001",
            "candidate_id": "cand-ev-002-device-mobile-sessions",
            "approved": True,
            "reason": "The healthy device evidence supports the selection.",
        }]
    })
    mock_http.post.side_effect = [
        response(tool_calls=[tool_call]),
        response(tool_calls=[healthy_tool_call]),
        response(content=json.dumps({
            **valid_discovery(),
            "discoveries": [{**valid_discovery()["discoveries"][0], "candidate_id": "cand-ev-002-device-mobile-sessions"}],
        })),
        response(content=verifier_content),
    ]

    result = ExploratoryGrowthAgent(api_key="sk-or-test", http_client=mock_http).explore(client, sample_input(), toolkit)
    assert len(result.discoveries) == 1
    assert result.audit.status == "completed"
    assert result.audit.source_statuses["ev-001"]["current"] == "error"
    assert result.discoveries[0].candidate_id == "cand-ev-002-device-mobile-sessions"
    assert result.audit.accepted_dependencies[0]["evidence_id"] == "ev-002"


def test_proposal_ids_survive_deterministic_filter_before_verifier():
    client = fake_client()
    toolkit = toolkit_for(client)
    tool_call = {
        "id": "call-device-1",
        "type": "function",
        "function": {"name": "query_device_conversion_breakdown", "arguments": "{}"},
    }
    invalid = {
        "proposal_id": "proposal-001",
        "candidate_id": "missing-candidate",
        "title": "Missing Candidate",
        "what_we_noticed": "Mobile visitors are an observed audience.",
        "recommended_next_step": "Review the mobile inquiry experience.",
        "numeric_claims": [],
    }
    valid = copy.deepcopy(valid_discovery()["discoveries"][0])
    valid["proposal_id"] = "proposal-002"
    model_content = json.dumps({"discoveries": [invalid, valid]})
    verifier_content = json.dumps({
        "decisions": [{
            "proposal_id": "proposal-002",
            "candidate_id": "cand-ev-001-device-mobile-sessions",
            "approved": True,
            "reason": "The selected candidate is supported.",
        }]
    })
    mock_http = MagicMock()
    mock_http.post.side_effect = [
        response(tool_calls=[tool_call]),
        response(content=model_content),
        response(content=verifier_content),
    ]

    result = ExploratoryGrowthAgent(api_key="sk-or-test", http_client=mock_http).explore(
        client, sample_input(), toolkit
    )

    assert [finding.proposal_id for finding in result.discoveries] == ["proposal-002"]
    assert [(decision.proposal_id, decision.status) for decision in result.audit.deterministic_decisions] == [
        ("proposal-001", "rejected"),
        ("proposal-002", "approved"),
    ]
    assert [(decision.discovery_index, decision.proposal_id, decision.status) for decision in result.audit.verifier_decisions] == [
        (1, "proposal-002", "approved"),
    ]


def test_duplicate_proposal_ids_and_invalid_card_claims_are_audited():
    client = fake_client()
    toolkit = toolkit_for(client)
    tool_call = {
        "id": "call-device-duplicate-proposals",
        "type": "function",
        "function": {"name": "query_device_conversion_breakdown", "arguments": "{}"},
    }
    first = copy.deepcopy(valid_discovery()["discoveries"][0])
    first["what_we_noticed"] = "Mobile visitors totaled 999 during the current period."
    second = copy.deepcopy(first)
    second["what_we_noticed"] = "Mobile visits increased during the observation window."
    verifier_content = json.dumps({
        "decisions": [{
            "proposal_id": "proposal-001",
            "candidate_id": "cand-ev-001-device-mobile-sessions",
            "approved": True,
            "reason": "The first proposal's bounded prose aligns with the selected candidate.",
        }]
    })
    mock_http = MagicMock()
    mock_http.post.side_effect = [
        response(tool_calls=[tool_call]),
        response(content=json.dumps({"discoveries": [first, second]})),
        response(content=verifier_content),
    ]

    result = ExploratoryGrowthAgent(api_key="sk-or-test", http_client=mock_http).explore(client, sample_input(), toolkit)

    assert result.discoveries == []
    assert [(item.discovery_index, item.proposal_id) for item in result.audit.deterministic_decisions] == [
        (0, "proposal-001"),
        (1, "proposal-001"),
    ]
    assert "numeric" in " ".join(result.audit.deterministic_decisions[0].reasons).lower()
    assert "movement" in " ".join(result.audit.deterministic_decisions[1].reasons).lower()
    assert result.audit.deterministic_decisions[1].status == "rejected"
    assert any("duplicate proposal_id" in reason.lower() for reason in result.audit.deterministic_decisions[1].reasons)


def test_verifier_outage_withholds_deterministic_local_finding():
    client = fake_client()
    toolkit = toolkit_for(client)
    tool_call = {
        "id": "call-device-1",
        "type": "function",
        "function": {"name": "query_device_conversion_breakdown", "arguments": "{}"},
    }
    mock_http = MagicMock()
    mock_http.post.side_effect = [
        response(tool_calls=[tool_call]),
        response(content=json.dumps(valid_discovery())),
        response(content="verifier unavailable", status_code=503),
    ]
    result = ExploratoryGrowthAgent(api_key="sk-or-test", http_client=mock_http).explore(client, sample_input(), toolkit)
    assert len(result.discoveries) == 0
    assert result.audit.status == "deterministic_only_verifier_unavailable"
    assert result.audit.verifier_status == "provider_error"
    assert result.audit.verifier_decisions[0].status == "rejected"
    assert result.audit.accepted_findings == []


def test_agent_system_prompt_forbids_raw_variable_names():
    assert "Never output raw technical variable names" in AGENT_SYSTEM_PROMPT
    assert "active_users" in AGENT_SYSTEM_PROMPT
    assert "contact_form_submit" in AGENT_SYSTEM_PROMPT
    assert "plain English" in AGENT_SYSTEM_PROMPT
