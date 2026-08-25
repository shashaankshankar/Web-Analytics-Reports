from app.ai.validation import validate_discovery
from app.analytics.discovery_integrity import approved_card_fingerprint
from app.delivery.discovery_copy import build_client_discovery_copy
from app.analytics.contracts import (
    ClientDiscoveryCard,
    DataDiscovery,
    EvidenceBundle,
    EvidenceCitation,
    EvidenceFact,
    EvidencePeriod,
    EvidenceRecord,
    NumericClaim,
    ExplorationAudit,
    ExplorationCandidate,
    SourceAvailability,
    ValidationDecision,
)
from app.ai.agent import ExploratoryGrowthAgent
from app.ai.tools import MultiSourceAnalyticsToolkit
from tests.fakes import FakeGA4Extractor, FakeGBPExtractor, FakeGSCExtractor, fake_client


def evidence_bundle():
    return EvidenceBundle(
        client_id="test-client",
        period_start="2026-07-22",
        period_end="2026-08-18",
        prior_start="2026-06-24",
        prior_end="2026-07-21",
        records=[
            EvidenceRecord(
                evidence_id="ev-001",
                source="ga4",
                current=EvidencePeriod(
                    start_date="2026-07-22",
                    end_date="2026-08-18",
                    status=SourceAvailability.AVAILABLE,
                ),
                prior=EvidencePeriod(
                    start_date="2026-06-24",
                    end_date="2026-07-21",
                    status=SourceAvailability.AVAILABLE,
                ),
                facts=[
                    EvidenceFact(
                        fact_id="sessions-42",
                        label="sessions",
                        value=42,
                        metric_name="sessions",
                        unit="count",
                        operation="value",
                    ),
                    EvidenceFact(
                        fact_id="conversion-rate-42",
                        label="conversion rate",
                        value=42,
                        metric_name="conversion_rate",
                        unit="percent",
                        operation="ratio",
                        formula="conversions divided by sessions, times 100",
                    ),
                ],
            )
        ],
    )


def test_numeric_claim_rejects_percent_claim_backed_by_same_number_of_sessions():
    client_card = ClientDiscoveryCard(
        title="Conversion rate was 42%",
        what_we_noticed="The conversion rate was 42% during the current period.",
        recommended_next_step="Review the conversion flow.",
    )
    candidate = ExplorationCandidate(
        candidate_id="candidate-sessions-42",
        claim_type="observation",
        category="traffic",
        label="sessions",
        source="ga4",
        evidence_id="ev-001",
        fact_ids=["sessions-42"],
        metric_name="sessions",
        unit="count",
        value=42,
        current_value=42,
    )
    discovery = DataDiscovery(
        proposal_id="proposal-001",
        title="Conversion rate was 42%",
        source="ga4",
        insight="The conversion rate was 42% during the current period.",
        recommended_action="Review the conversion flow.",
        client_card=client_card,
        supporting_facts=[EvidenceCitation(evidence_id="ev-001", fact_ids=["sessions-42"])],
        reported_values=[42],
        candidate_id=candidate.candidate_id,
        candidate=candidate,
        claim_type=candidate.claim_type,
        category=candidate.category,
        source_fact_ids=candidate.fact_ids,
        metric_name=candidate.metric_name,
        unit=candidate.unit,
        period=candidate.period,
        operation=candidate.operation,
        relation=candidate.relation,
        numeric_claims=[
            NumericClaim(
                claim_id="claim-001",
                field="title",
                claim_text="42%",
                value=42,
                metric_name="conversion_rate",
                unit="percent",
                operation="ratio",
                evidence_id="ev-001",
                fact_ids=["sessions-42"],
            )
        ],
    )
    reasons = validate_discovery(
        discovery,
        evidence_bundle(),
        "2026-07-22",
        "2026-08-18",
        "2026-06-24",
        "2026-07-21",
    )
    assert reasons
    assert any(
        phrase in reason
        for phrase in (
            "Numeric claim metric does not match",
            "Numeric claim unit does not match",
            "Numeric claim operation or period does not match",
        )
        for reason in reasons
    )


def test_numeric_claim_without_candidate_link_is_rejected():
    discovery = DataDiscovery(
        proposal_id="proposal-001",
        title="Conversion rate was 42%",
        source="GA4",
        insight="The conversion rate was 42% during the current period.",
        recommended_action="Review the conversion flow.",
        supporting_facts=[EvidenceCitation(evidence_id="ev-001", fact_ids=["conversion-rate-42"])],
        reported_values=[42],
        numeric_claims=[
            NumericClaim(
                claim_id="claim-001",
                field="title",
                claim_text="42%",
                value=42,
                metric_name="conversion_rate",
                unit="percent",
                operation="ratio",
                evidence_id="ev-001",
                fact_ids=["conversion-rate-42"],
            )
        ],
    )
    reasons = validate_discovery(
        discovery,
        evidence_bundle(),
        "2026-07-22",
        "2026-08-18",
        "2026-06-24",
        "2026-07-21",
    )
    assert any("candidate" in reason.lower() for reason in reasons)


def candidate_context(report_mode="comparison"):
    from app.analytics.contracts import ReportMode

    mode = ReportMode(report_mode)
    toolkit = MultiSourceAnalyticsToolkit(
        client=fake_client(),
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        report_mode=mode,
        measurement_start_date="2026-08-12" if mode == ReportMode.INITIAL_BASELINE else None,
        ga4_extractor=FakeGA4Extractor(),
        gsc_extractor=FakeGSCExtractor(),
        gbp_extractor=FakeGBPExtractor(),
    )
    import json

    json.loads(toolkit.execute_tool("query_device_conversion_breakdown", {}))
    bundle = toolkit.evidence_bundle()
    candidate = next(item for item in bundle.candidates if item.operation == "value")
    return toolkit, bundle, candidate


def discovery_for_candidate(candidate, bundle, **updates):
    from app.analytics.contracts import DiscoveryProposal

    discovery = ExploratoryGrowthAgent._materialize(
        DiscoveryProposal(
            proposal_id="proposal-candidate",
            candidate_id=candidate.candidate_id,
            title="Mobile Visits Deserve Attention",
            what_we_noticed="Mobile visitors are an important part of the observed audience.",
            recommended_next_step="Review the mobile inquiry experience in the next optimization cycle.",
        ),
        candidate,
        bundle.period_start,
        bundle.period_end,
    )
    if updates:
        return discovery.model_copy(update=updates)
    return discovery


def test_candidate_wrong_metric_unit_or_formula_is_hard_rejected():
    _, bundle, candidate = candidate_context()
    for field, value in (("metric_name", "conversion_rate"), ("unit", "percent"), ("formula", "invented formula")):
        bad_candidate = candidate.model_copy(update={field: value})
        discovery = discovery_for_candidate(bad_candidate, bundle, **{
            field: value,
            "metric_name": bad_candidate.metric_name,
            "unit": bad_candidate.unit,
            "formula": bad_candidate.formula,
        })
        reasons = validate_discovery(
            discovery,
            bundle,
            "2026-07-22",
            "2026-08-18",
            "2026-06-24",
            "2026-07-21",
        )
        assert reasons
        assert any(term in " ".join(reasons).lower() for term in ("metric", "unit", "formula"))


def test_initial_baseline_comparison_candidate_is_hard_rejected_structurally():
    _, comparison_bundle, comparison_candidate = candidate_context()
    comparison_candidate = next(item for item in comparison_bundle.candidates if item.operation == "change")
    baseline_bundle = comparison_bundle.model_copy(update={"report_mode": "initial_baseline"})
    discovery = discovery_for_candidate(comparison_candidate, baseline_bundle)
    reasons = validate_discovery(
        discovery,
        baseline_bundle,
        "2026-07-22",
        "2026-08-18",
        "2026-06-24",
        "2026-07-21",
    )
    assert reasons
    assert any("initial baseline" in reason.lower() for reason in reasons)


def test_numeric_claim_rejects_same_value_with_wrong_percent_unit():
    _, bundle, candidate = candidate_context()
    discovery = discovery_for_candidate(candidate, bundle)
    value_text = f"{int(candidate.value)}%"
    discovery = discovery.model_copy(update={
        "client_card": ClientDiscoveryCard(
            title=f"Mobile visits totaled {value_text}",
            what_we_noticed="Mobile visits are part of the configured audience.",
            recommended_next_step="Review the mobile visit path during the next cycle.",
        ),
        "title": f"Mobile visits totaled {value_text}",
        "insight": "Mobile visits are part of the configured audience.",
        "recommended_action": "Review the mobile visit path during the next cycle.",
        "numeric_claims": [NumericClaim(
            field="title",
            claim_text=value_text,
            value=candidate.value,
            metric_name=candidate.metric_name,
            unit="percent",
            operation=candidate.operation,
            evidence_id=candidate.evidence_id,
            fact_ids=candidate.fact_ids,
            period=candidate.period,
            formula=candidate.formula,
        )],
    })
    reasons = validate_discovery(discovery, bundle, bundle.period_start, bundle.period_end, bundle.prior_start, bundle.prior_end)
    assert reasons == ["Numeric claim unit does not match the selected candidate unit."]


def test_numeric_claim_accepts_client_facing_count_alias_and_bounded_ensure_action():
    _, bundle, candidate = candidate_context()
    discovery = discovery_for_candidate(candidate, bundle)
    value_text = f"{int(candidate.value)} visits"
    card = ClientDiscoveryCard(
        title="Mobile visits deserve attention",
        what_we_noticed=f"Mobile visitors totaled {value_text} during the current period.",
        recommended_next_step="Ensure the mobile inquiry experience remains easy to use and monitor for increased completion over future cycles.",
    )
    discovery = discovery.model_copy(update={
        "title": card.title,
        "insight": card.what_we_noticed,
        "recommended_action": card.recommended_next_step,
        "client_card": card,
        "reported_values": [candidate.value],
        "numeric_claims": [NumericClaim(
            field="what_we_noticed",
            claim_text=value_text,
            value=candidate.value,
            metric_name=candidate.metric_name,
            unit="visits",
            operation=candidate.operation,
            evidence_id=candidate.evidence_id,
            fact_ids=candidate.fact_ids,
            period=candidate.period,
            formula=candidate.formula,
        )],
    })
    reasons = validate_discovery(discovery, bundle, bundle.period_start, bundle.period_end, bundle.prior_start, bundle.prior_end)
    assert reasons == []


def test_client_render_rejects_tampered_card_after_approval():
    toolkit, bundle, candidate = candidate_context()
    discovery = discovery_for_candidate(candidate, bundle)
    discovery.verification_status = "verified"
    discovery.approved_card_fingerprint = approved_card_fingerprint(discovery, bundle, client_id="test-client")
    audit = ExplorationAudit(
        client_id="test-client",
        status="completed",
        verifier_status="completed",
        accepted_findings=[discovery],
        verifier_decisions=[ValidationDecision(
            discovery_index=0,
            proposal_id=discovery.proposal_id,
            candidate_id=discovery.candidate_id,
            status="approved",
            reasons=["approved"],
            approved_card_fingerprint=discovery.approved_card_fingerprint,
        )],
        evidence=bundle,
    )
    assert build_client_discovery_copy(
        discovery,
        audit=audit,
        client_id="test-client",
        period_start=bundle.period_start,
        period_end=bundle.period_end,
    ) is not None
    tampered = discovery.model_copy(update={
        "client_card": ClientDiscoveryCard(
            title=discovery.title,
            what_we_noticed="Tampered after verification.",
            recommended_next_step=discovery.recommended_action,
        ),
        "insight": "Tampered after verification.",
    })
    assert build_client_discovery_copy(
        tampered,
        audit=audit,
        client_id="test-client",
        period_start=bundle.period_start,
        period_end=bundle.period_end,
    ) is None
