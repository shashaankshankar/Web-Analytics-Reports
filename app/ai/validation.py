from __future__ import annotations

import math
import re
from typing import Iterable

from app.analytics.contracts import (
    DataDiscovery,
    EvidenceBundle,
    EvidenceFact,
    EvidenceRecord,
    ExplorationCandidate,
    NumericClaim,
    ReportMode,
    SourceAvailability,
    ValidationDecision,
)

_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?P<raw>[+-]?\d[\d,]*(?:\.\d+)?)(?P<percent>\s*%)?")
_BASELINE_MOVEMENT_PATTERN = re.compile(
    r"\b(?:compared|versus|vs\.?|prior|previous|period[- ]over[- ]period|week[- ]over[- ]week|"
    r"month[- ]over[- ]month|increased|decreased|declined|rose|fell|grew|dropped|spiked|"
    r"higher|lower|trend|trending|movement|gained|lost|"
    r"(?:change|changes|changed)\s+(?:in|from|to|by)\b)",
    re.IGNORECASE,
)
_INTERNAL_CLIENT_LANGUAGE = re.compile(
    r"\b(?:ga4|gsc|google\s+analytics(?:\s+4)?|google\s+search\s+console|search\s+console|"
    r"gbp|evidence|source\s+data|candidate|verifier|deterministic|metric|fact|formula|"
    r"position|rank|ranking|impressions?|ctr)\b",
    re.IGNORECASE,
)
_CAUSAL_OR_GUARANTEE_LANGUAGE = re.compile(
    r"\b(?:caused|because\s+of|resulted\s+in|led\s+to|guarantee(?:s|d)?|"
    r"proves?|proof\s+that|will\s+(?:increase|decrease|generate|deliver|produce))\b",
    re.IGNORECASE,
)
_GLOBAL_SCOPE_LANGUAGE = re.compile(
    r"\b(?:site[- ]wide|sitewide|overall|every(?:one|thing)?|all\s+(?:visitors|users|patients|inquiries|actions)|"
    r"the\s+(?:whole|entire)\s+(?:site|website)|across\s+the\s+(?:whole|entire)\s+(?:site|website))\b",
    re.IGNORECASE,
)
_SCOPE_ALIASES: dict[str, set[str]] = {
    "mobile": {"mobile", "phone", "phones", "smartphone", "smartphones", "handset", "handsets"},
    "desktop": {"desktop", "computer", "computers", "laptop", "laptops"},
    "tablet": {"tablet", "tablets"},
    "organic": {"organic", "unpaid"},
    "direct": {"direct"},
    "paid": {"paid", "advertising", "ads"},
}
_SEMANTIC_ALIASES: dict[str, set[str]] = {
    "sessions": {"session", "sessions", "visit", "visits", "visitor", "visitors", "traffic", "audience"},
    "activeusers": {"user", "users", "visitor", "visitors", "audience"},
    "conversions": {"conversion", "conversions", "inquiry", "inquiries", "lead", "leads", "appointment", "appointments", "contact", "contacts", "call", "calls"},
    "conversionrate": {"conversion", "conversions", "inquiry", "inquiries", "lead", "leads", "appointment", "appointments"},
    "conversionshare": {"conversion", "conversions", "inquiry", "inquiries", "lead", "leads", "appointment", "appointments", "share", "portion"},
    "sessionshare": {"session", "sessions", "visit", "visits", "visitor", "visitors", "traffic", "audience", "share", "portion", "majority"},
    "position": {"search", "searches", "visibility", "visible", "found", "appear", "appearing", "prominence"},
    "average_rating": {"rating", "ratings", "review", "reviews", "reputation", "profile"},
    "total_review_count": {"review", "reviews", "reputation", "profile"},
    "clicks": {"click", "clicks", "visits", "traffic"},
    "impressions": {"search", "searches", "visibility", "seen", "appear", "appearing"},
    "servicecontent": {"service", "services", "treatment", "treatments", "content", "page", "pages", "information"},
}
_SEMANTIC_STOPWORDS = {
    "a", "an", "and", "area", "by", "change", "current", "for", "in", "of", "on", "opportunity", "the",
    "this", "to", "with", "value", "during", "period", "percent", "share", "average", "total", "rate",
}


def _number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _numbers_in_text(text: str) -> list[float]:
    return [value for occurrence in _numeric_occurrences(text) if (value := occurrence["value"]) is not None]


def _numeric_occurrences(text: str) -> list[dict[str, object]]:
    occurrences: list[dict[str, object]] = []
    for match in _NUMBER_PATTERN.finditer(text):
        raw = match.group("raw")
        end = match.end()
        tail = text[end:end + 14]
        percent_word = re.match(r"\s*(?:percent(?:age)?|pct)\b", tail, re.IGNORECASE)
        occurrences.append({
            "value": _number(raw),
            "start": match.start(),
            "end": match.end(),
            "percent": bool(match.group("percent")) or bool(percent_word),
        })
    return occurrences


def _textual_unit(text: str, occurrence: dict[str, object]) -> str | None:
    start = int(occurrence["start"])
    end = int(occurrence["end"])
    context = text[max(0, start - 18):end + 18].lower()
    if occurrence["percent"] or re.search(r"\b(?:percent|percentage|pct)\b", context):
        return "percent"
    if re.search(r"\b(?:session|sessions|user|users|visit|visits|conversion|conversions|inquir(?:y|ies)|lead|leads|call|calls|click|clicks|review|reviews|impression|impressions|appearance|appearances|time|times)\b", context):
        return "count"
    if re.search(r"\b(?:rating|stars?)\b", context):
        return "rating"
    if re.search(r"\b(?:second|seconds|ms|milliseconds)\b", context):
        return "seconds"
    return None


def _text_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if token not in _SEMANTIC_STOPWORDS and len(token) > 1
    }


def _card_text(discovery: DataDiscovery) -> str:
    card = discovery.client_card
    if card is None:
        return ""
    return " ".join((card.title, card.what_we_noticed, card.recommended_next_step))


def _candidate_facts(candidate: ExplorationCandidate, bundle: EvidenceBundle) -> list[EvidenceFact]:
    record = _record_for(bundle, candidate.evidence_id)
    if record is None:
        return []
    facts_by_id = {fact.fact_id: fact for fact in record.facts}
    return [facts_by_id[fact_id] for fact_id in candidate.fact_ids if fact_id in facts_by_id]


def _candidate_scope_groups(candidate: ExplorationCandidate, facts: list[EvidenceFact]) -> set[str]:
    source_text = " ".join([candidate.label, candidate.metric_name, *(fact.label for fact in facts)])
    tokens = _text_tokens(source_text)
    return {
        group
        for group, aliases in _SCOPE_ALIASES.items()
        if tokens.intersection(aliases)
    }


def _candidate_topic_terms(candidate: ExplorationCandidate, facts: list[EvidenceFact]) -> set[str]:
    labels = " ".join([candidate.label, candidate.metric_name, *(fact.label for fact in facts)])
    tokens = _text_tokens(labels)
    semantic_tokens = set(tokens)
    for token in tokens:
        semantic_tokens.update(_SEMANTIC_ALIASES.get(token, set()))
    semantic_tokens.update(_SEMANTIC_ALIASES.get(_normalize_metric(candidate.metric_name), set()))
    return semantic_tokens


def _candidate_descriptive_terms(candidate: ExplorationCandidate, facts: list[EvidenceFact]) -> set[str]:
    labels = " ".join([candidate.label, *(fact.label for fact in facts)])
    tokens = _text_tokens(labels)
    technical = {
        "position", "rank", "ranking", "sessions", "session", "conversions", "conversion", "rate", "share",
        "change", "percent", "percentage", "average", "total", "count", "clicks", "click", "impressions",
        "ctr", "metric", "value", "current", "prior", "previous", "query", "queries",
    }
    scope_terms = set().union(*_SCOPE_ALIASES.values())
    return {token for token in tokens if token not in technical and token not in scope_terms and len(token) > 2}


def _candidate_numeric_values(candidate: ExplorationCandidate, facts: list[EvidenceFact]) -> list[float]:
    values: list[float] = []
    candidate_values = [candidate.value, candidate.current_value]
    if candidate.period in {"prior", "both"}:
        candidate_values.append(candidate.prior_value)
    for value in candidate_values:
        number = _number(value)
        if number is not None:
            values.append(number)
    for fact in facts:
        for value in (fact.value, fact.prior_value if candidate.period in {"prior", "both"} else None):
            number = _number(value)
            if number is not None:
                values.append(number)
    return list(dict.fromkeys(values))


def validate_client_card_semantics(
    discovery: DataDiscovery,
    candidate: ExplorationCandidate,
    bundle: EvidenceBundle,
) -> list[str]:
    """Validate model prose against typed evidence and scope before verifier review.

    This is deliberately a structured semantic guard: it combines candidate/fact
    identity, metric aliases, scope groups, typed source values, and forbidden
    claim classes. It is only the deterministic pre-check; the independent LLM
    verifier remains the final prose review.
    """

    card = discovery.client_card
    if card is None:
        return ["The model did not return a complete client card."]
    fields = (card.title, card.what_we_noticed, card.recommended_next_step)
    reasons: list[str] = []
    if any(not field.strip() for field in fields):
        reasons.append("Title, what_we_noticed, and recommended_next_step must all be nonempty.")

    full_text = " ".join(fields)
    internal_match = _INTERNAL_CLIENT_LANGUAGE.search(full_text)
    if internal_match:
        reasons.append(f"Client card uses internal or raw-data language: {internal_match.group(0)}.")
    causal_match = _CAUSAL_OR_GUARANTEE_LANGUAGE.search(full_text)
    if causal_match:
        reasons.append(f"Client card makes an unsupported causal or guaranteed claim: {causal_match.group(0)}.")

    facts = _candidate_facts(candidate, bundle)
    topic_terms = _candidate_topic_terms(candidate, facts)
    card_tokens = _text_tokens(full_text)
    if not card_tokens.intersection(topic_terms):
        reasons.append("Client card does not identify the selected evidence topic.")

    descriptive_terms = _candidate_descriptive_terms(candidate, facts)
    if descriptive_terms and not card_tokens.intersection(descriptive_terms):
        reasons.append("Client card does not preserve the selected candidate's specific subject or context.")

    scope_groups = _candidate_scope_groups(candidate, facts)
    for scope_group in scope_groups:
        if not card_tokens.intersection(_SCOPE_ALIASES[scope_group]):
            reasons.append(f"Client card expands beyond the candidate's {scope_group} scope.")
    if scope_groups and _GLOBAL_SCOPE_LANGUAGE.search(full_text):
        reasons.append("Client card uses a broader site-wide or overall scope than the selected candidate.")

    factual_text = " ".join((card.title, card.what_we_noticed))
    if candidate.operation not in {"change", "percent_change"} and _BASELINE_MOVEMENT_PATTERN.search(factual_text):
        reasons.append("Client card makes a comparison or movement claim for a non-comparative candidate.")
    return list(dict.fromkeys(reasons))


def _matches(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0005, abs_tol=0.02)


def _normalize_metric(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _normalize_unit(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9%]+", "", value.lower())
    return {"%": "percent", "percentage": "percent", "pct": "percent", "number": "count"}.get(normalized, normalized)


_METRIC_COUNT_UNIT_ALIASES: dict[str, set[str]] = {
    "sessions": {"session", "sessions", "visit", "visits"},
    "activeusers": {"user", "users", "visitor", "visitors"},
    "conversions": {
        "conversion", "conversions", "inquiry", "inquiries", "lead", "leads",
        "appointment", "appointments", "contact", "contacts", "call", "calls",
    },
    "impressions": {"impression", "impressions", "appearance", "appearances"},
    "clicks": {"click", "clicks"},
    "totalreviewscount": {"review", "reviews"},
    "averagerating": {"rating", "ratings", "star", "stars"},
}


def _units_compatible(left: str, right: str, metric_name: str) -> bool:
    """Allow a client-facing count noun to describe a source's generic count unit."""

    normalized_left = _normalize_unit(left)
    normalized_right = _normalize_unit(right)
    if normalized_left == normalized_right:
        return True
    aliases = _METRIC_COUNT_UNIT_ALIASES.get(_normalize_metric(metric_name), set())
    if normalized_left == "count":
        return normalized_right in aliases
    if normalized_right == "count":
        return normalized_left in aliases
    return False


def _record_for(bundle: EvidenceBundle, evidence_id: str) -> EvidenceRecord | None:
    return next((record for record in bundle.records if record.evidence_id == evidence_id), None)


def _candidate_for(bundle: EvidenceBundle, candidate_id: str) -> ExplorationCandidate | None:
    return next((candidate for candidate in bundle.candidates if candidate.candidate_id == candidate_id), None)


def _fact_values(fact: EvidenceFact, period: str) -> list[float]:
    values: list[float] = []
    if period in {"current", "both"} and fact.value is not None:
        value = _number(fact.value)
        if value is not None:
            values.append(value)
    if period in {"prior", "both"} and fact.prior_value is not None:
        value = _number(fact.prior_value)
        if value is not None:
            values.append(value)
    return values


def _calculated_value(fact: EvidenceFact, dependencies: list[EvidenceFact]) -> float | None:
    if fact.calculation == "source_value":
        return _number(fact.value)
    if not dependencies:
        return None
    first = _number(dependencies[0].value)
    prior = _number(dependencies[0].prior_value)
    if fact.calculation == "difference":
        return first - prior if first is not None and prior is not None else None
    if fact.calculation == "percent_change":
        return ((first - prior) / abs(prior)) * 100 if first is not None and prior not in (None, 0) else None
    if len(dependencies) < 2:
        return None
    denominator = _number(dependencies[1].value)
    return first / denominator * 100 if first is not None and denominator not in (None, 0) else None


def validate_evidence_bundle(bundle: EvidenceBundle) -> list[str]:
    errors: list[str] = []
    record_ids: set[str] = set()
    candidate_ids: set[str] = set()
    for record in bundle.records:
        if record.evidence_id in record_ids:
            errors.append(f"Duplicate evidence ID: {record.evidence_id}.")
        record_ids.add(record.evidence_id)
        if record.current.start_date != bundle.period_start or record.current.end_date != bundle.period_end:
            errors.append(f"Evidence {record.evidence_id} current period does not match the report period.")
        if bundle.report_mode == ReportMode.INITIAL_BASELINE and record.prior is not None:
            errors.append(f"Evidence {record.evidence_id} contains a prior response in an initial baseline.")
        if record.prior and (record.prior.start_date != bundle.prior_start or record.prior.end_date != bundle.prior_end):
            errors.append(f"Evidence {record.evidence_id} prior period does not match the comparison period.")
        fact_ids: set[str] = set()
        for fact in record.facts:
            if fact.fact_id in fact_ids:
                errors.append(f"Duplicate fact ID in {record.evidence_id}: {fact.fact_id}.")
            fact_ids.add(fact.fact_id)
            for value in (fact.value, fact.prior_value):
                number = _number(value) if isinstance(value, (int, float, str)) else None
                if number is not None and not math.isfinite(number):
                    errors.append(f"Non-finite numeric fact in {record.evidence_id}: {fact.fact_id}.")
            if fact.operation != "value" and not fact.formula:
                errors.append(f"Calculated fact {fact.fact_id} is missing a formula.")
            for dependency_id in fact.depends_on_fact_ids:
                if dependency_id not in fact_ids:
                    errors.append(f"Fact {fact.fact_id} references unknown dependency {dependency_id}.")
        for candidate in record.candidates:
            if candidate.candidate_id in candidate_ids:
                errors.append(f"Duplicate candidate ID: {candidate.candidate_id}.")
            candidate_ids.add(candidate.candidate_id)
            if candidate.evidence_id != record.evidence_id:
                errors.append(f"Candidate {candidate.candidate_id} is scoped to the wrong evidence record.")
            if candidate.fact_ids != candidate.source_fact_ids:
                errors.append(f"Candidate {candidate.candidate_id} has inconsistent source fact IDs.")
    if bundle.candidates or candidate_ids:
        if {candidate.candidate_id for candidate in bundle.candidates} != candidate_ids:
            errors.append("Evidence bundle candidate catalog does not match its evidence records.")
    return errors


def _source_matches(discovery_source: str, record_source: str) -> bool:
    source = discovery_source.lower()
    aliases = {
        "ga4": ("ga4", "google analytics", "analytics"),
        "gsc": ("gsc", "search console", "google search"),
        "gbp": ("gbp", "business profile", "google maps", "google business"),
    }
    return any(alias in source for alias in aliases.get(record_source.lower(), (record_source.lower(),)))


def _expected_relation(candidate: ExplorationCandidate) -> str:
    if candidate.operation in {"change", "percent_change"}:
        numeric = _number(candidate.value)
        if numeric is not None:
            return "increase" if numeric > 0 else "decrease" if numeric < 0 else "flat"
    if candidate.operation == "ratio":
        return "ratio"
    if candidate.operation == "share":
        return "share"
    return "observed"


def validate_candidate(
    candidate: ExplorationCandidate,
    bundle: EvidenceBundle,
    period_start: str,
    period_end: str,
    prior_start: str,
    prior_end: str,
) -> list[str]:
    reasons: list[str] = []
    record = _record_for(bundle, candidate.evidence_id)
    if record is None:
        return [f"Unknown evidence ID: {candidate.evidence_id}."]
    if record.current.start_date != period_start or record.current.end_date != period_end:
        reasons.append(f"Evidence {record.evidence_id} has the wrong current date range.")
    if record.current.status != SourceAvailability.AVAILABLE:
        reasons.append(f"Evidence {record.evidence_id} current status is {record.current.status.value}, not available.")
    if record.current.truncated:
        reasons.append(f"Evidence {record.evidence_id} current response is truncated.")
    if not _source_matches(candidate.source, record.source):
        reasons.append(f"Candidate source does not match evidence source {record.source}.")
    if not candidate.eligible:
        reasons.extend(candidate.eligibility_reasons or ["Candidate was marked ineligible by deterministic evidence checks."])
    if bundle.report_mode == ReportMode.INITIAL_BASELINE and candidate.period != "current":
        reasons.append("Initial baseline candidates may use current observation values only.")
    if candidate.period in {"prior", "both"}:
        if record.prior is None:
            reasons.append(f"Evidence {record.evidence_id} has no prior-period response.")
        else:
            if record.prior.start_date != prior_start or record.prior.end_date != prior_end:
                reasons.append(f"Evidence {record.evidence_id} has the wrong prior date range.")
            if record.prior.status != SourceAvailability.AVAILABLE:
                reasons.append(f"Evidence {record.evidence_id} prior status is {record.prior.status.value}, not available.")
            if record.prior.truncated:
                reasons.append(f"Evidence {record.evidence_id} prior response is truncated.")

    facts_by_id = {fact.fact_id: fact for fact in record.facts}
    if not candidate.fact_ids:
        return reasons + [f"Candidate {candidate.candidate_id} has no source fact IDs."]
    for fact_id in candidate.fact_ids:
        if fact_id not in facts_by_id:
            reasons.append(f"Unknown fact ID {fact_id} in evidence {candidate.evidence_id}.")
    primary = facts_by_id.get(candidate.fact_ids[0])
    if primary is None:
        return list(dict.fromkeys(reasons))
    if _normalize_metric(primary.metric_name) != _normalize_metric(candidate.metric_name):
        reasons.append(f"Candidate metric {candidate.metric_name} does not match fact {primary.fact_id} metric {primary.metric_name}.")
    if _normalize_unit(primary.unit) != _normalize_unit(candidate.unit):
        reasons.append(f"Candidate unit {candidate.unit} does not match fact {primary.fact_id} unit {primary.unit}.")
    if primary.operation != candidate.operation:
        reasons.append(f"Candidate operation {candidate.operation} does not match fact {primary.fact_id} operation {primary.operation}.")
    if primary.calculation != candidate.calculation:
        reasons.append(f"Candidate calculation {candidate.calculation} does not match fact {primary.fact_id} calculation {primary.calculation}.")
    if primary.formula != candidate.formula:
        reasons.append(f"Candidate formula does not match fact {primary.fact_id}.")
    if candidate.operation != "value" and not candidate.formula:
        reasons.append(f"Calculated candidate {candidate.candidate_id} lacks a formula.")
    dependencies = [facts_by_id[item] for item in primary.depends_on_fact_ids if item in facts_by_id]
    if set(primary.depends_on_fact_ids) - set(candidate.fact_ids):
        reasons.append(f"Candidate {candidate.candidate_id} omits a calculation dependency.")
    expected = _calculated_value(primary, dependencies)
    actual = _number(candidate.value)
    if expected is None or actual is None or not _matches(expected, actual):
        reasons.append(f"Candidate {candidate.candidate_id} value does not match its deterministic calculation.")
    if primary.operation in {"change", "percent_change"}:
        if not dependencies or dependencies[0].prior_value is None:
            reasons.append(f"Candidate {candidate.candidate_id} is missing a prior source value.")
        else:
            if not _matches(_number(dependencies[0].value) or 0, _number(candidate.current_value) or 0):
                reasons.append(f"Candidate {candidate.candidate_id} current value does not match its source fact.")
            if not _matches(_number(dependencies[0].prior_value) or 0, _number(candidate.prior_value) or 0):
                reasons.append(f"Candidate {candidate.candidate_id} prior value does not match its source fact.")
    elif primary.operation == "value":
        if candidate.current_value is None or not _matches(_number(primary.value) or 0, _number(candidate.current_value) or 0):
            reasons.append(f"Candidate {candidate.candidate_id} current value does not match its source fact.")
    if candidate.period != ("both" if primary.operation in {"change", "percent_change"} else "current"):
        reasons.append(f"Candidate {candidate.candidate_id} period does not match its operation.")
    if candidate.relation != _expected_relation(candidate):
        reasons.append(f"Candidate {candidate.candidate_id} relation does not match its calculated value.")
    expected_claim_type = (
        "comparison" if primary.operation in {"change", "percent_change"}
        else "ratio" if primary.operation == "ratio"
        else "share" if primary.operation == "share"
        else "observation"
    )
    if candidate.claim_type != expected_claim_type:
        reasons.append(f"Candidate {candidate.candidate_id} claim type does not match its operation.")
    return list(dict.fromkeys(reasons))


def lint_discovery(discovery: DataDiscovery, candidate: ExplorationCandidate | None = None) -> list[str]:
    """Return advisory prose warnings; warnings never reject a valid candidate."""
    # Keep this as an audit aid. Hard rejection happens in validate_discovery,
    # and no warning can authorize a client card or provide fallback prose.
    narrative = _card_text(discovery)
    warnings: list[str] = []
    if _numbers_in_text(narrative):
        warnings.append("Narrative contains numeric text; deterministic candidate fields remain authoritative.")
    if _BASELINE_MOVEMENT_PATTERN.search(narrative):
        warnings.append("Narrative contains comparison or movement language; review it against the candidate period and relation.")
    if candidate and candidate.operation in {"ratio", "share"} and _BASELINE_MOVEMENT_PATTERN.search(discovery.recommended_action):
        warnings.append("Recommended action uses movement language for a current-period candidate.")
    return list(dict.fromkeys(warnings))


def _validate_numeric_claim(
    claim: NumericClaim,
    discovery: DataDiscovery,
    candidate: ExplorationCandidate,
    bundle: EvidenceBundle,
    period_start: str,
    period_end: str,
    prior_start: str,
    prior_end: str,
) -> list[str]:
    reasons: list[str] = []
    card = discovery.client_card
    field_text = getattr(card, claim.field, "") if card else ""
    if claim.span_start is not None or claim.span_end is not None:
        if claim.span_start is None or claim.span_end is None or field_text[claim.span_start:claim.span_end] != claim.claim_text:
            reasons.append(f"Numeric claim {claim.claim_id or claim.claim_text} has an invalid exact span.")
        claim_start = claim.span_start or 0
    else:
        matches = [match.start() for match in re.finditer(re.escape(claim.claim_text), field_text)]
        if len(matches) != 1:
            reasons.append(f"Numeric claim {claim.claim_id or claim.claim_text} is missing an unambiguous exact phrase in {claim.field}.")
            claim_start = -1
        else:
            claim_start = matches[0]
    claim_occurrences = _numeric_occurrences(claim.claim_text)
    if len(claim_occurrences) != 1:
        reasons.append(f"Numeric claim {claim.claim_id or claim.claim_text} must describe exactly one number.")
    elif claim_start >= 0:
        actual_occurrence = claim_occurrences[0]
        actual_value = _numeric_occurrences(field_text)
        covered = [item for item in actual_value if int(item["start"]) == claim_start + int(actual_occurrence["start"])]
        if len(covered) != 1:
            reasons.append(f"Numeric claim {claim.claim_id or claim.claim_text} does not map to one number in {claim.field}.")
        else:
            textual_unit = _textual_unit(claim.claim_text, claim_occurrences[0])
            if textual_unit is None:
                reasons.append(f"Numeric claim {claim.claim_id or claim.claim_text} does not express an unambiguous unit in the client text.")
            elif not _units_compatible(textual_unit, claim.unit, candidate.metric_name):
                reasons.append(f"Numeric claim unit {claim.unit} does not match the unit written in {claim.claim_text}.")
            if _normalize_unit(candidate.unit) == "percent" and not textual_unit == "percent":
                reasons.append("Percentage claims must explicitly include percent semantics in the client text.")
    if bundle.report_mode == ReportMode.INITIAL_BASELINE and claim.period != "current":
        reasons.append("Initial baseline numeric claims may use current evidence only.")
    if not any(
        claim.evidence_id == citation.evidence_id and all(fact_id in citation.fact_ids for fact_id in claim.fact_ids)
        for citation in discovery.supporting_facts
    ):
        reasons.append(f"Numeric claim {claim.claim_id or claim.claim_text} is not covered by its discovery citations.")
    if claim.evidence_id != candidate.evidence_id or claim.fact_ids != candidate.fact_ids:
        reasons.append("Numeric claim must cite the selected candidate's exact evidence and fact IDs.")
    if _normalize_metric(claim.metric_name) != _normalize_metric(candidate.metric_name):
        reasons.append("Numeric claim metric does not match the selected candidate metric.")
    if not _units_compatible(claim.unit, candidate.unit, candidate.metric_name):
        reasons.append("Numeric claim unit does not match the selected candidate unit.")
    if claim.operation != candidate.operation or claim.period != candidate.period:
        reasons.append("Numeric claim operation or period does not match the selected candidate.")
    if claim.formula != candidate.formula:
        reasons.append("Numeric claim formula does not match the selected candidate.")
    candidate_number = _number(candidate.value)
    if candidate_number is None or not _matches(float(claim.value), candidate_number):
        reasons.append("Numeric claim value does not match the selected candidate value.")
    record = _record_for(bundle, claim.evidence_id)
    if record is None:
        return reasons + [f"Numeric claim references unknown evidence ID: {claim.evidence_id}."]
    if record.current.start_date != period_start or record.current.end_date != period_end:
        reasons.append(f"Numeric claim {claim.claim_id or claim.claim_text} has the wrong current date range.")
    if claim.period in {"prior", "both"} and (record.prior is None or record.prior.start_date != prior_start or record.prior.end_date != prior_end):
        reasons.append(f"Numeric claim {claim.claim_id or claim.claim_text} has the wrong prior date range.")
    facts_by_id = {fact.fact_id: fact for fact in record.facts}
    for fact_id in claim.fact_ids:
        if fact_id not in facts_by_id:
            reasons.append(f"Numeric claim references unknown fact ID {fact_id} in evidence {claim.evidence_id}.")
    primary = facts_by_id.get(candidate.fact_ids[0]) if candidate.fact_ids else None
    if primary is not None and not any(_matches(float(claim.value), value) for value in _fact_values(primary, claim.period)):
        reasons.append(f"Numeric claim value {claim.value} does not match the approved source fact.")
    return reasons


def validate_discovery(
    discovery: DataDiscovery,
    bundle: EvidenceBundle,
    period_start: str,
    period_end: str,
    prior_start: str,
    prior_end: str,
) -> list[str]:
    reasons = validate_evidence_bundle(bundle)
    if discovery.client_card is None:
        reasons.append("A complete model-authored client card is required.")
    else:
        if not discovery.client_card.title.strip() or not discovery.client_card.what_we_noticed.strip() or not discovery.client_card.recommended_next_step.strip():
            reasons.append("Title, what_we_noticed, and recommended_next_step are required.")
    if not discovery.supporting_facts:
        reasons.append("At least one supporting evidence citation is required.")
    candidate = discovery.candidate or _candidate_for(bundle, discovery.candidate_id)
    if candidate is not None:
        reasons.extend(validate_candidate(candidate, bundle, period_start, period_end, prior_start, prior_end))
        if discovery.candidate_id != candidate.candidate_id:
            reasons.append("Discovery candidate_id does not match its candidate payload.")
        if discovery.source != candidate.source:
            reasons.append("Discovery source does not match its deterministic candidate.")
        if discovery.source_fact_ids != candidate.fact_ids:
            reasons.append("Discovery source fact IDs do not match its deterministic candidate.")
        for field_name in ("claim_type", "category", "metric_name", "unit", "period", "operation", "relation"):
            if getattr(discovery, field_name) != getattr(candidate, field_name):
                reasons.append(f"Discovery {field_name} does not match its deterministic candidate.")
        if discovery.formula != candidate.formula:
            reasons.append("Discovery formula does not match its deterministic candidate.")
        for citation in discovery.supporting_facts:
            if citation.evidence_id != candidate.evidence_id or citation.fact_ids != candidate.fact_ids:
                reasons.append("Discovery citation does not match its deterministic candidate dependencies.")
        if discovery.reported_values:
            value = _number(candidate.value)
            if value is None or any(not _matches(float(item), value) for item in discovery.reported_values):
                reasons.append("Discovery reported values do not match the deterministic candidate value.")
        reasons.extend(validate_client_card_semantics(discovery, candidate, bundle))
    else:
        reasons.append("Discovery candidate could not be resolved to source evidence.")
        for citation in discovery.supporting_facts:
            record = _record_for(bundle, citation.evidence_id)
            if record is None:
                reasons.append(f"Unknown evidence ID: {citation.evidence_id}.")
                continue
            if not _source_matches(discovery.source, record.source):
                reasons.append(f"Discovery source does not match evidence source {record.source}.")
            if record.current.status != SourceAvailability.AVAILABLE:
                reasons.append(f"Evidence {record.evidence_id} current status is {record.current.status.value}, not available.")
            facts_by_id = {fact.fact_id for fact in record.facts}
            for fact_id in citation.fact_ids:
                if fact_id not in facts_by_id:
                    reasons.append(f"Unknown fact ID {fact_id} in evidence {citation.evidence_id}.")
    if candidate is not None:
        fields = {
            "title": discovery.client_card.title if discovery.client_card else "",
            "what_we_noticed": discovery.client_card.what_we_noticed if discovery.client_card else "",
            "recommended_next_step": discovery.client_card.recommended_next_step if discovery.client_card else "",
        }
        occurrences = [(field, item) for field, text in fields.items() for item in _numeric_occurrences(text)]
        covered: set[tuple[str, int]] = set()
        for claim in discovery.numeric_claims:
            reasons.extend(_validate_numeric_claim(claim, discovery, candidate, bundle, period_start, period_end, prior_start, prior_end))
            if claim.field in fields:
                start = claim.span_start
                if start is None:
                    matches = [match.start() for match in re.finditer(re.escape(claim.claim_text), fields[claim.field])]
                    start = matches[0] if len(matches) == 1 else None
                if start is not None:
                    for occurrence in _numeric_occurrences(claim.claim_text):
                        key = (claim.field, start + int(occurrence["start"]))
                        if key in covered:
                            reasons.append(f"Numeric statement in {claim.field} is covered by more than one typed numeric claim.")
                        covered.add(key)
        for field, occurrence in occurrences:
            key = (field, int(occurrence["start"]))
            if key not in covered:
                reasons.append(f"Numeric statement in {field} is not covered by a typed numeric claim.")
    return list(dict.fromkeys(reasons))


def deterministic_decisions(
    discoveries: Iterable[DataDiscovery],
    bundle: EvidenceBundle,
    period_start: str,
    period_end: str,
    prior_start: str,
    prior_end: str,
) -> tuple[list[DataDiscovery], list[ValidationDecision]]:
    valid: list[DataDiscovery] = []
    decisions: list[ValidationDecision] = []
    seen_proposal_ids: set[str] = set()
    for index, discovery in enumerate(discoveries):
        reasons = validate_discovery(discovery, bundle, period_start, period_end, prior_start, prior_end)
        candidate = discovery.candidate or _candidate_for(bundle, discovery.candidate_id)
        warnings = lint_discovery(discovery, candidate)
        discovery.narrative_lint_warnings = warnings
        proposal_id = discovery.proposal_id or f"proposal-{index + 1:03d}"
        if proposal_id in seen_proposal_ids:
            reasons.append(f"Duplicate proposal_id: {proposal_id}.")
        seen_proposal_ids.add(proposal_id)
        status = "approved" if not reasons else "rejected"
        decisions.append(ValidationDecision(
            discovery_index=index,
            proposal_id=proposal_id,
            candidate_id=discovery.candidate_id,
            status=status,
            reasons=list(dict.fromkeys(reasons)),
            warnings=warnings,
        ))
        if not reasons:
            valid.append(discovery)
    return valid, decisions
