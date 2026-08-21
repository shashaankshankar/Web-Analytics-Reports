from __future__ import annotations

from enum import Enum
from typing import Any, List, Mapping, Optional
from pydantic import BaseModel, Field, model_validator


class ReportType(str, Enum):
    WEEKLY = "weekly"
    PERFORMANCE_28D = "performance"


class ReportSpec(BaseModel):
    report_type: ReportType
    display_name: str
    default_days: int
    requires_pdf: bool = True
    max_actions: int = 4
    enable_deep_agent_default: bool = False


REPORT_SPECS: dict[ReportType, ReportSpec] = {
    ReportType.WEEKLY: ReportSpec(
        report_type=ReportType.WEEKLY,
        display_name="Weekly Growth Digest",
        default_days=7,
        requires_pdf=False,
        max_actions=2,
        enable_deep_agent_default=False,
    ),
    ReportType.PERFORMANCE_28D: ReportSpec(
        report_type=ReportType.PERFORMANCE_28D,
        display_name="28-Day Performance Report",
        default_days=28,
        requires_pdf=True,
        max_actions=4,
        enable_deep_agent_default=False,
    ),
}


class MetricDelta(BaseModel):
    metric_name: str
    display_name: str
    current_value: float
    prior_value: float
    absolute_change: float
    percentage_change: Optional[float] = None  # None if prior was 0
    percentage_points_change: Optional[float] = None  # Absolute percentage point diff for rates
    is_percentage_rate: bool = False
    direction: str  # "up", "down", "flat"
    unit: str = "count"  # "count", "currency", "percentage", "seconds"


class ConversionEventSummary(BaseModel):
    event_name: str
    display_name: str
    current_count: int
    prior_count: int
    count_change: int
    percentage_change: Optional[float] = None
    direction: str = "flat"


class ChannelPerformance(BaseModel):
    channel: str
    sessions: int
    active_users: int
    prior_sessions: int
    session_change: int
    percentage_change: Optional[float] = None
    conversions: int = 0


class PagePerformance(BaseModel):
    page_path: str
    sessions: int
    active_users: int
    prior_sessions: int
    session_change: int
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
    prior_impressions: int = 0
    impressions_change: int = 0
    current_clicks: int = 0
    prior_clicks: int = 0
    clicks_change: int = 0
    mover_type: str = "neutral"


class LocalInteractionData(BaseModel):
    phone_calls: int = 0
    prior_phone_calls: int = 0
    phone_calls_change: int = 0
    phone_calls_percentage_change: Optional[float] = None
    phone_calls_direction: str = "flat"

    direction_requests: int = 0
    prior_direction_requests: int = 0
    direction_requests_change: int = 0
    direction_requests_percentage_change: Optional[float] = None
    direction_requests_direction: str = "flat"

    website_clicks: int = 0
    prior_website_clicks: int = 0
    website_clicks_change: int = 0
    website_clicks_percentage_change: Optional[float] = None
    website_clicks_direction: str = "flat"

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
    period_days: int = 28
    period_start: str
    period_end: str
    comparison_start: str
    comparison_end: str
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
    local_seo: LocalInteractionData = Field(default_factory=LocalInteractionData)
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


class DataDiscovery(BaseModel):
    """Autonomous data discovery produced by exploratory multi-source tool analysis."""
    title: str = Field(..., description="High-level discovery headline")
    source: str = Field(..., description="Data source (e.g., 'GA4 Device Breakdown', 'Search Console Long-Tail', 'GBP Interaction Shift')")
    insight: str = Field(..., description="Detailed analytical insight derived from dynamic querying")
    recommended_action: str = Field(..., description="Tactical growth action to exploit this discovery")


class WeeklyDigestOutput(BaseModel):
    """Structured concise growth intelligence produced for 7-day weekly digests."""
    biggest_win: str = Field(..., description="One meaningful positive movement supported by data (1-2 sentences)")
    needs_attention: Optional[str] = Field(default=None, description="One meaningful negative movement or stalled item, or None if overall healthy")
    acquisition_insight: str = Field(..., description="Top channel movement & traffic driver summary (1-2 sentences)")
    search_opportunity: Optional[str] = Field(default=None, description="One striking-distance or trending search opportunity (1-2 sentences)")
    local_insight: Optional[str] = Field(default=None, description="GBP interaction movement (calls, directions) or None if unconfigured")
    next_actions: List[ActionItem] = Field(default_factory=list, description="Max 2 concrete optimizations for the upcoming week")
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
    agency_action_plan: List[ActionItem] = Field(..., description="2-4 concrete optimizations for upcoming cycle justifying retainer")
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
    branding: dict[str, Any]
    analytics: GrowthAnalysisInput
    insights: AIReportOutput
    weekly_insights: Optional[WeeklyDigestOutput] = None
