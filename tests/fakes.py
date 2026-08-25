"""Test-only source and provider fakes.

These fixtures deliberately live outside ``app``. Runtime reports may only use
the configured Google connectors and OpenRouter; tests use these deterministic
fakes to exercise the pipeline without inventing production fallbacks.
"""

from __future__ import annotations

from typing import Any

from app.analytics.contracts import (
    AIReportOutput,
    ActionItem,
    ClientDiscoveryCard,
    ConversionEventSummary,
    DataDiscovery,
    EvidenceBundle,
    EvidenceCitation,
    EvidenceFact,
    EvidencePeriod,
    EvidenceRecord,
    ExplorationAudit,
    ExplorationCandidate,
    ExplorationResult,
    FullGrowthBriefing,
    GrowthAnalysisInput,
    LocalInteractionData,
    MetricDelta,
    ReportMode,
    ReportType,
    SourceAvailability,
    StrikingDistanceKeyword,
    ValidationDecision,
    WeeklyDigestOutput,
)
from app.config import ClientConfig
from app.analytics.discovery_integrity import approved_card_fingerprint


CURRENT_START = "2026-07-22"
CURRENT_END = "2026-08-18"
PRIOR_START = "2026-06-24"
PRIOR_END = "2026-07-21"


class FakeGA4Extractor:
    """Deterministic GA4 response fake used only by tests."""

    def __init__(self, property_id: str = "111222333", status: str = "available"):
        self.property_id = property_id
        self.status = status

    def is_configured(self) -> bool:
        return bool(self.property_id)

    @staticmethod
    def _period(start_date: str) -> str:
        return "prior" if start_date == PRIOR_START else "current"

    def _rows(self, start_date: str, dimensions: list[str], metrics: list[str]) -> list[dict[str, list[str]]]:
        period = self._period(start_date)
        values = {
            "current": {
                "summary": ["500", "700", "0.65", "0.35", "28"],
                "channels": [
                    ("Organic Search", "450", "350", "18"),
                    ("Direct", "150", "120", "5"),
                ],
                "pages": [
                    ("/signup", "120", "100", "75"),
                    ("/pricing", "80", "65", "50"),
                ],
                "events": [("generate_lead", "28"), ("phone_click", "12")],
                "device": [("mobile", "420", "20"), ("desktop", "280", "8")],
                "referrers": [
                    ("google / organic", "/signup", "400", "16"),
                    ("(direct) / (none)", "/pricing", "120", "4"),
                ],
            },
            "prior": {
                "summary": ["400", "550", "0.60", "0.40", "20"],
                "channels": [
                    ("Organic Search", "350", "290", "14"),
                    ("Direct", "120", "95", "4"),
                ],
                "pages": [
                    ("/signup", "90", "78", "42"),
                    ("/pricing", "70", "55", "35"),
                ],
                "events": [("generate_lead", "20"), ("phone_click", "10")],
                "device": [("mobile", "300", "14"), ("desktop", "250", "6")],
                "referrers": [
                    ("google / organic", "/signup", "300", "12"),
                    ("(direct) / (none)", "/pricing", "100", "3"),
                ],
            },
        }[period]

        if not dimensions:
            row_values = values["summary"]
            return [{"dimensions": [], "metrics": [row_values[{
                "activeUsers": 0,
                "sessions": 1,
                "engagementRate": 2,
                "bounceRate": 3,
                "conversions": 4,
            }[metric]] for metric in metrics]}]
        if dimensions == ["sessionDefaultChannelGroup"]:
            return [{"dimensions": [channel], "metrics": [sessions, active_users, conversions][:len(metrics)]}
                    for channel, sessions, active_users, conversions in values["channels"]]
        if dimensions == ["landingPagePlusQueryString"]:
            return [{"dimensions": [path], "metrics": [sessions, active_users, event_count][:len(metrics)]}
                    for path, sessions, active_users, event_count in values["pages"]]
        if dimensions == ["eventName"]:
            return [{"dimensions": [event], "metrics": [count][:len(metrics)]}
                    for event, count in values["events"]]
        if dimensions == ["deviceCategory"]:
            return [{"dimensions": [device], "metrics": [sessions, conversions][:len(metrics)]}
                    for device, sessions, conversions in values["device"]]
        if dimensions == ["sessionSourceMedium", "landingPagePlusQueryString"]:
            return [{"dimensions": [source, path], "metrics": [sessions, conversions][:len(metrics)]}
                    for source, path, sessions, conversions in values["referrers"]]
        return []

    def run_report(
        self,
        start_date: str,
        end_date: str,
        dimensions: list[str],
        metrics: list[str],
        limit: int = 100,
        **_: Any,
    ) -> dict[str, Any]:
        if self.status != SourceAvailability.AVAILABLE.value:
            return {
                "source": "ga4",
                "status": self.status,
                "start_date": start_date,
                "end_date": end_date,
                "rows": [],
                "row_count": 0,
                "reason": f"fake GA4 status={self.status}",
            }
        rows = self._rows(start_date, dimensions, metrics)[:limit]
        return {
            "source": "ga4",
            "status": SourceAvailability.AVAILABLE.value if rows else SourceAvailability.EMPTY.value,
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "row_count": len(rows),
        }

    def fetch_metrics_and_channels(
        self,
        start_date: str,
        end_date: str,
        prior_start_date: str | None = None,
        prior_end_date: str | None = None,
    ) -> dict[str, Any]:
        if self.status != SourceAvailability.AVAILABLE.value:
            return {
                "source": "ga4",
                "status": self.status,
                "reason": f"fake GA4 status={self.status}",
                "summary": {},
                "prior_summary": {},
                "channels": [],
                "pages": [],
                "events": {},
                "prior_events": {},
                "errors": ["fake source failure"] if self.status == "error" else [],
                "query_statuses": {},
            }
        comparison_enabled = bool(prior_start_date and prior_end_date)
        return {
            "source": "ga4",
            "status": "available",
            "summary": {"activeUsers": 500, "sessions": 700, "engagementRate": 0.65, "bounceRate": 0.35, "conversions": 28},
            "prior_summary": {"activeUsers": 400, "sessions": 550, "engagementRate": 0.60, "bounceRate": 0.40, "conversions": 20} if comparison_enabled else {},
            "channels": [
                {"channel": "Organic Search", "sessions": 450, "activeUsers": 350, "conversions": 18, "priorSessions": 350, "sessionChange": 100},
                {"channel": "Direct", "sessions": 150, "activeUsers": 120, "conversions": 5, "priorSessions": 120, "sessionChange": 30},
            ],
            "pages": [
                {"pagePath": "/signup", "sessions": 120, "activeUsers": 100, "priorSessions": 90, "sessionChange": 30},
                {"pagePath": "/pricing", "sessions": 80, "activeUsers": 65, "priorSessions": 70, "sessionChange": 10},
            ],
            "events": {"generate_lead": 28, "phone_click": 12},
            "prior_events": {"generate_lead": 20, "phone_click": 10} if comparison_enabled else {},
            "errors": [],
            "query_statuses": {},
            "current_status": "available",
            "prior_status": "available" if comparison_enabled else None,
            "comparison_enabled": comparison_enabled,
        }


class FakeGSCExtractor:
    """Deterministic Search Console response fake used only by tests."""

    def __init__(self, site_url: str = "https://test.example.com/", status: str = "available", truncated: bool = False):
        self.site_url = site_url
        self.status = status
        self.truncated = truncated

    def fetch_search_analytics(self, start_date: str, end_date: str, row_limit: int = 1000, **_: Any) -> dict[str, Any]:
        if self.status != "available":
            return {
                "source": "gsc",
                "status": self.status,
                "start_date": start_date,
                "end_date": end_date,
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "reason": f"fake GSC status={self.status}",
            }
        prior = start_date == PRIOR_START
        rows = [
            {
                "query": "invisalign dentist",
                "clicks": 25 if not prior else 15,
                "impressions": 500 if not prior else 420,
                "ctr": 0.05 if not prior else 0.0357,
                "position": 6.2 if not prior else 9.5,
            },
            {
                "query": "teeth whitening cost",
                "clicks": 10 if not prior else 9,
                "impressions": 300 if not prior else 280,
                "ctr": 0.0333 if not prior else 0.0321,
                "position": 12.0 if not prior else 11.8,
            },
        ][:row_limit]
        return {
            "source": "gsc",
            "status": "available",
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "row_count": len(rows),
            "truncated": self.truncated,
        }

    def fetch_comparative_search_analytics(
        self,
        start_date: str,
        end_date: str,
        prior_start_date: str,
        prior_end_date: str,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            self.fetch_search_analytics(start_date, end_date, **kwargs),
            self.fetch_search_analytics(prior_start_date, prior_end_date, **kwargs),
        )


class FakeGBPExtractor:
    """GBP fake that exposes only honest profile metadata."""

    def __init__(self, location_id: str = "places/test", status: str = "available", account_id: str = ""):
        self.location_id = location_id
        self.status = status
        self.account_id = account_id

    def fetch_local_insights(self, start_date: str, end_date: str, **_: Any) -> dict[str, Any]:
        return {
            "source": "gbp",
            "status": self.status,
            "reason": "fake GBP profile response",
            "location_id": self.location_id,
            "phone_calls": None,
            "direction_requests": None,
            "website_clicks": None,
            "profile_status": self.status,
            "profile_summary": {
                "title": "Test Company",
                "primary_phone": "+1 555 0100",
                "address": {"addressLines": ["1 Test Street"]},
                "regular_hours": {"periods": []},
                "primary_category": {"displayName": "Test Business"},
                "services": [],
            } if self.status == "available" else {},
            "performance_metrics": {},
            "available_performance_metrics": [],
            "performance_status": "unavailable",
            "monthly_search_keywords": [],
            "search_keywords_status": "unavailable",
            "reviews": [],
            "reviews_status": "unavailable",
            "review_inventory_complete": False,
            "review_response_summary": {},
            "business_calls": {},
            "business_calls_status": "unavailable",
            "answered_calls": None,
            "missed_calls": None,
            "average_rating": 4.9 if self.status == "available" else None,
            "total_reviews_count": 42 if self.status == "available" else None,
            "recent_review_snippets": [],
            "capabilities": {
                "profile_metadata": self.status,
                "action_metrics": "unavailable",
                "period_comparison": "unavailable",
            },
            "limitations": ["Fake GBP connector exposes profile metadata only."],
        }

    def fetch_periodic_insights(self, start_date: str, end_date: str) -> dict[str, Any]:
        return {
            "source": "gbp",
            "status": SourceAvailability.UNAVAILABLE.value,
            "start_date": start_date,
            "end_date": end_date,
            "performance_metrics": {},
            "monthly_search_keywords": [],
            "business_calls": {},
            "business_calls_status": SourceAvailability.UNAVAILABLE.value,
            "answered_calls": None,
            "missed_calls": None,
            "phone_calls": None,
            "direction_requests": None,
            "website_clicks": None,
            "reason": "Fake GBP connector exposes profile metadata only.",
        }


def fake_client(client_id: str = "test-client", deep_insights: bool = False) -> ClientConfig:
    return ClientConfig(
        client_id=client_id,
        company_name="Test Company",
        domain="https://test.example.com",
        industry="healthcare",
        ga4_property_id="111222333",
        gsc_site_url="https://test.example.com/",
        gbp_location_id="places/test",
        recipients={
            "client": "client@test.example.com",
            "agency_cc": "agency@test.example.com",
            "agency_audit": "audit@test.example.com",
        },
        goals=["Improve qualified inquiries"],
        reporting={
            "performance_report": {
                "enabled": True,
                "cadence": "28d",
                "attach_pdf": True,
                "deep_insights": deep_insights,
            }
        },
    )


def fake_report_output() -> AIReportOutput:
    return AIReportOutput(
        executive_summary=["Sessions increased during the current reporting period.", "Organic Search was the largest channel.", "Lead events increased during the current reporting period."],
        biggest_win="Lead events increased during the current reporting period.",
        watch_item="Continue monitoring the highest-intent pages.",
        traffic_and_inflow_insights="The available analytics data shows traffic from configured channels and pages.",
        conversion_insights="The available analytics data includes configured lead events.",
        seo_and_content_opportunities="Search Console data is available for the configured query snapshot.",
        local_seo_insights="GBP action metrics are unavailable from this connector.",
        agency_action_plan=[
            ActionItem(
                title="Review highest-intent pages",
                description="Use the available page data to prioritize the next optimization cycle.",
                impact_area="Conversion",
                priority="High",
                evidence="Configured GA4 page data",
            )
        ],
    )


def fake_full_briefing() -> FullGrowthBriefing:
    """Return a neutral, source-shaped briefing for renderer tests."""

    analytics = GrowthAnalysisInput(
        client_id="test-client",
        company_name="Test Company",
        domain="https://test.example.com",
        industry="healthcare",
        report_type=ReportType.PERFORMANCE_28D,
        period_days=28,
        period_start=CURRENT_START,
        period_end=CURRENT_END,
        comparison_start=PRIOR_START,
        comparison_end=PRIOR_END,
        core_metrics=[
            MetricDelta(
                metric_name="sessions",
                display_name="Total Sessions",
                current_value=700,
                prior_value=550,
                absolute_change=150,
                percentage_change=27.27,
                direction="up",
            ),
            MetricDelta(
                metric_name="conversion_rate",
                display_name="Conversion Rate",
                current_value=4.0,
                prior_value=3.64,
                absolute_change=0.36,
                percentage_points_change=0.36,
                is_percentage_rate=True,
                direction="up",
                unit="percentage",
            ),
        ],
        conversion_events=[
            ConversionEventSummary(
                event_name="generate_lead",
                display_name="Lead Submissions",
                current_count=28,
                prior_count=20,
                count_change=8,
                percentage_change=40.0,
                direction="up",
            )
        ],
        striking_distance_keywords=[
            StrikingDistanceKeyword(
                query="configured search topic",
                impressions=300,
                clicks=10,
                ctr=3.33,
                position=12.4,
                opportunity_score=240.0,
            )
        ],
        local_seo=LocalInteractionData(),
    )
    insights = AIReportOutput(
        executive_summary=[
            "The configured reporting window contains a current source-backed observation.",
            "The configured acquisition snapshot is available for review.",
            "The current event snapshot includes the configured lead event.",
        ],
        biggest_win="The current observation provides a source-backed starting point for optimization.",
        watch_item="Continue monitoring the configured conversion path.",
        traffic_and_inflow_insights="The available source data describes the configured channels and pages.",
        conversion_insights="The current event snapshot includes the configured lead event.",
        seo_and_content_opportunities="The configured search topic is available for future prioritization.",
        local_seo_insights="GBP action metrics are unavailable from this connector.",
        agency_action_plan=[
            ActionItem(
                title="Review Configured Page Data",
                description="Use available source data to prioritize the next optimization cycle.",
                impact_area="Conversion",
                priority="High",
                evidence="Configured source data",
            )
        ],
    )
    return FullGrowthBriefing(
        client_id="test-client",
        company_name="Test Company",
        domain="https://test.example.com",
        industry="healthcare",
        generated_at="Test generation",
        period_label=f"28-Day Period ({CURRENT_START} to {CURRENT_END})",
        report_type=ReportType.PERFORMANCE_28D,
        branding={
            "primary_color": "#1E3A8A",
            "secondary_color": "#3B82F6",
            "accent_color": "#F59E0B",
        },
        analytics=analytics,
        insights=insights,
    )


def fake_discovery(
    suffix: str,
    *,
    category: str,
    metric_name: str,
    label: str,
    value: int | float | None,
    unit: str,
    title: str | None = None,
    insight: str = "Stored source context is available for the next optimization cycle.",
    recommended_action: str = "Review the stored source context during the next optimization cycle.",
    verification_status: str = "verified",
    claim_type: str = "observation",
) -> DataDiscovery:
    """Build a deterministic, approved discovery for client-surface tests."""

    evidence_id = f"ev-{suffix}"
    fact_id = f"fact-{suffix}"
    card_title = title or f"Test Discovery {suffix}"
    return DataDiscovery(
        proposal_id=f"proposal-{suffix}",
        title=card_title,
        source="gsc",
        insight=insight,
        recommended_action=recommended_action,
        client_card=ClientDiscoveryCard(
            title=card_title,
            what_we_noticed=insight,
            recommended_next_step=recommended_action,
        ),
        supporting_facts=[EvidenceCitation(evidence_id=evidence_id, fact_ids=[fact_id])],
        candidate=ExplorationCandidate(
            candidate_id=f"cand-{suffix}",
            claim_type=claim_type,
            category=category,
            label=label,
            source="gsc",
            evidence_id=evidence_id,
            fact_ids=[fact_id],
            metric_name=metric_name,
            unit=unit,
            value=value,
        ),
        verification_status=verification_status,
    )


def fake_completed_discovery_audit(discoveries: list[DataDiscovery]) -> ExplorationAudit:
    """Approve the supplied test discoveries using the same integrity contract."""

    records: list[EvidenceRecord] = []
    for discovery in discoveries:
        candidate = discovery.candidate
        assert candidate is not None
        records.append(
            EvidenceRecord(
                evidence_id=candidate.evidence_id,
                source=candidate.source,
                current=EvidencePeriod(
                    start_date=CURRENT_START,
                    end_date=CURRENT_END,
                    status=SourceAvailability.AVAILABLE,
                ),
                prior=EvidencePeriod(
                    start_date=PRIOR_START,
                    end_date=PRIOR_END,
                    status=SourceAvailability.AVAILABLE,
                ),
                facts=[
                    EvidenceFact(
                        fact_id=fact_id,
                        label=candidate.label,
                        value=candidate.value,
                        prior_value=candidate.prior_value,
                        metric_name=candidate.metric_name,
                        unit=candidate.unit,
                        operation=candidate.operation,
                        formula=candidate.formula,
                        calculation=candidate.calculation,
                        scope=candidate.scope,
                    )
                    for fact_id in candidate.fact_ids
                ],
            )
        )

    evidence = EvidenceBundle(
        client_id="test-client",
        period_start=CURRENT_START,
        period_end=CURRENT_END,
        prior_start=PRIOR_START,
        prior_end=PRIOR_END,
        records=records,
    )
    accepted_findings: list[DataDiscovery] = []
    verifier_decisions: list[ValidationDecision] = []
    for index, discovery in enumerate(discoveries):
        discovery.verification_status = "verified"
        fingerprint = approved_card_fingerprint(discovery, evidence, client_id="test-client")
        assert fingerprint is not None
        discovery.approved_card_fingerprint = fingerprint
        accepted_findings.append(discovery.model_copy(deep=True))
        verifier_decisions.append(
            ValidationDecision(
                discovery_index=index,
                proposal_id=discovery.proposal_id,
                candidate_id=discovery.candidate_id,
                status="approved",
                reasons=["approved"],
                approved_card_fingerprint=fingerprint,
            )
        )

    return ExplorationAudit(
        client_id="test-client",
        status="completed",
        verifier_status="completed",
        accepted_findings=accepted_findings,
        verifier_decisions=verifier_decisions,
        evidence=evidence,
    )


def fake_baseline_report_output() -> AIReportOutput:
    return AIReportOutput(
        executive_summary=[
            "The measurement property recorded a current observation for the initial baseline window.",
            "Organic Search is represented in the current channel snapshot.",
            "The current event snapshot includes configured inquiry activity.",
        ],
        biggest_win="The current observation establishes a starting point for future reporting.",
        watch_item="Continue collecting a complete measurement window before evaluating movement.",
        traffic_and_inflow_insights="The current analytics snapshot shows the observed visitor and page data available for this baseline.",
        conversion_insights="The current event snapshot shows the inquiry activity recorded during the observed window.",
        seo_and_content_opportunities="The current Search Console snapshot identifies available search topics for future prioritization.",
        local_seo_insights="GBP action metrics are unavailable from this connector.",
        agency_action_plan=[
            ActionItem(
                title="Continue measurement collection",
                description="Use the current observation as the starting point for the next complete comparison window.",
                impact_area="Technical",
                priority="High",
                evidence="Current source-backed baseline data",
            )
        ],
    )


def fake_weekly_output() -> WeeklyDigestOutput:
    return WeeklyDigestOutput(
        biggest_win="The configured analytics snapshot is available.",
        needs_attention=None,
        acquisition_insight="The configured acquisition channels are available.",
        conversion_insight="The current snapshot includes recorded customer actions; configured key conversions remain the separate GA4 conversion metric.",
        search_opportunity=None,
        local_insight="GBP action metrics are unavailable from this connector.",
        next_actions=[ActionItem(title="Review page data", description="Prioritize the configured page data.")],
    )


def fake_exploration_result(
    client: ClientConfig,
    enabled: bool = True,
    report_mode: ReportMode = ReportMode.COMPARISON,
    measurement_start_date: str | None = None,
    observation_start: str = CURRENT_START,
    observation_end: str = CURRENT_END,
    comparison_suppression_reason: str | None = None,
) -> ExplorationResult:
    bundle = EvidenceBundle(
        client_id=client.client_id,
        period_start=observation_start,
        period_end=observation_end,
        prior_start=PRIOR_START,
        prior_end=PRIOR_END,
        report_mode=report_mode,
        measurement_start_date=measurement_start_date,
        comparison_suppressed=report_mode == ReportMode.INITIAL_BASELINE,
        comparison_suppression_reason=comparison_suppression_reason,
    )
    audit = ExplorationAudit(
        client_id=client.client_id,
        enabled=enabled,
        status="completed" if enabled else "disabled",
        report_mode=report_mode,
        measurement_start_date=measurement_start_date,
        observation_window_start=observation_start,
        observation_window_end=observation_end,
        comparison_suppressed=report_mode == ReportMode.INITIAL_BASELINE,
        comparison_suppression_reason=comparison_suppression_reason,
        evidence=bundle,
        diagnostics=[],
    )
    return ExplorationResult(discoveries=[], audit=audit)
