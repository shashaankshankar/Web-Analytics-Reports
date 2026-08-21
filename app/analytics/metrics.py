from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.analytics.contracts import (
    ChannelPerformance,
    ConversionEventSummary,
    GrowthAnalysisInput,
    LocalInteractionData,
    MetricDelta,
    PagePerformance,
    ReportType,
    SearchQueryMover,
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
            expected_ctr = max(0.01, min(0.05, 0.05 - (pos - 8.0) * 0.003))
            ctr_opportunity_multiplier = 1.0 + max(0.0, (expected_ctr - ctr) / expected_ctr)
            base_score = imp * max(1.0, (21.0 - pos))
            score = round(base_score * ctr_opportunity_multiplier, 1)
            results.append(
                StrikingDistanceKeyword(
                    query=query,
                    impressions=imp,
                    clicks=clicks,
                    ctr=round(ctr * 100.0, 2) if ctr <= 1.0 else round(ctr, 2),
                    position=round(pos, 1),
                    opportunity_score=score,
                )
            )
    results.sort(key=lambda x: x.opportunity_score, reverse=True)
    return results[:top_n]


def calculate_search_movers(
    current_queries: List[Dict[str, Any]],
    prior_queries: Optional[List[Dict[str, Any]]] = None,
    top_n: int = 5,
) -> List[SearchQueryMover]:
    """Calculate query movements between current and prior periods."""
    if not prior_queries:
        # Fallback to current snapshot opportunities when prior is absent
        movers: List[SearchQueryMover] = []
        for q in current_queries[:top_n]:
            query = q.get("query", "").strip()
            if not query:
                continue
            movers.append(
                SearchQueryMover(
                    query=query,
                    current_position=round(float(q.get("position", 0.0)), 1),
                    prior_position=None,
                    position_change=None,
                    current_impressions=int(q.get("impressions", 0)),
                    prior_impressions=0,
                    impressions_change=int(q.get("impressions", 0)),
                    current_clicks=int(q.get("clicks", 0)),
                    prior_clicks=0,
                    clicks_change=int(q.get("clicks", 0)),
                    mover_type="striking_distance",
                )
            )
        return movers

    prior_map = {q.get("query", "").strip().lower(): q for q in prior_queries if q.get("query")}
    movers = []

    for q in current_queries:
        query = q.get("query", "").strip()
        if not query:
            continue
        q_lower = query.lower()
        curr_pos = float(q.get("position", 0.0))
        curr_imp = int(q.get("impressions", 0))
        curr_clk = int(q.get("clicks", 0))

        prior_item = prior_map.get(q_lower)
        if prior_item:
            prior_pos = float(prior_item.get("position", 0.0))
            prior_imp = int(prior_item.get("impressions", 0))
            prior_clk = int(prior_item.get("clicks", 0))

            pos_diff = round(curr_pos - prior_pos, 1)  # negative = position improved (closer to 1)
            imp_diff = curr_imp - prior_imp
            clk_diff = curr_clk - prior_clk

            if pos_diff <= -1.5 and curr_imp >= 20:
                mover_type = "ranking_gain"
            elif pos_diff >= 2.0 and curr_imp >= 20:
                mover_type = "ranking_drop"
            elif imp_diff >= 100:
                mover_type = "impression_spike"
            elif 8.0 <= curr_pos <= 20.0:
                mover_type = "striking_distance"
            else:
                mover_type = "neutral"

            movers.append(
                SearchQueryMover(
                    query=query,
                    current_position=round(curr_pos, 1),
                    prior_position=round(prior_pos, 1),
                    position_change=pos_diff,
                    current_impressions=curr_imp,
                    prior_impressions=prior_imp,
                    impressions_change=imp_diff,
                    current_clicks=curr_clk,
                    prior_clicks=prior_clk,
                    clicks_change=clk_diff,
                    mover_type=mover_type,
                )
            )
        else:
            if curr_imp >= 15:
                movers.append(
                    SearchQueryMover(
                        query=query,
                        current_position=round(curr_pos, 1),
                        prior_position=None,
                        position_change=None,
                        current_impressions=curr_imp,
                        prior_impressions=0,
                        impressions_change=curr_imp,
                        current_clicks=curr_clk,
                        prior_clicks=0,
                        clicks_change=curr_clk,
                        mover_type="new_ranking",
                    )
                )

    # Sort prioritizing ranking gains and striking distance
    def sort_key(m: SearchQueryMover) -> float:
        score = 0.0
        if m.mover_type == "ranking_gain":
            score += 100.0 + abs(m.position_change or 0) * 10
        elif m.mover_type == "striking_distance":
            score += 50.0 + (m.current_impressions * 0.1)
        elif m.mover_type == "impression_spike":
            score += 40.0 + (m.impressions_change * 0.05)
        elif m.mover_type == "ranking_drop":
            score += 30.0 + abs(m.position_change or 0) * 5
        return score

    movers.sort(key=sort_key, reverse=True)
    return movers[:top_n]


def normalize_conversion_events(
    current_events: Dict[str, int],
    prior_events: Dict[str, int],
) -> List[ConversionEventSummary]:
    """Create normalized event conversion rows with deterministic deltas."""
    event_display_names = {
        "generate_lead": "Lead Submissions",
        "contact_form_submit": "Contact Forms",
        "appointment_request": "Appointment Inquiries",
        "phone_click": "Direct Phone Clicks",
        "email_click": "Email Inquiries",
        "schedule_appointment": "Appointments Booked",
        "quote_request": "Quote Inquiries",
        "click_to_call": "Phone Clicks",
    }
    all_keys = set(current_events.keys()).union(set(prior_events.keys()))
    summaries = []

    for event_name in all_keys:
        curr_val = int(current_events.get(event_name, 0))
        prior_val = int(prior_events.get(event_name, 0))
        chg = curr_val - prior_val
        pct = calculate_percentage_change(float(curr_val), float(prior_val))
        direction = determine_direction(float(curr_val), float(prior_val))
        disp = event_display_names.get(event_name, event_name.replace("_", " ").title())

        summaries.append(
            ConversionEventSummary(
                event_name=event_name,
                display_name=disp,
                current_count=curr_val,
                prior_count=prior_val,
                count_change=chg,
                percentage_change=pct,
                direction=direction,
            )
        )
    summaries.sort(key=lambda x: x.current_count, reverse=True)
    return summaries


def aggregate_growth_metrics(
    client: ClientConfig,
    start_date: str,
    end_date: str,
    prior_start_date: str,
    prior_end_date: str,
    ga4_data: Dict[str, Any],
    gsc_queries: List[Dict[str, Any]],
    gbp_data: Dict[str, Any],
    report_type: ReportType = ReportType.PERFORMANCE_28D,
    period_days: int = 28,
    prior_gsc_queries: Optional[List[Dict[str, Any]]] = None,
) -> GrowthAnalysisInput:
    """Process and normalize all multi-source raw inputs into a validated GrowthAnalysisInput contract."""
    summary = ga4_data.get("summary", {})
    prior_summary = ga4_data.get("prior_summary", {})

    sessions_curr = float(summary.get("sessions", 0))
    sessions_prior = float(prior_summary.get("sessions", 0))
    conversions_curr = float(summary.get("conversions", 0))
    conversions_prior = float(prior_summary.get("conversions", 0))

    # Calculate deterministic conversion rates (key conversions / sessions)
    cr_curr = round((conversions_curr / sessions_curr) * 100.0, 2) if sessions_curr > 0 else 0.0
    cr_prior = round((conversions_prior / sessions_prior) * 100.0, 2) if sessions_prior > 0 else 0.0
    cr_pt_change = round(cr_curr - cr_prior, 2)
    cr_pct_change = calculate_percentage_change(cr_curr, cr_prior)
    cr_dir = determine_direction(cr_curr, cr_prior, threshold=0.01)

    conversion_rate_delta = MetricDelta(
        metric_name="conversion_rate",
        display_name="Conversion Rate",
        current_value=cr_curr,
        prior_value=cr_prior,
        absolute_change=cr_pt_change,
        percentage_change=cr_pct_change,
        percentage_points_change=cr_pt_change,
        is_percentage_rate=True,
        direction=cr_dir,
        unit="percentage",
    )

    # Core metric deltas
    core_metrics = [
        MetricDelta(
            metric_name="sessions",
            display_name="Total Sessions",
            current_value=sessions_curr,
            prior_value=sessions_prior,
            absolute_change=round(sessions_curr - sessions_prior, 1),
            percentage_change=calculate_percentage_change(sessions_curr, sessions_prior),
            direction=determine_direction(sessions_curr, sessions_prior),
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
        conversion_rate_delta,
        MetricDelta(
            metric_name="conversions",
            display_name="Key Conversions",
            current_value=conversions_curr,
            prior_value=conversions_prior,
            absolute_change=round(conversions_curr - conversions_prior, 1),
            percentage_change=calculate_percentage_change(conversions_curr, conversions_prior),
            direction=determine_direction(conversions_curr, conversions_prior),
            unit="count",
        ),
        MetricDelta(
            metric_name="engagement_rate",
            display_name="Engagement Rate",
            current_value=round(float(summary.get("engagementRate", 0.0)) * 100.0, 1),
            prior_value=round(float(prior_summary.get("engagementRate", 0.0)) * 100.0, 1),
            absolute_change=round((float(summary.get("engagementRate", 0.0)) - float(prior_summary.get("engagementRate", 0.0))) * 100.0, 1),
            percentage_change=calculate_percentage_change(float(summary.get("engagementRate", 0.0)), float(prior_summary.get("engagementRate", 0.0))),
            percentage_points_change=round((float(summary.get("engagementRate", 0.0)) - float(prior_summary.get("engagementRate", 0.0))) * 100.0, 1),
            is_percentage_rate=True,
            direction=determine_direction(float(summary.get("engagementRate", 0.0)), float(prior_summary.get("engagementRate", 0.0))),
            unit="percentage",
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

    page_gainers = sorted([p for p in pages if p.session_change > 0], key=lambda x: x.session_change, reverse=True)[:5]
    page_decliners = sorted([p for p in pages if p.session_change < 0], key=lambda x: x.session_change)[:5]

    # Striking distance keywords & Search movers from GSC
    striking_keywords = filter_striking_distance_keywords(gsc_queries)
    search_movers = calculate_search_movers(gsc_queries, prior_gsc_queries)

    # Normalized conversion events
    events_summary = normalize_conversion_events(
        ga4_data.get("events", {}),
        ga4_data.get("prior_events", {}),
    )

    # Local SEO / GBP metrics with deterministic deltas
    calls_curr = int(gbp_data.get("phone_calls", 0))
    calls_prior = int(gbp_data.get("prior_phone_calls", 0))
    calls_diff = calls_curr - calls_prior
    calls_pct = calculate_percentage_change(float(calls_curr), float(calls_prior))
    calls_dir = determine_direction(float(calls_curr), float(calls_prior))

    dir_curr = int(gbp_data.get("direction_requests", 0))
    dir_prior = int(gbp_data.get("prior_direction_requests", 0))
    dir_diff = dir_curr - dir_prior
    dir_pct = calculate_percentage_change(float(dir_curr), float(dir_prior))
    dir_dir = determine_direction(float(dir_curr), float(dir_prior))

    web_curr = int(gbp_data.get("website_clicks", 0))
    web_prior = int(gbp_data.get("prior_website_clicks", 0))
    web_diff = web_curr - web_prior
    web_pct = calculate_percentage_change(float(web_curr), float(web_prior))
    web_dir = determine_direction(float(web_curr), float(web_prior))

    local_seo = LocalInteractionData(
        phone_calls=calls_curr,
        prior_phone_calls=calls_prior,
        phone_calls_change=calls_diff,
        phone_calls_percentage_change=calls_pct,
        phone_calls_direction=calls_dir,
        direction_requests=dir_curr,
        prior_direction_requests=dir_prior,
        direction_requests_change=dir_diff,
        direction_requests_percentage_change=dir_pct,
        direction_requests_direction=dir_dir,
        website_clicks=web_curr,
        prior_website_clicks=web_prior,
        website_clicks_change=web_diff,
        website_clicks_percentage_change=web_pct,
        website_clicks_direction=web_dir,
        average_rating=gbp_data.get("average_rating"),
        total_reviews_count=gbp_data.get("total_reviews_count"),
        recent_review_snippets=gbp_data.get("recent_review_snippets", []),
    )

    return GrowthAnalysisInput(
        client_id=client.client_id,
        company_name=client.company_name,
        domain=client.domain,
        industry=client.industry,
        report_type=report_type,
        period_days=period_days,
        period_start=start_date,
        period_end=end_date,
        comparison_start=prior_start_date,
        comparison_end=prior_end_date,
        goals=list(client.goals),
        core_metrics=core_metrics,
        conversion_rate=conversion_rate_delta,
        conversion_events=events_summary,
        top_channels=channels[:8],
        top_pages=pages[:10],
        page_gainers=page_gainers,
        page_decliners=page_decliners,
        striking_distance_keywords=striking_keywords,
        search_movers=search_movers,
        local_seo=local_seo,
        raw_summary_stats={
            "events": ga4_data.get("events", {}),
            "prior_events": ga4_data.get("prior_events", {}),
        },
    )
