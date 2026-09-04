from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import httpx
from pydantic import ValidationError

from app.ai.privacy import sanitize_for_ai
from app.ai.tools import MultiSourceAnalyticsToolkit
from app.ai.validation import deterministic_decisions
from app.ai.verifier import DiscoveryVerifier
from app.ai.structured_output import EXPLORATION_SCHEMA, parse_response_json, response_format
from app.analytics.discovery_integrity import approved_card_fingerprint
from app.analytics.contracts import (
    DataDiscovery,
    DiscoveryProposal,
    EvidenceCitation,
    ExplorationAudit,
    ExplorationCandidate,
    ExplorationResult,
    GrowthAnalysisInput,
    LintWarningRecord,
    NumericClaim,
    ReportMode,
    ValidationDecision,
)
from app.config import ClientConfig, Settings


AGENT_SYSTEM_PROMPT = """You are an evidence-led exploratory analytics advisor for a client performance report.

Use read-only tools to inspect real source data. Each tool response contains a deterministic candidate catalog. Select and rank only candidates whose eligible field is true. Do not create candidate IDs, fact IDs, metrics, values, dates, formulas, comparisons, or causal claims. Empty, unavailable, and error responses are not findings, but a failure in one source does not prevent you from using eligible candidates from another source.

Return only JSON with this shape:
{
  "discoveries": [
    {
      "proposal_id": "proposal-001",
      "candidate_id": "cand-ev-001-device-mobile-sessions",
      "title": "A concise professional client-ready headline in normal capitalization",
      "what_we_noticed": "A plain-language observation that is directly supported by the selected candidate and preserves its scope.",
      "recommended_next_step": "A bounded, practical agency action that follows from the selected candidate without adding facts, guarantees, or causal claims.",
      "numeric_claims": [],
      "rank": 1
    }
  ]
}

The candidate and cited source facts are the only factual authority. All three client-card fields must be written by you for each selected candidate; never leave one blank and never rely on a template. Use plain business language for the intended client audience. Do not mention GA4, Search Console, GBP, GSC, source names, candidate IDs, evidence IDs, formulas, rankings, positions, metrics, verifier/status terms, or other audit language. For search visibility, translate technical source terms into client language such as "appeared in search results 6 times"; do not use terms such as impressions, CTR, ranking, or position in the card. For profile review totals or ratings, state only the observed profile value; do not call it strong, positive, or proof, connect it to inquiry growth, or claim that reviewers were satisfied. Recommendations must follow the selected candidate's exact scope and facts; do not introduce unprovided service lines, page elements, tracking event names, locations, patient satisfaction, or outcomes. For a review-count candidate, keep the action limited to profile or review-process maintenance. For a page-level conversion candidate, do not infer specific forms, phone, booking, or other tracking mechanisms unless the candidate facts name them. Prefer concise qualitative card prose, but include one exact, client-useful number when its magnitude would help the reader prioritize the next step, especially for search visibility, review totals, or audience share. Return "numeric_claims": [] when the three card fields contain no digits. When you include a number, use the exact number from the selected candidate, make the number's unit explicit in the same phrase, set claim_text to the exact phrase containing that one number, set span_start and span_end to its zero-based Python slice bounds, and cite the selected candidate's exact evidence_id and fact_ids. Do not add numbers merely because the candidate has a numeric value. Do not claim that one thing caused another, guarantee an outcome, or broaden a scoped candidate into a site-wide claim. If the candidate is scoped to a device, channel, page, query, location, or audience, keep that scope explicit in the card. In an initial baseline, select current-period candidates only. Return an empty discoveries array when no eligible candidate is useful.
"""


def format_goals_context(goals: list[str]) -> str:
    cleaned_goals = [goal.strip() for goal in goals if goal.strip()]
    if not cleaned_goals:
        return "No specific client goals are configured."
    return "\n".join(f"{index}. {goal}" for index, goal in enumerate(cleaned_goals, start=1))


def _format_value(value: object, unit: str, signed: bool = False) -> str:
    if value is None:
        return "not available"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit == "percent":
        text = f"{number:.2f}%".rstrip("0").rstrip(".")
    elif unit == "rating":
        text = f"{number:.1f}"
    elif unit == "position":
        text = f"{number:.1f}"
    elif number.is_integer():
        text = f"{int(number):,}"
    else:
        text = f"{number:,.2f}".rstrip("0").rstrip(".")
    if signed and number > 0:
        return f"+{text}"
    return text


def deterministic_candidate_headline(candidate: ExplorationCandidate) -> str:
    if candidate.operation in {"change", "percent_change"}:
        return f"{candidate.label}: {_format_value(candidate.value, candidate.unit, signed=True)} change"
    return f"{candidate.label}: {_format_value(candidate.value, candidate.unit)}"


def deterministic_candidate_context(candidate: ExplorationCandidate, bundle_start: str, bundle_end: str) -> str:
    if candidate.operation in {"change", "percent_change"}:
        return (
            f"Current {_format_value(candidate.current_value, candidate.unit)}; prior "
            f"{_format_value(candidate.prior_value, candidate.unit)}; calculated "
            f"{_format_value(candidate.value, candidate.unit, signed=True)} during the comparison window."
        )
    return f"Observed {_format_value(candidate.value, candidate.unit)} during {bundle_start} to {bundle_end}."


def _normalize_numeric_claim_spans(proposal: DiscoveryProposal) -> DiscoveryProposal:
    """Repair uniquely identifiable phrase bounds while preserving model-authored copy."""

    field_text = {
        "title": proposal.title,
        "what_we_noticed": proposal.what_we_noticed,
        "recommended_next_step": proposal.recommended_next_step,
    }
    normalized_claims: list[NumericClaim] = []
    for claim in proposal.numeric_claims:
        text = field_text[claim.field]
        matches = [match.start() for match in re.finditer(re.escape(claim.claim_text), text)]
        if len(matches) == 1:
            start = matches[0]
            claim = claim.model_copy(update={"span_start": start, "span_end": start + len(claim.claim_text)})
        normalized_claims.append(claim)
    return proposal.model_copy(update={"numeric_claims": normalized_claims})


class ExploratoryGrowthAgent:
    """Explore source evidence, then accept only deterministic candidates."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        reasoning_mode: Optional[str] = None,
        base_url: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
        max_steps: int = 5,
    ):
        settings = Settings.from_env()
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self.model = model or settings.llm_model
        self.reasoning_effort = reasoning_effort or settings.llm_reasoning_effort
        self.reasoning_mode = reasoning_mode or settings.llm_reasoning_mode
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")
        self.http_client = http_client
        self.max_steps = max_steps

    @staticmethod
    def _audit(
        client: ClientConfig,
        toolkit: MultiSourceAnalyticsToolkit,
        status: str,
        diagnostics: Optional[list[str]] = None,
        proposed: Optional[list[dict[str, Any]]] = None,
        deterministic: Optional[list[ValidationDecision]] = None,
        verifier_status: Optional[str] = None,
        verifier_decisions: Optional[list[ValidationDecision]] = None,
        accepted: Optional[list[DataDiscovery]] = None,
    ) -> ExplorationAudit:
        evidence = toolkit.evidence_bundle()
        source_statuses: dict[str, Any] = {}
        for record in evidence.records:
            source_statuses[record.evidence_id] = {
                "source": record.source,
                "current": record.current.status.value,
                "prior": record.prior.status.value if record.prior else None,
                "current_truncated": record.current.truncated,
                "prior_truncated": record.prior.truncated if record.prior else False,
                "current_reason": record.current.reason,
                "prior_reason": record.prior.reason if record.prior else None,
                "candidate_count": len(record.candidates),
                "eligible_candidate_count": sum(1 for candidate in record.candidates if candidate.eligible),
            }
        accepted_dependencies = [
            {
                "proposal_id": finding.proposal_id,
                "candidate_id": finding.candidate_id,
                "evidence_id": finding.candidate.evidence_id if finding.candidate else "",
                "fact_ids": finding.source_fact_ids,
                "verification_status": finding.verification_status,
                "approved_card_fingerprint": finding.approved_card_fingerprint,
            }
            for finding in (accepted or [])
        ]
        lint_warnings = [
            LintWarningRecord(
                discovery_index=decision.discovery_index,
                proposal_id=decision.proposal_id,
                warnings=list(decision.warnings),
            )
            for decision in (deterministic or [])
            if decision.warnings
        ]
        return ExplorationAudit(
            client_id=client.client_id,
            status=status,
            report_mode=evidence.report_mode,
            measurement_start_date=evidence.measurement_start_date,
            observation_window_start=evidence.period_start,
            observation_window_end=evidence.period_end,
            comparison_suppressed=evidence.comparison_suppressed,
            comparison_suppression_reason=evidence.comparison_suppression_reason,
            source_statuses=source_statuses,
            evidence=evidence,
            proposed_findings=proposed or [],
            deterministic_decisions=deterministic or [],
            verifier_status=verifier_status,
            verifier_decisions=verifier_decisions or [],
            # Keep an approval snapshot separate from the returned discovery objects. A
            # later mutation of a discovery must not mutate the audit record that is
            # used as the verifier's approval authority.
            accepted_findings=[finding.model_copy(deep=True) for finding in (accepted or [])],
            accepted_dependencies=accepted_dependencies,
            lint_warnings=lint_warnings,
            diagnostics=list(evidence.diagnostics) + list(diagnostics or []),
        )

    @staticmethod
    def _materialize(
        proposal: DiscoveryProposal,
        candidate: ExplorationCandidate,
        bundle_start: str,
        bundle_end: str,
    ) -> DataDiscovery:
        proposal = _normalize_numeric_claim_spans(proposal)
        reported_values = [claim.value for claim in proposal.numeric_claims]
        proposal_id = proposal.proposal_id.strip() or f"proposal-{candidate.candidate_id}"
        return DataDiscovery(
            proposal_id=proposal_id,
            title=proposal.title,
            source=candidate.source,
            insight=proposal.what_we_noticed,
            recommended_action=proposal.recommended_next_step,
            client_card={
                "title": proposal.title,
                "what_we_noticed": proposal.what_we_noticed,
                "recommended_next_step": proposal.recommended_next_step,
            },
            supporting_facts=[EvidenceCitation(evidence_id=candidate.evidence_id, fact_ids=candidate.fact_ids, period=candidate.period)],
            reported_values=reported_values,
            numeric_claims=list(proposal.numeric_claims),
            candidate_id=candidate.candidate_id,
            candidate=candidate,
            claim_type=candidate.claim_type,
            category=candidate.category,
            source_fact_ids=list(candidate.fact_ids),
            metric_name=candidate.metric_name,
            unit=candidate.unit,
            period=candidate.period,
            operation=candidate.operation,
            formula=candidate.formula,
            relation=candidate.relation,
            deterministic_eligible=candidate.eligible,
            eligibility_reasons=list(candidate.eligibility_reasons),
            deterministic_headline=deterministic_candidate_headline(candidate),
            deterministic_context=deterministic_candidate_context(candidate, bundle_start, bundle_end),
        )

    def _materialize_proposals(
        self,
        raw_items: list[Any],
        toolkit: MultiSourceAnalyticsToolkit,
    ) -> tuple[list[DataDiscovery], list[dict[str, Any]]]:
        bundle = toolkit.evidence_bundle()
        candidates = {candidate.candidate_id: candidate for candidate in bundle.candidates}
        discoveries: list[DataDiscovery] = []
        proposed: list[dict[str, Any]] = []
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, dict):
                raise ValueError("A discovery proposal is not an object.")
            proposal_id = str(raw_item.get("proposal_id") or f"proposal-{index + 1:03d}")
            proposed.append({
                "proposal_index": index,
                "proposal_id": proposal_id,
                "candidate_id": raw_item.get("candidate_id"),
                "client_card": {
                    "title": raw_item.get("title"),
                    "what_we_noticed": raw_item.get("what_we_noticed"),
                    "recommended_next_step": raw_item.get("recommended_next_step"),
                },
                "raw": raw_item,
            })
            proposal_payload = dict(raw_item)
            proposal_payload["proposal_id"] = proposal_id
            if "numeric_claims" not in raw_item:
                raise ValueError("Every explorer proposal must include numeric_claims, even when it is empty.")
            proposal = DiscoveryProposal.model_validate(proposal_payload)
            candidate = candidates.get(proposal.candidate_id)
            if candidate is None:
                discoveries.append(DataDiscovery(
                    proposal_id=proposal_id,
                    title=proposal.title,
                    source="",
                    insight=proposal.what_we_noticed,
                    recommended_action=proposal.recommended_next_step,
                    client_card={
                        "title": proposal.title,
                        "what_we_noticed": proposal.what_we_noticed,
                        "recommended_next_step": proposal.recommended_next_step,
                    },
                    supporting_facts=[],
                    candidate_id=proposal.candidate_id,
                    deterministic_eligible=False,
                    eligibility_reasons=[f"Candidate {proposal.candidate_id} was not returned by the queried evidence."],
                ))
                continue
            discoveries.append(self._materialize(proposal, candidate, bundle.period_start, bundle.period_end))
        return discoveries, proposed

    def explore(
        self,
        client: ClientConfig,
        analytics_input: GrowthAnalysisInput,
        toolkit: MultiSourceAnalyticsToolkit,
    ) -> ExplorationResult:
        if not self.api_key or not self.api_key.strip():
            return ExplorationResult(discoveries=[], audit=self._audit(
                client, toolkit, "provider_unavailable", ["OpenRouter explorer credentials are unavailable."], verifier_status="not_run"
            ))

        goals_context = format_goals_context(client.goals)
        initial_user_msg = (
            f"Client: {client.company_name} ({client.domain})\nIndustry: {client.industry}\nGoals:\n{goals_context}\n"
            f"Report mode: {analytics_input.report_mode.value}\nObserved period: {analytics_input.period_start} to {analytics_input.period_end}\n"
            + (f"Prior period: {analytics_input.comparison_start} to {analytics_input.comparison_end}\n" if analytics_input.report_mode == ReportMode.COMPARISON else "Measurement baseline: current observation only; comparison is suppressed.\n")
            + "Query healthy sources, then select and rank only useful eligible candidate IDs."
        )
        messages: list[Dict[str, Any]] = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": initial_user_msg},
        ]
        endpoint = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        for _ in range(self.max_steps):
            payload: Dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "tools": toolkit.get_tool_definitions(),
                "temperature": 0.1,
                "response_format": response_format("exploration_result", EXPLORATION_SCHEMA),
            }
            if self.reasoning_effort:
                payload["reasoning"] = {"effort": self.reasoning_effort}
            payload = sanitize_for_ai(payload)
            try:
                if self.http_client:
                    response = self.http_client.post(endpoint, headers=headers, json=payload, timeout=60.0)
                else:
                    with httpx.Client(timeout=60.0) as client_http:
                        response = client_http.post(endpoint, headers=headers, json=payload)
            except Exception as exc:
                return ExplorationResult(discoveries=[], audit=self._audit(client, toolkit, "provider_error", [f"OpenRouter explorer request failed: {type(exc).__name__}."], verifier_status="not_run"))
            if response.status_code != 200:
                return ExplorationResult(discoveries=[], audit=self._audit(client, toolkit, "provider_error", [f"OpenRouter explorer returned HTTP {response.status_code}."], verifier_status="not_run"))
            try:
                body = response.json()
                message = body["choices"][0]["message"]
            except Exception as exc:
                return ExplorationResult(discoveries=[], audit=self._audit(client, toolkit, "invalid_provider_response", [f"OpenRouter explorer response was malformed: {type(exc).__name__}."], verifier_status="not_run"))
            messages.append(message)
            tool_calls = message.get("tool_calls")
            if tool_calls:
                for tool_call in tool_calls:
                    try:
                        function = tool_call["function"]
                        tool_name = function["name"]
                        raw_arguments = function.get("arguments", "{}")
                        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                        if not isinstance(arguments, dict):
                            raise ValueError("tool arguments are not an object")
                    except Exception as exc:
                        return ExplorationResult(discoveries=[], audit=self._audit(client, toolkit, "malformed_tool_call", [f"Explorer emitted malformed tool arguments: {type(exc).__name__}."], verifier_status="not_run"))
                    tool_result = toolkit.execute_tool(tool_name, arguments)
                    try:
                        parsed_tool_result = json.loads(tool_result)
                    except json.JSONDecodeError:
                        return ExplorationResult(discoveries=[], audit=self._audit(client, toolkit, "malformed_tool_response", ["A source tool returned invalid JSON."], verifier_status="not_run"))
                    messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "name": tool_name, "content": tool_result})
                    if parsed_tool_result.get("status") == "error":
                        # Keep the error in the conversation and evidence audit; healthy candidates remain eligible.
                        messages.append({"role": "system", "content": f"Tool {tool_name} failed. Continue with eligible candidates from healthy evidence."})
                continue

            try:
                parsed = parse_response_json(body, "OpenRouter explorer")
            except ValueError as exc:
                return ExplorationResult(
                    discoveries=[],
                    audit=self._audit(
                        client,
                        toolkit,
                        "invalid_provider_response",
                        [str(exc)],
                        verifier_status="not_run",
                    ),
                )

            try:
                raw_discoveries = parsed.get("discoveries") if isinstance(parsed, dict) else None
                if not isinstance(raw_discoveries, list):
                    raise ValueError("discoveries array is missing")
                discoveries, proposed = self._materialize_proposals(raw_discoveries, toolkit)
            except (ValueError, TypeError, ValidationError, json.JSONDecodeError) as exc:
                raw_items = parsed.get("discoveries", []) if isinstance(parsed, dict) else []
                return ExplorationResult(
                    discoveries=[],
                    audit=self._audit(
                        client,
                        toolkit,
                        "malformed_findings",
                        [f"Explorer output did not match the candidate selection schema: {type(exc).__name__}."],
                        proposed=raw_items if isinstance(raw_items, list) else [],
                        verifier_status="not_run",
                    ),
                )

            bundle = toolkit.evidence_bundle()
            valid, deterministic = deterministic_decisions(
                discoveries, bundle, analytics_input.period_start, analytics_input.period_end, analytics_input.comparison_start, analytics_input.comparison_end
            )
            if not valid:
                return ExplorationResult(discoveries=[], audit=self._audit(client, toolkit, "no_valid_findings", ["No selected candidate passed deterministic evidence validation."], proposed=proposed, deterministic=deterministic, verifier_status="not_run"))

            verifier = DiscoveryVerifier(
                api_key=self.api_key,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                base_url=self.base_url,
                http_client=self.http_client,
            )
            verifier_decisions, verifier_status = verifier.verify(valid, bundle)
            decisions_by_key = {(decision.proposal_id, decision.candidate_id): decision for decision in verifier_decisions}
            accepted: list[DataDiscovery] = []
            mapped_verifier_decisions: list[ValidationDecision] = []
            for index, discovery in enumerate(valid):
                key = (discovery.proposal_id, discovery.candidate_id)
                decision = decisions_by_key.get(key)
                if decision is None:
                    decision = next((item for item in verifier_decisions if item.proposal_id == discovery.proposal_id or item.candidate_id == discovery.candidate_id), None)
                if decision is None:
                    decision = ValidationDecision(
                        discovery_index=index,
                        proposal_id=discovery.proposal_id,
                        candidate_id=discovery.candidate_id,
                        status="rejected",
                        reasons=["Verifier did not return a decision; the client card is withheld."],
                    )
                mapped = decision.model_copy(update={"discovery_index": next((item.discovery_index for item in deterministic if item.proposal_id == discovery.proposal_id), index)})
                mapped_verifier_decisions.append(mapped)
                if mapped.status == "approved":
                    fingerprint = approved_card_fingerprint(discovery, bundle, client_id=client.client_id)
                    if fingerprint is None or mapped.approved_card_fingerprint != fingerprint:
                        mapped = mapped.model_copy(update={
                            "status": "rejected",
                            "reasons": ["Verifier approval fingerprint did not match the exact candidate, evidence, and client card."],
                            "approved_card_fingerprint": None,
                        })
                    else:
                        discovery.verification_status = "verified"
                        discovery.approved_card_fingerprint = fingerprint
                        mapped = mapped.model_copy(update={"approved_card_fingerprint": fingerprint})
                        accepted.append(discovery)
                mapped_verifier_decisions[-1] = mapped
            audit_status = "completed" if verifier_status == "completed" and accepted else (
                "verifier_rejected" if verifier_status == "completed" else "deterministic_only_verifier_unavailable"
            )
            return ExplorationResult(discoveries=accepted, audit=self._audit(
                client, toolkit, audit_status, proposed=proposed, deterministic=deterministic,
                verifier_status=verifier_status, verifier_decisions=mapped_verifier_decisions, accepted=accepted,
            ))

        return ExplorationResult(discoveries=[], audit=self._audit(
            client, toolkit, "step_limit_reached", ["Explorer reached its tool-call limit without returning a final candidate selection."], verifier_status="not_run"
        ))
