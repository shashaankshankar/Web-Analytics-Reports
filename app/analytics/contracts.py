from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field


class MetricDelta(BaseModel):
    metric_name: str
    display_name: str
    current_value: float
    prior_value: float
    absolute_change: float
    percentage_change: Optional[float] = None  # None if prior was 0
    direction: str  # "up", "down", "flat"
    unit: str = "count"  # "count", "currency", "percentage", "seconds"


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


class LocalInteractionData(BaseModel):
    phone_calls: int = 0
    prior_phone_calls: int = 0
    direction_requests: int = 0
    prior_direction_requests: int = 0
    website_clicks: int = 0
    prior_website_clicks: int = 0
    average_rating: Optional[float] = None
    total_reviews_count: Optional[int] = None
    recent_review_snippets: List[str] = Field(default_factory=list)


class GrowthAnalysisInput(BaseModel):
    """Normalized, deterministic data contract passed to AI Growth Analyst."""
    client_id: str
    company_name: str
    domain: str
    industry: str
    period_start: str
    period_end: str
    comparison_start: str
    comparison_end: str
    monthly_retainer_focus: str = ""
    
    core_metrics: List[MetricDelta] = Field(default_factory=list)
    top_channels: List[ChannelPerformance] = Field(default_factory=list)
    top_pages: List[PagePerformance] = Field(default_factory=list)
    striking_distance_keywords: List[StrikingDistanceKeyword] = Field(default_factory=list)
    local_seo: LocalInteractionData = Field(default_factory=LocalInteractionData)
    raw_summary_stats: dict[str, Any] = Field(default_factory=dict)


class ActionItem(BaseModel):
    title: str
    description: str
    impact_area: str  # "SEO", "Conversion", "Content", "Local", "Technical"
    priority: str = "High"  # "High", "Medium", "Low"


class AIReportOutput(BaseModel):
    """Structured growth intelligence briefing produced by AI."""
    executive_summary: List[str] = Field(..., description="Top 3 plain-English takeaways")
    traffic_and_inflow_insights: str = Field(..., description="Analysis of channels, users, and high-intent pages")
    seo_and_content_opportunities: str = Field(..., description="Striking-distance keyword targets and new service pages")
    local_seo_insights: str = Field(default="", description="Analysis of GBP interactions, reviews, and local discovery")
    agency_action_plan: List[ActionItem] = Field(..., description="2-4 concrete optimizations for upcoming month justifying retainer")
    overall_sentiment: str = Field(default="Growth", description="Positive, Moderate, Critical, Growth")


class FullGrowthBriefing(BaseModel):
    """Complete report combining client config, deterministic analytics, and AI insights."""
    client_id: str
    company_name: str
    domain: str
    industry: str
    generated_at: str
    period_label: str
    branding: dict[str, Any]
    analytics: GrowthAnalysisInput
    insights: AIReportOutput
