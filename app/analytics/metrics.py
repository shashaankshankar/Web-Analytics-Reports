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
    ReportMode,
    ReportType,
    SearchQueryMover,
    SourceAvailability,
    StrikingDistanceKeyword,
)
from app.config import ClientConfig
from app.sources.gbp import GBP_PERFORMANCE_METRIC_LABELS


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
    prior_status: Optional[str | SourceAvailability] = None,
    prior_truncated: bool = False,
) -> List[SearchQueryMover]:
    """Calculate movements only when a complete, usable prior snapshot exists.

    A current-only, empty, unavailable, or truncated prior response cannot
    establish a movement baseline. Those cases return no movers; current-only
    search opportunities remain available through ``filter_striking_distance_keywords``.
    """
    if prior_truncated:
        return []
    if prior_status is not None:
        normalized_status = (
            prior_status.value if isinstance(prior_status, SourceAvailability) else str(prior_status)
        )
        if normalized_status != SourceAvailability.AVAILABLE.value:
            return []
    elif not prior_queries:
        return []
    if not prior_queries:
        return []

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
            if any(
                field not in prior_item or prior_item.get(field) is None
                for field in ("position", "impressions", "clicks")
            ):
                continue
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
        # Search Console omits low-volume queries. A query absent from the
        # prior snapshot is not evidence of a zero baseline, so it is excluded
        # from movement claims rather than labeled a new ranking.

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
    prior_events: Optional[Dict[str, int]] = None,
    comparison_available: bool = True,
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
    prior_events = prior_events or {}
    # A current event can disappear from a GA4 event-name response when its
    # count is zero. Keep prior-only names in comparison reports so a real
    # drop to zero is visible instead of silently disappearing.
    all_keys = set(current_events.keys())
    if comparison_available:
        all_keys.update(prior_events.keys())
    summaries = []

    for event_name in all_keys:
        curr_val = int(current_events.get(event_name, 0))
        prior_raw = prior_events.get(event_name) if comparison_available else None
        prior_val = int(prior_raw) if prior_raw is not None else None
        chg = curr_val - prior_val if prior_val is not None else None
        pct = calculate_percentage_change(float(curr_val), float(prior_val)) if prior_val is not None else None
        direction = determine_direction(float(curr_val), float(prior_val)) if prior_val is not None else "unavailable"
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
    source_statuses: Optional[Dict[str, Any]] = None,
    source_diagnostics: Optional[Dict[str, str]] = None,
    prior_gsc_status: Optional[str | SourceAvailability] = None,
    prior_gsc_truncated: bool = False,
    report_mode: ReportMode = ReportMode.COMPARISON,
    measurement_start_date: Optional[str] = None,
    requested_period_start: Optional[str] = None,
    requested_period_end: Optional[str] = None,
    requested_comparison_start: Optional[str] = None,
    requested_comparison_end: Optional[str] = None,
    comparison_suppressed: bool = False,
    comparison_suppression_reason: Optional[str] = None,
) -> GrowthAnalysisInput:
    """Process and normalize all multi-source raw inputs into a validated GrowthAnalysisInput contract."""
    summary = ga4_data.get("summary", {})
    comparison_available = report_mode == ReportMode.COMPARISON and not comparison_suppressed
    prior_summary = ga4_data.get("prior_summary", {}) if comparison_available else {}

    sessions_curr = float(summary.get("sessions", 0))
    sessions_prior = float(prior_summary.get("sessions")) if prior_summary.get("sessions") is not None else None
    conversions_curr = float(summary.get("conversions", 0))
    conversions_prior = float(prior_summary.get("conversions")) if prior_summary.get("conversions") is not None else None

    # Calculate deterministic conversion rates (key conversions / sessions)
    cr_curr = round((conversions_curr / sessions_curr) * 100.0, 2) if sessions_curr > 0 else 0.0
    cr_prior = (
        round((conversions_prior / sessions_prior) * 100.0, 2)
        if sessions_prior is not None and conversions_prior is not None and sessions_prior > 0
        else None
    )
    cr_pt_change = round(cr_curr - cr_prior, 2) if cr_prior is not None else None
    cr_pct_change = calculate_percentage_change(cr_curr, cr_prior) if cr_prior is not None else None
    cr_dir = determine_direction(cr_curr, cr_prior, threshold=0.01) if cr_prior is not None else "unavailable"

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

    def metric_delta(
        metric_name: str,
        display_name: str,
        current_value: float,
        prior_value: Optional[float],
        unit: str = "count",
        is_percentage_rate: bool = False,
    ) -> MetricDelta:
        absolute_change = round(current_value - prior_value, 1) if prior_value is not None else None
        return MetricDelta(
            metric_name=metric_name,
            display_name=display_name,
            current_value=current_value,
            prior_value=prior_value,
            absolute_change=absolute_change,
            percentage_change=(
                calculate_percentage_change(current_value, prior_value)
                if prior_value is not None
                else None
            ),
            percentage_points_change=(
                absolute_change if is_percentage_rate and absolute_change is not None else None
            ),
            is_percentage_rate=is_percentage_rate,
            direction=determine_direction(current_value, prior_value) if prior_value is not None else "unavailable",
            unit=unit,
        )

    # Core metric deltas remain current-only in a baseline. A missing prior
    # value is represented as None rather than a synthetic zero.
    core_metrics = [
        metric_delta("sessions", "Total Sessions", sessions_curr, sessions_prior),
        metric_delta(
            "active_users",
            "Active Users",
            float(summary.get("activeUsers", 0)),
            float(prior_summary.get("activeUsers")) if prior_summary.get("activeUsers") is not None else None,
        ),
        conversion_rate_delta,
        metric_delta("conversions", "Key Conversions", conversions_curr, conversions_prior),
        metric_delta(
            "engagement_rate",
            "Engagement Rate",
            round(float(summary.get("engagementRate", 0.0)) * 100.0, 1),
            (
                round(float(prior_summary.get("engagementRate")) * 100.0, 1)
                if prior_summary.get("engagementRate") is not None
                else None
            ),
            unit="percentage",
            is_percentage_rate=True,
        ),
    ]

    # Channels
    channels = []
    for ch in ga4_data.get("channels", []):
        sess = int(ch.get("sessions", 0))
        prior_raw = ch.get("priorSessions") if comparison_available else None
        prior_sess = int(prior_raw) if prior_raw is not None else None
        channels.append(
            ChannelPerformance(
                channel=ch.get("channel", "Direct"),
                sessions=sess,
                active_users=int(ch.get("activeUsers", 0)),
                prior_sessions=prior_sess,
                session_change=sess - prior_sess if prior_sess is not None else None,
                percentage_change=(
                    calculate_percentage_change(float(sess), float(prior_sess))
                    if prior_sess is not None
                    else None
                ),
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
        prior_raw = pg.get("priorSessions") if comparison_available else None
        prior_sess = int(prior_raw) if prior_raw is not None else None
        is_intent = any(kw in path.lower() for kw in high_intent_keywords)
        pages.append(
            PagePerformance(
                page_path=path,
                sessions=sess,
                active_users=int(pg.get("activeUsers", 0)),
                prior_sessions=prior_sess,
                session_change=sess - prior_sess if prior_sess is not None else None,
                is_high_intent=is_intent,
            )
        )
    pages.sort(key=lambda x: x.sessions, reverse=True)

    page_gainers = (
        sorted([p for p in pages if p.session_change is not None and p.session_change > 0], key=lambda x: x.session_change, reverse=True)[:5]
        if comparison_available
        else []
    )
    page_decliners = (
        sorted([p for p in pages if p.session_change is not None and p.session_change < 0], key=lambda x: x.session_change)[:5]
        if comparison_available
        else []
    )

    # Striking distance keywords use the current snapshot only. Movement
    # claims require an explicitly available, non-truncated prior snapshot.
    striking_keywords = filter_striking_distance_keywords(gsc_queries)
    gsc_status_block = (source_statuses or {}).get("gsc", {})
    if prior_gsc_status is None and isinstance(gsc_status_block, dict):
        prior_gsc_status = gsc_status_block.get("prior")
    if prior_gsc_truncated is False and isinstance(gsc_status_block, dict):
        prior_gsc_truncated = bool(gsc_status_block.get("prior_truncated", False))
    if not comparison_available:
        prior_gsc_status = SourceAvailability.UNAVAILABLE
        prior_gsc_truncated = False
    elif prior_gsc_status is None:
        prior_gsc_status = (
            SourceAvailability.AVAILABLE if prior_gsc_queries else SourceAvailability.UNAVAILABLE
        )
    normalized_prior_status = (
        prior_gsc_status.value
        if isinstance(prior_gsc_status, SourceAvailability)
        else str(prior_gsc_status)
    )
    search_comparison_status = (
        SourceAvailability.UNAVAILABLE
        if prior_gsc_truncated
        else SourceAvailability(normalized_prior_status)
        if normalized_prior_status in {item.value for item in SourceAvailability}
        else SourceAvailability.UNAVAILABLE
    )
    search_comparison_diagnostics: list[str] = []
    if not comparison_available:
        search_comparison_diagnostics.append(
            comparison_suppression_reason
            or "Search Console comparison was suppressed because this is an initial measurement baseline."
        )
    elif prior_gsc_truncated:
        search_comparison_diagnostics.append("Prior Search Console data was truncated at the connector row limit; movement claims were suppressed.")
    elif search_comparison_status != SourceAvailability.AVAILABLE:
        search_comparison_diagnostics.append(
            f"Prior Search Console comparison is {search_comparison_status.value}; movement claims were suppressed."
        )
    search_movers = (
        calculate_search_movers(
            gsc_queries,
            prior_gsc_queries,
            prior_status=search_comparison_status,
            prior_truncated=prior_gsc_truncated,
        )
        if comparison_available
        else []
    )

    # Normalized conversion events
    events_summary = normalize_conversion_events(
        ga4_data.get("events", {}),
        ga4_data.get("prior_events", {}),
        comparison_available=comparison_available,
    )

    # Local SEO / GBP metrics with deterministic deltas
    def optional_delta(current_key: str, prior_key: str) -> tuple[Optional[int], Optional[int], Optional[int], Optional[float], str]:
        current_raw = gbp_data.get(current_key)
        prior_raw = gbp_data.get(prior_key)
        if current_raw is None or prior_raw is None:
            return None, None, None, None, "unavailable"
        current = int(current_raw)
        prior = int(prior_raw)
        return (
            current,
            prior,
            current - prior,
            calculate_percentage_change(float(current), float(prior)),
            determine_direction(float(current), float(prior)),
        )

    calls_curr, calls_prior, calls_diff, calls_pct, calls_dir = optional_delta("phone_calls", "prior_phone_calls")
    dir_curr, dir_prior, dir_diff, dir_pct, dir_dir = optional_delta("direction_requests", "prior_direction_requests")
    web_curr, web_prior, web_diff, web_pct, web_dir = optional_delta("website_clicks", "prior_website_clicks")
    answered_curr, answered_prior, answered_diff, answered_pct, answered_dir = optional_delta(
        "answered_calls", "prior_answered_calls"
    )
    missed_curr, missed_prior, missed_diff, missed_pct, missed_dir = optional_delta(
        "missed_calls", "prior_missed_calls"
    )

    performance_metrics = gbp_data.get("performance_metrics", {}) or {}
    prior_performance_metrics = (
        gbp_data.get("prior_performance_metrics", {}) or {}
        if comparison_available
        else {}
    )
    performance_metric_deltas: list[MetricDelta] = []
    for metric_name in sorted(performance_metrics):
        current_payload = performance_metrics.get(metric_name) or {}
        if not isinstance(current_payload, dict) or current_payload.get("total") is None:
            continue
        current_total = float(current_payload["total"])
        prior_payload = prior_performance_metrics.get(metric_name) or {}
        prior_total = (
            float(prior_payload["total"])
            if isinstance(prior_payload, dict) and prior_payload.get("total") is not None
            else None
        )
        performance_metric_deltas.append(
            metric_delta(
                metric_name=f"gbp_{metric_name.lower()}",
                display_name=GBP_PERFORMANCE_METRIC_LABELS.get(
                    metric_name,
                    metric_name.replace("_", " ").title(),
                ),
                current_value=current_total,
                prior_value=prior_total,
            )
        )

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
        profile_status=str(gbp_data.get("profile_status") or SourceAvailability.UNAVAILABLE.value),
        profile=gbp_data.get("profile_summary", {}) or {},
        performance_status=str(gbp_data.get("performance_status") or SourceAvailability.UNAVAILABLE.value),
        performance_metrics=performance_metrics,
        prior_performance_metrics=prior_performance_metrics,
        available_performance_metrics=list(gbp_data.get("available_performance_metrics", []) or []),
        performance_metric_deltas=performance_metric_deltas,
        search_keywords_status=str(
            gbp_data.get("search_keywords_status") or SourceAvailability.UNAVAILABLE.value
        ),
        monthly_search_keywords=list(gbp_data.get("monthly_search_keywords", []) or []),
        prior_monthly_search_keywords=(
            list(gbp_data.get("prior_monthly_search_keywords", []) or [])
            if comparison_available
            else []
        ),
        reviews_status=str(gbp_data.get("reviews_status") or SourceAvailability.UNAVAILABLE.value),
        reviews=list(gbp_data.get("reviews", []) or []),
        review_inventory_complete=bool(gbp_data.get("review_inventory_complete", False)),
        review_response_summary=gbp_data.get("review_response_summary", {}) or {},
        prior_review_response_summary=gbp_data.get("prior_review_response_summary", {}) or {},
        business_calls_status=str(
            gbp_data.get("business_calls_status") or SourceAvailability.UNAVAILABLE.value
        ),
        business_calls=gbp_data.get("business_calls", {}) or {},
        answered_calls=answered_curr,
        prior_answered_calls=answered_prior,
        answered_calls_change=answered_diff,
        answered_calls_percentage_change=answered_pct,
        answered_calls_direction=answered_dir,
        missed_calls=missed_curr,
        prior_missed_calls=missed_prior,
        missed_calls_change=missed_diff,
        missed_calls_percentage_change=missed_pct,
        missed_calls_direction=missed_dir,
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
        report_mode=report_mode,
        period_days=period_days,
        observed_days=(
            (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
        ),
        period_start=start_date,
        period_end=end_date,
        comparison_start=prior_start_date,
        comparison_end=prior_end_date,
        requested_period_start=requested_period_start or start_date,
        requested_period_end=requested_period_end or end_date,
        requested_comparison_start=requested_comparison_start or prior_start_date,
        requested_comparison_end=requested_comparison_end or prior_end_date,
        measurement_start_date=measurement_start_date,
        comparison_suppressed=not comparison_available,
        comparison_suppression_reason=comparison_suppression_reason,
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
            "prior_events": ga4_data.get("prior_events", {}) if comparison_available else {},
            "report_mode": report_mode.value,
            "measurement_start_date": measurement_start_date,
            "observed_days": (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1,
            "comparison_suppressed": not comparison_available,
            "comparison_suppression_reason": comparison_suppression_reason,
            "source_statuses": source_statuses or {},
            "source_diagnostics": source_diagnostics or {},
            "search_comparison_status": search_comparison_status.value,
            "search_comparison_truncated": prior_gsc_truncated,
            "search_comparison_diagnostics": search_comparison_diagnostics,
        },
        source_statuses=source_statuses or {},
        source_diagnostics=source_diagnostics or {},
        search_comparison_status=search_comparison_status,
        search_comparison_truncated=prior_gsc_truncated,
        search_comparison_diagnostics=search_comparison_diagnostics,
    )
