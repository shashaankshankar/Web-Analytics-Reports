from __future__ import annotations

import re
from collections.abc import Iterable

from app.analytics.contracts import (
    ClientDiscoveryCard,
    DataDiscovery,
    ExplorationAudit,
    ExplorationCandidate,
)
from app.analytics.discovery_integrity import approved_card_fingerprint


def _presentation_eligible(
    discovery: DataDiscovery,
    audit: ExplorationAudit | None = None,
    *,
    client_id: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> ClientDiscoveryCard | None:
    """Return the stored model card only when the full approval gate passed."""

    if (
        discovery.client_card is None
        or discovery.candidate is None
        or not discovery.deterministic_eligible
        or discovery.verification_status != "verified"
    ):
        return None
    if audit is None or audit.status != "completed" or audit.verifier_status != "completed":
        return None
    if client_id is None or audit.client_id != client_id or audit.evidence.client_id != client_id:
        return None
    if period_start is not None and audit.evidence.period_start != period_start:
        return None
    if period_end is not None and audit.evidence.period_end != period_end:
        return None
    approved = next((accepted for accepted in audit.accepted_findings
                     if accepted.proposal_id == discovery.proposal_id
                     and accepted.candidate_id == discovery.candidate_id), None)
    if approved is None or approved.verification_status != "verified":
        return None
    verifier_approval = next((decision for decision in audit.verifier_decisions
                              if decision.proposal_id == discovery.proposal_id
                              and decision.candidate_id == discovery.candidate_id
                              and decision.status == "approved"), None)
    if verifier_approval is None or not verifier_approval.approved_card_fingerprint:
        return None
    fingerprint = approved_card_fingerprint(discovery, audit.evidence, client_id=client_id)
    if (
        not fingerprint
        or fingerprint != discovery.approved_card_fingerprint
        or fingerprint != approved.approved_card_fingerprint
        or fingerprint != verifier_approval.approved_card_fingerprint
    ):
        return None
    return discovery.client_card


def build_client_discovery_copy(
    discovery: DataDiscovery,
    *,
    audit: ExplorationAudit | None = None,
    client_id: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> ClientDiscoveryCard | None:
    """Expose the model-authored card without creating or rewriting its copy."""

    return _presentation_eligible(discovery, audit, client_id=client_id, period_start=period_start, period_end=period_end)


def _theme_tokens(candidate: ExplorationCandidate) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", candidate.label.lower()))
    technical = {
        "position", "rank", "ranking", "sessions", "session", "conversions", "conversion", "rate", "share",
        "change", "percent", "percentage", "average", "total", "count", "clicks", "click", "impressions",
        "ctr", "query", "queries",
    }
    return {token for token in tokens if token not in technical and len(token) > 1}


def _same_theme(left: ExplorationCandidate, right: ExplorationCandidate) -> bool:
    if (left.category or "").lower() != (right.category or "").lower():
        return False
    if (left.metric_name or "").lower() != (right.metric_name or "").lower():
        return False
    left_tokens = _theme_tokens(left)
    right_tokens = _theme_tokens(right)
    if not left_tokens or not right_tokens:
        return not left_tokens and not right_tokens
    overlap = left_tokens.intersection(right_tokens)
    smaller = min(len(left_tokens), len(right_tokens))
    larger = max(len(left_tokens), len(right_tokens))
    return len(overlap) >= 2 and (len(overlap) == smaller or len(overlap) / larger >= 0.6)


def build_client_discovery_copies(
    discoveries: Iterable[DataDiscovery],
    *,
    audit: ExplorationAudit | None = None,
    client_id: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    max_items: int = 3,
) -> list[ClientDiscoveryCard]:
    """Select distinct approved cards; never merge or author their text."""

    if max_items <= 0:
        return []

    selected: list[tuple[ExplorationCandidate, ClientDiscoveryCard]] = []
    selected_ids: set[tuple[str, str]] = set()
    for discovery in discoveries:
        card = _presentation_eligible(discovery, audit, client_id=client_id, period_start=period_start, period_end=period_end)
        if card is None:
            continue
        candidate = discovery.candidate
        key = (discovery.proposal_id, candidate.candidate_id)
        if key in selected_ids or any(_same_theme(candidate, existing) for existing, _ in selected):
            continue
        selected.append((candidate, card))
        selected_ids.add(key)
        if len(selected) >= max_items:
            break
    return [card for _, card in selected]
