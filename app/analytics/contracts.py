from __future__ import annotations

from enum import Enum
from typing import Any, List, Literal, Mapping, Optional
from pydantic import BaseModel, Field, model_validator


class ReportType(str, Enum):
    WEEKLY = "weekly"
    PERFORMANCE_28D = "performance"


class ReportMode(str, Enum):
    """Whether a report contains a valid comparison or an initial baseline."""

    COMPARISON = "comparison"
    INITIAL_BASELINE = "initial_baseline"


class SourceAvailability(str, Enum):
    """Honest state for a source query or period."""

    AVAILABLE = "available"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ReportSpec(BaseModel):
    report_type: ReportType
    display_name: str
    default_days: int
    requires_pdf: bool = True
    max_actions: int = 3
    enable_deep_agent_default: bool = False


REPORT_SPECS: dict[ReportType, ReportSpec] = {
    ReportType.WEEKLY: ReportSpec(
        report_type=ReportType.WEEKLY,
        display_name="Weekly Growth Digest",
        default_days=7,
        requires_pdf=False,
        max_actions=0,
        enable_deep_agent_default=False,
    ),
    ReportType.PERFORMANCE_28D: ReportSpec(
        report_type=ReportType.PERFORMANCE_28D,
        display_name="28-Day Performance Report",
        default_days=28,
        requires_pdf=True,
        max_actions=3,
        enable_deep_agent_default=False,
    ),
}


class MetricDelta(BaseModel):
    metric_name: str
    display_name: str
    current_value: float
    prior_value: Optional[float] = None
    absolute_change: Optional[float] = None
    percentage_change: Optional[float] = None  # None if prior was 0
    percentage_points_change: Optional[float] = None  # Absolute percentage point diff for rates
    is_percentage_rate: bool = False
    direction: str = "unavailable"  # "up", "down", "flat", "unavailable"
    unit: str = "count"  # "count", "currency", "percentage", "seconds"


class ConversionEventSummary(BaseModel):
    event_name: str
    display_name: str
    current_count: int
    prior_count: Optional[int] = None
    count_change: Optional[int] = None
    percentage_change: Optional[float] = None
    direction: str = "unavailable"


class ChannelPerformance(BaseModel):
    channel: str
    sessions: int
    active_users: int
    prior_sessions: Optional[int] = None
    session_change: Optional[int] = None
    percentage_change: Optional[float] = None
    conversions: int = 0


class PagePerformance(BaseModel):
    page_path: str
    sessions: int
    active_users: int
    prior_sessions: Optional[int] = None
    session_change: Optional[int] = None
    is_high_intent: bool = False


class StrikingDistanceKeyword(BaseModel):
    query: str
    impressions: int
    clicks: int
    ctr: float
    position: float
    opportunity_score: float  # computed metric based on impressions & position


class SearchQueryMover(BaseModel):
    query: str
    current_position: float
    prior_position: Optional[float] = None
    position_change: Optional[float] = None  # negative is rank improvement
    current_impressions: int
    prior_impressions: Optional[int] = None
    impressions_change: Optional[int] = None
    current_clicks: int = 0
    prior_clicks: Optional[int] = None
    clicks_change: Optional[int] = None
    mover_type: str = "neutral"


class LocalInteractionData(BaseModel):
    phone_calls: Optional[int] = None
    prior_phone_calls: Optional[int] = None
    phone_calls_change: Optional[int] = None
    phone_calls_percentage_change: Optional[float] = None
    phone_calls_direction: str = "unavailable"

    direction_requests: Optional[int] = None
    prior_direction_requests: Optional[int] = None
    direction_requests_change: Optional[int] = None
    direction_requests_percentage_change: Optional[float] = None
    direction_requests_direction: str = "unavailable"

    website_clicks: Optional[int] = None
    prior_website_clicks: Optional[int] = None
    website_clicks_change: Optional[int] = None
    website_clicks_percentage_change: Optional[float] = None
    website_clicks_direction: str = "unavailable"

    # GBP profile and source capability metadata. These remain dictionaries so
    # the contract can preserve Google's nested address, hours, category, and
    # service structures without flattening or inventing fields.
    profile_status: str = SourceAvailability.UNAVAILABLE.value
    profile: dict[str, Any] = Field(default_factory=dict)

    # Daily Performance API series and deterministic period totals/deltas.
    performance_status: str = SourceAvailability.UNAVAILABLE.value
    performance_metrics: dict[str, Any] = Field(default_factory=dict)
    prior_performance_metrics: dict[str, Any] = Field(default_factory=dict)
    available_performance_metrics: List[str] = Field(default_factory=list)
    performance_metric_deltas: List[MetricDelta] = Field(default_factory=list)

    # Monthly keyword counts may contain threshold values instead of exact
    # counts; consumers must retain the value type supplied by Google.
    search_keywords_status: str = SourceAvailability.UNAVAILABLE.value
    monthly_search_keywords: List[dict[str, Any]] = Field(default_factory=list)
    prior_monthly_search_keywords: List[dict[str, Any]] = Field(default_factory=list)

    # Managed Reviews API inventory and reply state.
    reviews_status: str = SourceAvailability.UNAVAILABLE.value
    reviews: List[dict[str, Any]] = Field(default_factory=list)
    review_inventory_complete: bool = False
    review_response_summary: dict[str, Any] = Field(default_factory=dict)
    prior_review_response_summary: dict[str, Any] = Field(default_factory=dict)

    # Business Calls is optional and may be disabled or unavailable for a
    # location. None means the source did not provide a count.
    business_calls_status: str = SourceAvailability.UNAVAILABLE.value
    business_calls: dict[str, Any] = Field(default_factory=dict)
    answered_calls: Optional[int] = None
    prior_answered_calls: Optional[int] = None
    answered_calls_change: Optional[int] = None
    answered_calls_percentage_change: Optional[float] = None
    answered_calls_direction: str = "unavailable"
    missed_calls: Optional[int] = None
    prior_missed_calls: Optional[int] = None
    missed_calls_change: Optional[int] = None
    missed_calls_percentage_change: Optional[float] = None
    missed_calls_direction: str = "unavailable"

    average_rating: Optional[float] = None
    total_reviews_count: Optional[int] = None
    recent_review_snippets: List[str] = Field(default_factory=list)


class GrowthAnalysisInput(BaseModel):
    """Normalized, deterministic data contract passed to AI Growth Analyst and renderers."""
    client_id: str
    company_name: str
    domain: str
    industry: str
    report_type: ReportType = ReportType.PERFORMANCE_28D
    report_mode: ReportMode = ReportMode.COMPARISON
    period_days: int = 28
    observed_days: Optional[int] = None
    period_start: str
    period_end: str
    comparison_start: str
    comparison_end: str
    requested_period_start: Optional[str] = None
    requested_period_end: Optional[str] = None
    requested_comparison_start: Optional[str] = None
    requested_comparison_end: Optional[str] = None
    measurement_start_date: Optional[str] = None
    comparison_suppressed: bool = False
    comparison_suppression_reason: Optional[str] = None
    goals: list[str] = Field(default_factory=list, description="Client goals and growth priorities")

    core_metrics: List[MetricDelta] = Field(default_factory=list)
    conversion_rate: Optional[MetricDelta] = None
    conversion_events: List[ConversionEventSummary] = Field(default_factory=list)
    top_channels: List[ChannelPerformance] = Field(default_factory=list)
    top_pages: List[PagePerformance] = Field(default_factory=list)
    page_gainers: List[PagePerformance] = Field(default_factory=list)
    page_decliners: List[PagePerformance] = Field(default_factory=list)
    striking_distance_keywords: List[StrikingDistanceKeyword] = Field(default_factory=list)
    search_movers: List[SearchQueryMover] = Field(default_factory=list)
    search_comparison_status: SourceAvailability = SourceAvailability.UNAVAILABLE
    search_comparison_truncated: bool = False
    search_comparison_diagnostics: list[str] = Field(default_factory=list)
    local_seo: LocalInteractionData = Field(default_factory=LocalInteractionData)
    source_statuses: dict[str, Any] = Field(
        default_factory=dict,
        description="Current/prior availability and diagnostic state for each configured source.",
    )
    source_diagnostics: dict[str, str] = Field(default_factory=dict)
    raw_summary_stats: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def reject_removed_monthly_focus(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and "monthly_retainer_focus" in data:
            raise ValueError("monthly_retainer_focus is no longer supported; use goals instead")
        return data


class ActionItem(BaseModel):
    title: str
    description: str
    impact_area: str = "SEO"  # "SEO", "Conversion", "Content", "Local", "Technical"
    priority: str = "High"  # "High", "Medium", "Low"
    evidence: str = ""


class EvidenceFact(BaseModel):
    """One source-backed scalar retained for deterministic discovery checks."""

    fact_id: str
    label: str
    value: int | float | str | bool | None = None
    prior_value: int | float | str | bool | None = None
    metric_name: str = ""
    unit: str = ""
    operation: Literal["value", "change", "percent_change", "ratio", "share"] = "value"
    source_path: str = ""
    formula: str | None = None
    calculation: Literal["source_value", "difference", "percent_change", "ratio", "share"] = "source_value"
    depends_on_fact_ids: list[str] = Field(default_factory=list)
    scope: dict[str, str] = Field(default_factory=dict)


class ExplorationCandidate(BaseModel):
    """A deterministic, source-backed finding candidate exposed to the explorer."""

    candidate_id: str
    claim_type: Literal["observation", "comparison", "ratio", "share"]
    category: str
    label: str
    source: str
    evidence_id: str
    fact_ids: list[str] = Field(default_factory=list, min_length=1)
    source_fact_ids: list[str] = Field(default_factory=list)
    metric_name: str
    unit: str
    period: Literal["current", "prior", "both"] = "current"
    operation: Literal["value", "change", "percent_change", "ratio", "share"] = "value"
    formula: str | None = None
    calculation: Literal["source_value", "difference", "percent_change", "ratio", "share"] = "source_value"
    value: int | float | str | bool | None = None
    current_value: int | float | str | bool | None = None
    prior_value: int | float | str | bool | None = None
    relation: Literal["observed", "increase", "decrease", "flat", "ratio", "share"] = "observed"
    eligible: bool = True
    eligibility_reasons: list[str] = Field(default_factory=list)
    scope: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_fact_ids(self) -> "ExplorationCandidate":
        if not self.fact_ids and self.source_fact_ids:
            self.fact_ids = list(self.source_fact_ids)
        if not self.source_fact_ids:
            self.source_fact_ids = list(self.fact_ids)
        if self.source_fact_ids != self.fact_ids:
            raise ValueError("fact_ids and source_fact_ids must identify the same source facts")
        return self


class EvidencePeriod(BaseModel):
    """Exact normalized response for one requested reporting period."""

    start_date: str
    end_date: str
    status: SourceAvailability
    row_count: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    reason: str | None = None


class EvidenceRecord(BaseModel):
    """Evidence retained for one exploratory tool invocation."""

    evidence_id: str
    source: str
    query: dict[str, Any] = Field(default_factory=dict)
    current: EvidencePeriod
    prior: EvidencePeriod | None = None
    facts: list[EvidenceFact] = Field(default_factory=list)
    candidates: list[ExplorationCandidate] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    candidate_eligibility_blocked: bool = False


class EvidenceBundle(BaseModel):
    """Tenant- and period-scoped evidence returned by the explorer tools."""

    client_id: str
    period_start: str
    period_end: str
    prior_start: str
    prior_end: str
    report_mode: ReportMode = ReportMode.COMPARISON
    measurement_start_date: str | None = None
    requested_period_start: str | None = None
    requested_period_end: str | None = None
    requested_comparison_start: str | None = None
    requested_comparison_end: str | None = None
    comparison_suppressed: bool = False
    comparison_suppression_reason: str | None = None
    records: list[EvidenceRecord] = Field(default_factory=list)
    candidates: list[ExplorationCandidate] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class EvidenceCitation(BaseModel):
    """A discovery citation naming an evidence record and its supporting facts."""

    evidence_id: str
    fact_ids: list[str] = Field(..., min_length=1)
    period: Literal["current", "prior", "both"] = "current"


class NumericClaim(BaseModel):
    """A typed numeric phrase whose metric identity and calculation are auditable."""

    claim_id: str = ""
    field: Literal["title", "what_we_noticed", "recommended_next_step"]
    claim_text: str = Field(..., min_length=1, description="Exact numeric phrase used in one client-card field")
    span_start: int | None = None
    span_end: int | None = None
    value: int | float
    metric_name: str = Field(..., description="Canonical metric represented by the number")
    unit: str = Field(..., description="Unit such as count, percent, position, or rating")
    operation: Literal["value", "change", "percent_change", "ratio", "share"] = "value"
    evidence_id: str
    fact_ids: list[str] = Field(..., min_length=1)
    period: Literal["current", "prior", "both"] = "current"
    formula: str | None = None


class ClientDiscoveryCard(BaseModel):
    """The complete client-facing card authored by the exploratory model."""

    title: str = Field(..., min_length=1, description="Concise professional client-facing headline")
    what_we_noticed: str = Field(..., min_length=1, description="Plain-language observation grounded in the selected candidate")
    recommended_next_step: str = Field(..., min_length=1, description="Bounded, actionable next step grounded in the selected candidate")


class DiscoveryProposal(BaseModel):
    """Model-authored selection and complete client card; candidate facts stay authoritative."""

    proposal_id: str = ""
    candidate_id: str
    title: str = Field(..., min_length=1)
    what_we_noticed: str = Field(..., min_length=1)
    recommended_next_step: str = Field(..., min_length=1)
    numeric_claims: list[NumericClaim] = Field(default_factory=list)
    rank: int | None = None


class DataDiscovery(BaseModel):
    """Autonomous discovery with the model-authored card preserved for audit and rendering."""
    proposal_id: str = Field(default="", description="Stable identifier for this model proposal")
    title: str = Field(default="", description="Legacy mirror of client_card.title; never authored by a renderer")
    source: str = Field(..., description="Data source (e.g., 'GA4 Device Breakdown', 'Search Console Long-Tail', 'GBP Interaction Shift')")
    insight: str = Field(default="", description="Legacy mirror of client_card.what_we_noticed")
    recommended_action: str = Field(default="", description="Legacy mirror of client_card.recommended_next_step")
    client_card: ClientDiscoveryCard | None = Field(
        default=None,
        description="Complete model-authored client card; this is the canonical client copy.",
    )
    supporting_facts: list[EvidenceCitation] = Field(
        default_factory=list, description="Evidence IDs and fact IDs that directly support the finding"
    )
    reported_values: list[int | float] = Field(
        default_factory=list,
        description="Numeric values stated by the finding; each must match cited evidence exactly",
    )
    numeric_claims: list[NumericClaim] = Field(
        default_factory=list,
        description="Typed evidence mapping for every numeric claim in the narrative",
    )
    candidate_id: str = ""
    candidate: ExplorationCandidate | None = None
    claim_type: str = ""
    category: str = ""
    source_fact_ids: list[str] = Field(default_factory=list)
    metric_name: str = ""
    unit: str = ""
    period: Literal["current", "prior", "both"] = "current"
    operation: Literal["value", "change", "percent_change", "ratio", "share"] = "value"
    formula: str | None = None
    relation: str = "observed"
    deterministic_eligible: bool = True
    eligibility_reasons: list[str] = Field(default_factory=list)
    narrative_lint_warnings: list[str] = Field(default_factory=list)
    deterministic_headline: str = ""
    deterministic_context: str = ""
    verification_status: Literal["unverified", "verified"] = "unverified"
    approved_card_fingerprint: str | None = None

    @model_validator(mode="before")
    @classmethod
    def preserve_client_card(cls, data: Any) -> Any:
        """Keep older callers readable while making the model card explicit and canonical."""

        if not isinstance(data, Mapping):
            return data
        values = dict(data)
        card = values.get("client_card")
        legacy_fields = {
            "title": values.get("title", ""),
            "what_we_noticed": values.get("insight", ""),
            "recommended_next_step": values.get("recommended_action", ""),
        }
        if card is not None:
            card_values = card.model_dump() if isinstance(card, ClientDiscoveryCard) else dict(card)
            mirrors = {
                "title": "title",
                "what_we_noticed": "insight",
                "recommended_next_step": "recommended_action",
            }
            for card_key, legacy_key in mirrors.items():
                card_value = card_values.get(card_key, "")
                legacy_value = values.get(legacy_key, "")
                if str(legacy_value or "").strip() and legacy_value != card_value:
                    raise ValueError(f"DataDiscovery {legacy_key} must match client_card.{card_key}.")
                values[legacy_key] = card_value
        return values

    @model_validator(mode="after")
    def mirror_client_card(self) -> "DataDiscovery":
        """Expose the card through legacy fields without generating or rewriting its prose."""

        if self.client_card is None:
            return self
        if self.title != self.client_card.title:
            raise ValueError("DataDiscovery title must match client_card.title.")
        if self.insight != self.client_card.what_we_noticed:
            raise ValueError("DataDiscovery insight must match client_card.what_we_noticed.")
        if self.recommended_action != self.client_card.recommended_next_step:
            raise ValueError("DataDiscovery recommended_action must match client_card.recommended_next_step.")
        return self


class ValidationDecision(BaseModel):
    """Deterministic or verifier decision for one proposed discovery."""

    discovery_index: int
    proposal_id: str = ""
    candidate_id: str = ""
    status: Literal["approved", "rejected", "deterministically_accepted"]
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    approved_card_fingerprint: str | None = None


class LintWarningRecord(BaseModel):
    """Audit-only lint warnings retaining the source proposal's stable identity."""

    discovery_index: int
    proposal_id: str = ""
    warnings: list[str] = Field(default_factory=list)


class ExplorationAudit(BaseModel):
    """Internal audit record; never used as a source of client-facing findings."""

    client_id: str
    enabled: bool = True
    status: str
    report_mode: ReportMode = ReportMode.COMPARISON
    measurement_start_date: str | None = None
    observation_window_start: str | None = None
    observation_window_end: str | None = None
    comparison_suppressed: bool = False
    comparison_suppression_reason: str | None = None
    source_statuses: dict[str, Any] = Field(default_factory=dict)
    evidence: EvidenceBundle
    proposed_findings: list[dict[str, Any]] = Field(default_factory=list)
    deterministic_decisions: list[ValidationDecision] = Field(default_factory=list)
    verifier_status: str | None = None
    verifier_decisions: list[ValidationDecision] = Field(default_factory=list)
    accepted_findings: list[DataDiscovery] = Field(default_factory=list)
    accepted_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    lint_warnings: list[LintWarningRecord] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class ExplorationResult(BaseModel):
    """Validated exploratory output plus the evidence audit for one run."""

    discoveries: list[DataDiscovery] = Field(default_factory=list)
    audit: ExplorationAudit

    def __iter__(self):
        return iter(self.discoveries)

    def __len__(self) -> int:
        return len(self.discoveries)


class WeeklyDigestOutput(BaseModel):
    """Structured concise growth intelligence produced for 7-day weekly digests."""
    biggest_win: str = Field(..., description="One meaningful positive movement supported by data (1-2 sentences)")
    needs_attention: Optional[str] = Field(default=None, description="One meaningful negative movement or stalled item, or None if overall healthy")
    acquisition_insight: str = Field(..., description="Top channel movement & traffic driver summary (1-2 sentences)")
    conversion_insight: str = Field(default="", description="Plain-English summary of recorded customer/contact actions and configured key conversions")
    search_opportunity: Optional[str] = Field(default=None, description="One striking-distance or trending search opportunity (1-2 sentences)")
    local_insight: Optional[str] = Field(default=None, description="GBP interaction movement (calls, directions) or None if unconfigured")
    overall_sentiment: str = Field(default="Growth", description="Positive, Moderate, Critical, Growth")


class AIReportOutput(BaseModel):
    """Structured growth intelligence briefing produced for 28-day performance reports."""
    executive_summary: List[str] = Field(..., description="Top 3 plain-English takeaways")
    biggest_win: str = Field(default="", description="One meaningful positive movement supported by data")
    watch_item: Optional[str] = Field(default=None, description="Primary risk or watch item needing attention")
    traffic_and_inflow_insights: str = Field(..., description="Analysis of channels, users, and high-intent pages")
    conversion_insights: str = Field(default="", description="Analysis of conversion rate, key events, and funnel effectiveness")
    seo_and_content_opportunities: str = Field(..., description="Striking-distance keyword targets and new service pages")
    local_seo_insights: str = Field(default="", description="Analysis of GBP interactions, reviews, and local discovery")
    agency_action_plan: List[ActionItem] = Field(..., description="Up to 3 strongest evidence-backed optimizations for the upcoming cycle")
    deep_discoveries: List[DataDiscovery] = Field(default_factory=list, description="Client-specific discoveries from exploratory multi-source agent tools")
    overall_sentiment: str = Field(default="Growth", description="Positive, Moderate, Critical, Growth")


class FullGrowthBriefing(BaseModel):
    """Complete report combining client config, deterministic analytics, and AI insights."""
    client_id: str
    company_name: str
    domain: str
    industry: str
    generated_at: str
    period_label: str
    report_type: ReportType = ReportType.PERFORMANCE_28D
    report_mode: ReportMode = ReportMode.COMPARISON
    measurement_start_date: Optional[str] = None
    observation_window_start: Optional[str] = None
    observation_window_end: Optional[str] = None
    requested_period_start: Optional[str] = None
    requested_period_end: Optional[str] = None
    requested_comparison_start: Optional[str] = None
    requested_comparison_end: Optional[str] = None
    comparison_suppressed: bool = False
    comparison_suppression_reason: Optional[str] = None
    branding: dict[str, Any]
    analytics: GrowthAnalysisInput
    insights: AIReportOutput
    weekly_insights: Optional[WeeklyDigestOutput] = None
    exploration_audit: Optional[ExplorationAudit] = None
