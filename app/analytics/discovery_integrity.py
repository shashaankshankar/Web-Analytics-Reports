from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from app.analytics.contracts import DataDiscovery, EvidenceBundle


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip())


def approved_card_fingerprint(
    discovery: DataDiscovery,
    evidence: EvidenceBundle,
    *,
    client_id: str,
) -> str | None:
    """Hash the exact approved card, source identity, and report context."""
    candidate = discovery.candidate
    if discovery.client_card is None or candidate is None:
        return None
    record = next((item for item in evidence.records if item.evidence_id == candidate.evidence_id), None)
    if record is None:
        return None
    facts = {fact.fact_id: fact for fact in record.facts}
    payload = {
        "schema": 1,
        "context": {
            "client_id": client_id,
            "evidence_client_id": evidence.client_id,
            "report_mode": evidence.report_mode.value,
            "period_start": evidence.period_start,
            "period_end": evidence.period_end,
            "prior_start": evidence.prior_start,
            "prior_end": evidence.prior_end,
            "measurement_start_date": evidence.measurement_start_date,
            "comparison_suppressed": evidence.comparison_suppressed,
        },
        "proposal_id": discovery.proposal_id,
        "candidate": candidate.model_dump(mode="json"),
        "evidence": {
            "evidence_id": record.evidence_id,
            "source": record.source,
            "fact_ids": candidate.fact_ids,
            "facts": [facts[fact_id].model_dump(mode="json") for fact_id in candidate.fact_ids if fact_id in facts],
        },
        "card": {
            "title": _normalize_text(discovery.client_card.title),
            "what_we_noticed": _normalize_text(discovery.client_card.what_we_noticed),
            "recommended_next_step": _normalize_text(discovery.client_card.recommended_next_step),
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
