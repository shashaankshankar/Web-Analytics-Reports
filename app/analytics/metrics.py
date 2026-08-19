from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.analytics.contracts import (
    ChannelPerformance,
    GrowthAnalysisInput,
    LocalInteractionData,
    MetricDelta,
    PagePerformance,
    StrikingDistanceKeyword,
)
from app.config import ClientConfig


def calculate_percentage_change(current: float, prior: float) -> Optional[float]:
    """Compute percentage change, returning None when prior is 0 or NaN."""
    if prior == 0 or math.isnan(prior) or math.isnan(current):
        return None
    return round(((current - prior) / abs(prior)) * 100.0, 1)


def determine_direction(current: float, prior: float, threshold: float = 0.001) -> str:
    diff = current - prior
    if abs(diff) <= threshold:
        return "flat"
    return "up" if diff > 0 else "down"


def calculate_date_ranges(days: int = 28, timezone_str: str = "America/New_York", today: Optional[date] = None) -> Tuple[str, str, str, str]:
    """Calculate [current_start, current_end, prior_start, prior_end] excluding incomplete local today."""
    if today is None:
        today = datetime.now(ZoneInfo(timezone_str)).date()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=days - 1)
    prior_end_date = start_date - timedelta(days=1)
    prior_start_date = prior_end_date - timedelta(days=days - 1)
    return (
        start_date.isoformat(),
        end_date.isoformat(),
        prior_start_date.isoformat(),
        prior_end_date.isoformat(),
    )


def filter_striking_distance_keywords(
    raw_queries: List[Dict[str, Any]],
    min_position: float = 8.0,
    max_position: float = 20.0,
    min_impressions: int = 10,
    top_n: int = 10,
) -> List[StrikingDistanceKeyword]:
    """Filter queries in striking distance (positions 8-20) ranked by opportunity score."""
    results = []
    for q in raw_queries:
        query = q.get("query", "").strip()
        if not query or len(query) < 3:
            continue
        pos = float(q.get("position", 0.0))
        imp = int(q.get("impressions", 0))
        clicks = int(q.get("clicks", 0))
        ctr = float(q.get("ctr", 0.0))

        if min_position <= pos <= max_position and imp >= min_impressions:
            # Opportunity score: Higher impressions and closer to page 1 rank higher
            # Formula: Impressions * (21 - Position)
            score = round(imp * max(1.0, (21.0 - pos)), 1)
            results.append(
                StrikingDistanceKeyword(
                    query=query,
                    impressions=imp,
                    clicks=clicks,
                    ctr=round(ctr * 100.0, 2),
                    position=round(pos, 1),
                    opportunity_score=score,
                )
            )
    # Sort descending by opportunity score
    results.sort(key=lambda x: x.opportunity_score, reverse=True)
    return results[:top_n]


def aggregate_growth_metrics(
    client: ClientConfig,
    start_date: str,
    end_date: str,
    prior_start_date: str,
    prior_end_date: str,
    ga4_data: Dict[str, Any],
    gsc_queries: List[Dict[str, Any]],
    gbp_data: Dict[str, Any],
) -> GrowthAnalysisInput:
    """Process and normalize all multi-source raw inputs into a validated GrowthAnalysisInput contract."""
    summary = ga4_data.get("summary", {})
    prior_summary = ga4_data.get("prior_summary", {})

    # Core metric deltas
    core_metrics = [
        MetricDelta(
            metric_name="sessions",
            display_name="Total Sessions",
            current_value=float(summary.get("sessions", 0)),
            prior_value=float(prior_summary.get("sessions", 0)),
            absolute_change=round(float(summary.get("sessions", 0)) - float(prior_summary.get("sessions", 0)), 1),
            percentage_change=calculate_percentage_change(float(summary.get("sessions", 0)), float(prior_summary.get("sessions", 0))),
            direction=determine_direction(float(summary.get("sessions", 0)), float(prior_summary.get("sessions", 0))),
            unit="count",
        ),
        MetricDelta(
            metric_name="active_users",
            display_name="Active Users",
            current_value=float(summary.get("activeUsers", 0)),
            prior_value=float(prior_summary.get("activeUsers", 0)),
            absolute_change=round(float(summary.get("activeUsers", 0)) - float(prior_summary.get("activeUsers", 0)), 1),
            percentage_change=calculate_percentage_change(float(summary.get("activeUsers", 0)), float(prior_summary.get("activeUsers", 0))),
            direction=determine_direction(float(summary.get("activeUsers", 0)), float(prior_summary.get("activeUsers", 0))),
            unit="count",
        ),
        MetricDelta(
            metric_name="engagement_rate",
            display_name="Engagement Rate",
            current_value=round(float(summary.get("engagementRate", 0.0)) * 100.0, 1),
            prior_value=round(float(prior_summary.get("engagementRate", 0.0)) * 100.0, 1),
            absolute_change=round((float(summary.get("engagementRate", 0.0)) - float(prior_summary.get("engagementRate", 0.0))) * 100.0, 1),
            percentage_change=calculate_percentage_change(float(summary.get("engagementRate", 0.0)), float(prior_summary.get("engagementRate", 0.0))),
            direction=determine_direction(float(summary.get("engagementRate", 0.0)), float(prior_summary.get("engagementRate", 0.0))),
            unit="percentage",
        ),
        MetricDelta(
            metric_name="conversions",
            display_name="Total Key Conversions",
            current_value=float(summary.get("conversions", 0)),
            prior_value=float(prior_summary.get("conversions", 0)),
            absolute_change=round(float(summary.get("conversions", 0)) - float(prior_summary.get("conversions", 0)), 1),
            percentage_change=calculate_percentage_change(float(summary.get("conversions", 0)), float(prior_summary.get("conversions", 0))),
            direction=determine_direction(float(summary.get("conversions", 0)), float(prior_summary.get("conversions", 0))),
            unit="count",
        ),
    ]

    # Channels
    channels = []
    for ch in ga4_data.get("channels", []):
        sess = int(ch.get("sessions", 0))
        prior_sess = int(ch.get("priorSessions", 0))
        channels.append(
            ChannelPerformance(
                channel=ch.get("channel", "Direct"),
                sessions=sess,
                active_users=int(ch.get("activeUsers", 0)),
                prior_sessions=prior_sess,
                session_change=sess - prior_sess,
                percentage_change=calculate_percentage_change(float(sess), float(prior_sess)),
                conversions=int(ch.get("conversions", 0)),
            )
        )
    # Sort channels by sessions descending
    channels.sort(key=lambda x: x.sessions, reverse=True)

    # Pages & High-Intent identification
    pages = []
    high_intent_keywords = {"contact", "book", "schedule", "quote", "pricing", "services", "treatment", "signup", "appointment"}
    for pg in ga4_data.get("pages", []):
        path = pg.get("pagePath", "/")
        sess = int(pg.get("sessions", 0))
        prior_sess = int(pg.get("priorSessions", 0))
        is_intent = any(kw in path.lower() for kw in high_intent_keywords)
        pages.append(
            PagePerformance(
                page_path=path,
                sessions=sess,
                active_users=int(pg.get("activeUsers", 0)),
                prior_sessions=prior_sess,
                session_change=sess - prior_sess,
                is_high_intent=is_intent,
            )
        )
    pages.sort(key=lambda x: x.sessions, reverse=True)

    # Striking distance keywords from GSC
    striking_keywords = filter_striking_distance_keywords(gsc_queries)

    # Local SEO / GBP metrics
    local_seo = LocalInteractionData(
        phone_calls=int(gbp_data.get("phone_calls", 0)),
        prior_phone_calls=int(gbp_data.get("prior_phone_calls", 0)),
        direction_requests=int(gbp_data.get("direction_requests", 0)),
        prior_direction_requests=int(gbp_data.get("prior_direction_requests", 0)),
        website_clicks=int(gbp_data.get("website_clicks", 0)),
        prior_website_clicks=int(gbp_data.get("prior_website_clicks", 0)),
        average_rating=gbp_data.get("average_rating"),
        total_reviews_count=gbp_data.get("total_reviews_count"),
        recent_review_snippets=gbp_data.get("recent_review_snippets", []),
    )

    return GrowthAnalysisInput(
        client_id=client.client_id,
        company_name=client.company_name,
        domain=client.domain,
        industry=client.industry,
        period_start=start_date,
        period_end=end_date,
        comparison_start=prior_start_date,
        comparison_end=prior_end_date,
        monthly_retainer_focus=client.monthly_retainer_focus,
        core_metrics=core_metrics,
        top_channels=channels[:8],
        top_pages=pages[:10],
        striking_distance_keywords=striking_keywords,
        local_seo=local_seo,
        raw_summary_stats={
            "events": ga4_data.get("events", {}),
            "prior_events": ga4_data.get("prior_events", {}),
        },
    )
