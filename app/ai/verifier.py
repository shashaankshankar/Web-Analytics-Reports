from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from app.ai.privacy import sanitize_for_ai
from app.ai.structured_output import VERIFIER_SCHEMA, parse_response_json, response_format
from app.analytics.contracts import DataDiscovery, EvidenceBundle, ValidationDecision
from app.analytics.discovery_integrity import approved_card_fingerprint
from app.config import Settings

VERIFIER_SYSTEM_PROMPT = """You are an independent verifier for evidence-backed analytics findings.

Each proposal contains one complete model-authored client card and one deterministic candidate. The candidate and its exact cited source facts are authoritative. Evaluate the title, what_we_noticed, and recommended_next_step together against those exact facts. Do not rewrite or add facts. Reject a proposal whose card makes an unsupported quantitative, temporal, causal, technical, or scope claim, whose language widens the candidate's audience/device/channel/page/query/location scope, whose title is unclear or unprofessional for a client email, or whose action does not follow from the candidate. Every proposal must receive an independent decision and a concrete reason.

Return one approve/reject decision for every proposal, preserving both proposal_id and candidate_id:
{"decisions":[{"proposal_id":"proposal-001","candidate_id":"cand-001","approved":true,"reason":"The complete client card stays within the selected candidate's facts and scope."}]}
"""


class DiscoveryVerifier:
    """Independent verifier; unavailable verification always withholds client cards."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        base_url: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        settings = Settings.from_env()
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self.model = model or settings.llm_model
        self.reasoning_effort = reasoning_effort or settings.llm_reasoning_effort
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")
        self.http_client = http_client

    @staticmethod
    def _reject_without_verification(discoveries: list[DataDiscovery], reason: str) -> list[ValidationDecision]:
        return [
            ValidationDecision(
                discovery_index=index,
                proposal_id=discovery.proposal_id or f"proposal-{index + 1:03d}",
                candidate_id=discovery.candidate_id,
                status="rejected",
                reasons=[reason],
            )
            for index, discovery in enumerate(discoveries)
        ]

    def verify(
        self,
        discoveries: list[DataDiscovery],
        evidence: EvidenceBundle,
    ) -> tuple[list[ValidationDecision], str]:
        if not discoveries:
            return [], "not_run"
        if not self.api_key or not self.api_key.strip():
            return self._reject_without_verification(
                discoveries,
                "OpenRouter verifier credentials are unavailable; the client card is withheld.",
            ), "provider_unavailable"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "report_period": {
                        "current": [evidence.period_start, evidence.period_end],
                        "prior": [evidence.prior_start, evidence.prior_end],
                        "mode": evidence.report_mode.value,
                    },
                    "candidate_payloads": [
                        {
                            "proposal_id": discovery.proposal_id,
                            "candidate_id": discovery.candidate_id,
                            "candidate": discovery.candidate.model_dump(mode="json") if discovery.candidate else None,
                            "client_card": discovery.client_card.model_dump(mode="json") if discovery.client_card else None,
                        }
                        for discovery in discoveries
                    ],
                    "cited_evidence": [
                        record.model_dump(mode="json")
                        for record in evidence.records
                        if any(record.evidence_id == (discovery.candidate.evidence_id if discovery.candidate else "") for discovery in discoveries)
                    ],
                }, ensure_ascii=False)},
            ],
            "temperature": 0,
            "response_format": response_format("verification_result", VERIFIER_SCHEMA),
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        payload = sanitize_for_ai(payload)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        endpoint = f"{self.base_url}/chat/completions"
        try:
            if self.http_client:
                response = self.http_client.post(endpoint, headers=headers, json=payload, timeout=60.0)
            else:
                with httpx.Client(timeout=60.0) as client:
                    response = client.post(endpoint, headers=headers, json=payload)
            if response.status_code != 200:
                return self._reject_without_verification(
                    discoveries,
                    f"Verifier provider returned HTTP {response.status_code}; the client card is withheld.",
                ), "provider_error"
            parsed = parse_response_json(response.json(), "OpenRouter verifier")
            raw_decisions = parsed.get("decisions") if isinstance(parsed, dict) else None
            if not isinstance(raw_decisions, list):
                raise ValueError("Verifier response is missing decisions.")
            expected = {(item.proposal_id, item.candidate_id): index for index, item in enumerate(discoveries)}
            decisions: list[ValidationDecision] = []
            seen: set[tuple[str, str]] = set()
            for raw in raw_decisions:
                if not isinstance(raw, dict):
                    raise ValueError("Verifier decision is not an object.")
                proposal_id = raw.get("proposal_id")
                candidate_id = raw.get("candidate_id")
                approved = raw.get("approved")
                reason = raw.get("reason", "")
                if (
                    not isinstance(proposal_id, str)
                    or not isinstance(candidate_id, str)
                    or not isinstance(approved, bool)
                    or not isinstance(reason, str)
                    or not reason.strip()
                ):
                    raise ValueError("Verifier decision has an invalid shape.")
                key = (proposal_id, candidate_id)
                if key not in expected or key in seen:
                    raise ValueError("Verifier decision does not match a unique candidate proposal.")
                seen.add(key)
                fingerprint = (
                    approved_card_fingerprint(
                        discoveries[expected[key]],
                        evidence,
                        client_id=evidence.client_id,
                    )
                    if approved
                    else None
                )
                if approved and fingerprint is None:
                    raise ValueError("Verifier approved a card that could not be fingerprinted against its evidence.")
                decisions.append(ValidationDecision(
                    discovery_index=expected[key],
                    proposal_id=proposal_id,
                    candidate_id=candidate_id,
                    status="approved" if approved else "rejected",
                    reasons=[reason],
                    approved_card_fingerprint=fingerprint,
                ))
            if seen != set(expected):
                raise ValueError("Verifier did not decide every candidate proposal.")
            decisions.sort(key=lambda item: item.discovery_index)
            return decisions, "completed"
        except Exception as exc:
            return self._reject_without_verification(
                discoveries,
                f"Verifier response was unavailable or invalid ({type(exc).__name__}); the client card is withheld.",
            ), "invalid_response"
