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
    WebsiteInquiryMetrics,
    SearchQueryMover,
    SourceAvailability,
    StrikingDistanceKeyword,
)
from app.config import ClientConfig
from app.sources.gbp import GBP_PERFORMANCE_METRIC_LABELS


# GA4 event classification is intentionally narrow. Automatic events are
# engagement context and never enter the primary-lead or customer-action
# tables. Unknown events are retained only in source diagnostics/raw connector
# output, not promoted into client-facing conversion categories.
PRIMARY_LEAD_EVENTS = frozenset({"generate_lead"})
CUSTOMER_ACTION_EVENTS = frozenset({
    "form_submit",
    "appointment_request",
    "phone_click",
    "email_click",
    "cta_click",
    "file_download",
})
FUNNEL_ACTIVITY_EVENTS = frozenset({
    "form_start",
    "form_step",
    "form_step_1",
    "form_step_2",
    "form_step_3",
    "form_step1",
    "form_step2",
    "form_step3",
    "step_1",
    "step_2",
    "step_3",
})
ENGAGEMENT_EVENTS = frozenset({"page_view", "scroll", "user_engagement"})

EVENT_DISPLAY_NAMES = {
    "generate_lead": "Primary Leads",
    "form_submit": "Form Submissions",
    "appointment_request": "Appointment Requests",
    "phone_click": "Phone Clicks",
    "email_click": "Email Clicks",
    "cta_click": "CTA Clicks",
    "file_download": "File Downloads",
    "form_start": "Form Starts",
    "form_step": "Form Steps",
    "form_step_1": "Form Step 1",
    "form_step_2": "Form Step 2",
    "form_step_3": "Form Step 3",
    "form_step1": "Form Step 1",
    "form_step2": "Form Step 2",
    "form_step3": "Form Step 3",
    "step_1": "Form Step 1",
    "step_2": "Form Step 2",
    "step_3": "Form Step 3",
}


def _event_status(value: Any, *, has_payload: bool = False) -> SourceAvailability:
    if isinstance(value, SourceAvailability):
        return value
    if value is not None:
        try:
            return SourceAvailability(str(value))
        except ValueError:
            pass
    return SourceAvailability.AVAILABLE if has_payload else SourceAvailability.NOT_CONFIGURED


def _optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    number = _optional_number(value)
    return int(number) if number is not None else None


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
    """Normalize only the primary GA4 key event for compatibility callers."""
    return normalize_event_group(
        current_events,
        prior_events,
        PRIMARY_LEAD_EVENTS,
        comparison_available=comparison_available,
        ensure_event_names=PRIMARY_LEAD_EVENTS,
    )


def normalize_event_group(
    current_events: Dict[str, int],
    prior_events: Optional[Dict[str, int]],
    allowed_events: frozenset[str] | set[str],
    *,
    comparison_available: bool = True,
    current_status: SourceAvailability | str = SourceAvailability.AVAILABLE,
    prior_status: SourceAvailability | str | None = None,
    ensure_event_names: frozenset[str] | set[str] | None = None,
) -> List[ConversionEventSummary]:
    """Create one explicitly classified GA4 event group.

    A missing event row is rendered as zero only when the event query itself
    is available. If the query is unavailable or errored, no synthetic event
    count is emitted. Prior-only names remain visible for a valid comparison.
    """
    current_events = current_events or {}
    prior_events = prior_events or {}
    current_state = _event_status(current_status, has_payload=bool(current_events))
    prior_state = _event_status(prior_status, has_payload=bool(prior_events)) if comparison_available else SourceAvailability.NOT_CONFIGURED
    current_available = current_state in {SourceAvailability.AVAILABLE, SourceAvailability.EMPTY}
    prior_available = prior_state in {SourceAvailability.AVAILABLE, SourceAvailability.EMPTY}
    all_keys = {key for key in current_events if key in allowed_events}
    if comparison_available:
        all_keys.update(key for key in prior_events if key in allowed_events)
    if current_available:
        all_keys.update(ensure_event_names or set())
    summaries = []

    for event_name in sorted(all_keys):
        curr_raw = current_events.get(event_name) if current_available else None
        curr_val = _optional_int(curr_raw) if curr_raw is not None else (0 if current_available else None)
        prior_raw = prior_events.get(event_name) if comparison_available and prior_available else None
        prior_val = _optional_int(prior_raw) if prior_raw is not None else (0 if comparison_available and prior_available else None)
        chg = curr_val - prior_val if curr_val is not None and prior_val is not None else None
        pct = calculate_percentage_change(float(curr_val), float(prior_val)) if curr_val is not None and prior_val is not None else None
        direction = determine_direction(float(curr_val), float(prior_val)) if curr_val is not None and prior_val is not None else "unavailable"
        disp = EVENT_DISPLAY_NAMES.get(event_name, event_name.replace("_", " ").title())

        summaries.append(
            ConversionEventSummary(
                event_name=event_name,
                display_name=disp,
                current_count=curr_val,
                prior_count=prior_val,
                count_change=chg,
                percentage_change=pct,
                direction=direction,
                status=(
                    SourceAvailability.PARTIAL
                    if (current_state == SourceAvailability.AVAILABLE and comparison_available and prior_state != SourceAvailability.AVAILABLE)
                    else current_state
                ),
            )
        )
    summaries.sort(key=lambda x: x.current_count if x.current_count is not None else -1, reverse=True)
    return summaries


def classify_ga4_events(
    current_events: Dict[str, int],
    prior_events: Optional[Dict[str, int]] = None,
    *,
    comparison_available: bool = True,
    current_status: SourceAvailability | str | None = None,
    prior_status: SourceAvailability | str | None = None,
) -> dict[str, Any]:
    """Return separated GA4 event groups and engagement counts."""
    current_events = current_events or {}
    prior_events = prior_events or {}
    inferred_current = _event_status(current_status, has_payload=bool(current_events))
    inferred_prior = _event_status(prior_status, has_payload=bool(prior_events)) if comparison_available else SourceAvailability.NOT_CONFIGURED
    return {
        "primary_leads": normalize_event_group(
            current_events, prior_events, PRIMARY_LEAD_EVENTS,
            comparison_available=comparison_available,
            current_status=inferred_current,
            prior_status=inferred_prior,
            ensure_event_names=PRIMARY_LEAD_EVENTS,
        ),
        "customer_actions": normalize_event_group(
            current_events, prior_events, CUSTOMER_ACTION_EVENTS,
            comparison_available=comparison_available,
            current_status=inferred_current,
            prior_status=inferred_prior,
        ),
        "funnel_activity": normalize_event_group(
            current_events, prior_events, FUNNEL_ACTIVITY_EVENTS,
            comparison_available=comparison_available,
            current_status=inferred_current,
            prior_status=inferred_prior,
        ),
        "engagement_events": {
            "current": {name: _optional_int(current_events.get(name)) for name in sorted(ENGAGEMENT_EVENTS) if name in current_events},
            "prior": {name: _optional_int(prior_events.get(name)) for name in sorted(ENGAGEMENT_EVENTS) if name in prior_events} if comparison_available else {},
            "current_status": inferred_current.value,
            "prior_status": inferred_prior.value if comparison_available else SourceAvailability.NOT_CONFIGURED.value,
        },
        "automatic_events_excluded": sorted(
            key for key in set(current_events) | (set(prior_events) if comparison_available else set())
            if key not in PRIMARY_LEAD_EVENTS | CUSTOMER_ACTION_EVENTS | FUNNEL_ACTIVITY_EVENTS
        ),
    }


def derive_form_progression_and_abandonment(
    current_events: Dict[str, Any],
    prior_events: Optional[Dict[str, Any]] = None,
    *,
    comparison_available: bool = True,
) -> Optional[dict[str, Any]]:
    """Calculate funnel progression and dropoff rates across form steps.

    Funnel steps:
      Form starts -> Step 1 -> Step 2 -> Step 3 -> Submit (form_submit / generate_lead)
    Computes:
      - Dropoff counts & rates between steps
      - Identifies dropoff stage from final recorded step
      - Overall start-to-completion rate & abandonment rate
    """
    current_events = current_events or {}
    prior_events = (prior_events or {}) if comparison_available else {}

    def _get_val(events: Mapping[str, Any], names: tuple[str, ...]) -> int | None:
        for name in names:
            if name in events and events[name] is not None:
                try:
                    return int(events[name])
                except (ValueError, TypeError):
                    pass
        return None

    step_definitions = [
        ("form_start", "Form Starts", ("form_start", "form_starts")),
        ("form_step_1", "Step 1", ("form_step_1", "form_step1", "step_1", "step1")),
        ("form_step_2", "Step 2", ("form_step_2", "form_step2", "step_2", "step2")),
        ("form_step_3", "Step 3", ("form_step_3", "form_step3", "step_3", "step3")),
        ("form_submit", "Submit", ("form_submit", "form_submission", "generate_lead")),
    ]

    raw_steps = []
    has_any = False
    for step_id, disp_name, aliases in step_definitions:
        curr = _get_val(current_events, aliases)
        prior = _get_val(prior_events, aliases) if comparison_available else None
        if curr is not None or prior is not None:
            has_any = True
        raw_steps.append({
            "step_id": step_id,
            "display_name": disp_name,
            "current_count": curr,
            "prior_count": prior,
        })

    if not has_any:
        return None

    steps_data = []
    dropoff_by_final_step: dict[str, int] = {}
    highest_dropoff_count = -1
    highest_dropoff_stage: str | None = None

    for i in range(len(raw_steps)):
        step = raw_steps[i]
        curr = step["current_count"]
        prior = step["prior_count"]

        dropoff_count = None
        dropoff_rate = None
        progression_rate = None

        if i < len(raw_steps) - 1:
            next_step = raw_steps[i + 1]
            next_curr = next_step["current_count"]
            if curr is not None and next_curr is not None:
                dropoff_count = max(0, curr - next_curr)
                dropoff_by_final_step[step["step_id"]] = dropoff_count
                if curr > 0:
                    dropoff_rate = round((dropoff_count / curr) * 100.0, 2)
                    progression_rate = round((next_curr / curr) * 100.0, 2)
                else:
                    dropoff_rate = 0.0
                    progression_rate = 0.0

                stage_label = f"{step['display_name']} -> {next_step['display_name']}"
                if dropoff_count > highest_dropoff_count:
                    highest_dropoff_count = dropoff_count
                    highest_dropoff_stage = stage_label

        step_dict = {
            "step_id": step["step_id"],
            "display_name": step["display_name"],
            "current_count": curr,
            "prior_count": prior,
            "dropoff_count": dropoff_count,
            "dropoff_rate": dropoff_rate,
            "progression_rate": progression_rate,
        }
        steps_data.append(step_dict)

    starts = raw_steps[0]["current_count"]
    submits = raw_steps[-1]["current_count"]
    completion_rate = None
    abandonment_rate = None
    if starts is not None and starts > 0 and submits is not None:
        completion_rate = round((submits / starts) * 100.0, 2)
        abandonment_rate = round((max(0, starts - submits) / starts) * 100.0, 2)

    return {
        "steps": steps_data,
        "total_starts": starts,
        "total_completions": submits,
        "overall_completion_rate": completion_rate,
        "overall_abandonment_rate": abandonment_rate,
        "highest_dropoff_stage": highest_dropoff_stage,
        "dropoff_by_final_step": dropoff_by_final_step,
    }


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
    website_inquiry_metrics: Optional[WebsiteInquiryMetrics | dict[str, Any]] = None,
) -> GrowthAnalysisInput:
    """Process and normalize all multi-source raw inputs into a validated GrowthAnalysisInput contract."""
    summary = ga4_data.get("summary", {})
    comparison_available = report_mode == ReportMode.COMPARISON and not comparison_suppressed
    prior_summary = ga4_data.get("prior_summary", {}) if comparison_available else {}

    query_statuses = ga4_data.get("query_statuses", {}) or {}
    summary_query = query_statuses.get("summary", {}) if isinstance(query_statuses, dict) else {}
    event_query = query_statuses.get("events", {}) if isinstance(query_statuses, dict) else {}
    summary_status = _event_status(
        summary_query.get("current") if isinstance(summary_query, dict) else None,
        has_payload=bool(summary),
    )
    prior_summary_status = _event_status(
        summary_query.get("prior") if isinstance(summary_query, dict) else None,
        has_payload=bool(prior_summary),
    ) if comparison_available else SourceAvailability.NOT_CONFIGURED
    event_status = _event_status(
        event_query.get("current") if isinstance(event_query, dict) else ga4_data.get("events_status"),
        has_payload="events" in ga4_data,
    )
    prior_event_status = _event_status(
        event_query.get("prior") if isinstance(event_query, dict) else ga4_data.get("prior_events_status"),
        has_payload=bool(ga4_data.get("prior_events")),
    ) if comparison_available else SourceAvailability.NOT_CONFIGURED

    sessions_curr = _optional_number(summary.get("sessions"))
    sessions_prior = _optional_number(prior_summary.get("sessions")) if comparison_available else None
    event_groups = classify_ga4_events(
        ga4_data.get("events", {}),
        ga4_data.get("prior_events", {}) if comparison_available else {},
        comparison_available=comparison_available,
        current_status=event_status,
        prior_status=prior_event_status,
    )
    primary_leads = event_groups["primary_leads"]
    customer_actions = event_groups["customer_actions"]
    funnel_activity = event_groups["funnel_activity"]
    abandonment_summary = derive_form_progression_and_abandonment(
        ga4_data.get("events", {}),
        ga4_data.get("prior_events", {}) if comparison_available else {},
        comparison_available=comparison_available,
    )
    lead_current = next((item.current_count for item in primary_leads if item.event_name == "generate_lead"), None)
    lead_prior = next((item.prior_count for item in primary_leads if item.event_name == "generate_lead"), None)
    conversions_curr = float(lead_current) if lead_current is not None else None
    conversions_prior = float(lead_prior) if lead_prior is not None else None

    # Calculate deterministic conversion rates (key conversions / sessions)
    cr_curr = round((conversions_curr / sessions_curr) * 100.0, 2) if conversions_curr is not None and sessions_curr is not None and sessions_curr > 0 else None
    cr_prior = (
        round((conversions_prior / sessions_prior) * 100.0, 2)
        if sessions_prior is not None and conversions_prior is not None and sessions_prior > 0
        else None
    )
    cr_pt_change = round(cr_curr - cr_prior, 2) if cr_curr is not None and cr_prior is not None else None
    cr_pct_change = calculate_percentage_change(cr_curr, cr_prior) if cr_curr is not None and cr_prior is not None else None
    cr_dir = determine_direction(cr_curr, cr_prior, threshold=0.01) if cr_curr is not None and cr_prior is not None else "unavailable"

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
        status=(
            SourceAvailability.PARTIAL
            if cr_curr is not None and comparison_available and cr_prior is None
            else (event_status if cr_curr is None else SourceAvailability.AVAILABLE)
        ),
    )

    def metric_delta(
        metric_name: str,
        display_name: str,
        current_value: Optional[float],
        prior_value: Optional[float],
        unit: str = "count",
        is_percentage_rate: bool = False,
        status: SourceAvailability = SourceAvailability.AVAILABLE,
    ) -> MetricDelta:
        absolute_change = round(current_value - prior_value, 1) if current_value is not None and prior_value is not None else None
        return MetricDelta(
            metric_name=metric_name,
            display_name=display_name,
            current_value=current_value,
            prior_value=prior_value,
            absolute_change=absolute_change,
            percentage_change=(
                calculate_percentage_change(current_value, prior_value)
                if current_value is not None and prior_value is not None
                else None
            ),
            percentage_points_change=(
                absolute_change if is_percentage_rate and absolute_change is not None else None
            ),
            is_percentage_rate=is_percentage_rate,
            direction=determine_direction(current_value, prior_value) if current_value is not None and prior_value is not None else "unavailable",
            unit=unit,
            status=status if current_value is not None else SourceAvailability.PARTIAL,
        )

    # Core metric deltas remain current-only in a baseline. A missing prior
    # value is represented as None rather than a synthetic zero.
    core_metrics = [
        metric_delta("sessions", "Total Sessions", sessions_curr, sessions_prior, status=summary_status),
        metric_delta(
            "active_users",
            "Active Users",
            _optional_number(summary.get("activeUsers")),
            _optional_number(prior_summary.get("activeUsers")) if comparison_available else None,
            status=summary_status,
        ),
        conversion_rate_delta,
        metric_delta("primary_leads", "Primary Leads", conversions_curr, conversions_prior, status=event_status),
        metric_delta(
            "engagement_rate",
            "Engagement Rate",
            round(_optional_number(summary.get("engagementRate")) * 100.0, 1) if _optional_number(summary.get("engagementRate")) is not None else None,
            (
                round(_optional_number(prior_summary.get("engagementRate")) * 100.0, 1)
                if prior_summary.get("engagementRate") is not None
                else None
            ),
            unit="percentage",
            is_percentage_rate=True,
            status=summary_status,
        ),
    ]

    # Channels
    channels = []
    for ch in ga4_data.get("channels", []):
        sess = _optional_int(ch.get("sessions"))
        prior_raw = ch.get("priorSessions") if comparison_available else None
        prior_sess = _optional_int(prior_raw)
        active_users = _optional_int(ch.get("activeUsers"))
        channel_primary_leads = _optional_int(ch.get("primaryLeads"))
        channels.append(
            ChannelPerformance(
                channel=ch.get("channel", "Direct"),
                sessions=sess,
                active_users=active_users,
                prior_sessions=prior_sess,
                session_change=sess - prior_sess if sess is not None and prior_sess is not None else None,
                percentage_change=(
                    calculate_percentage_change(float(sess), float(prior_sess))
                    if sess is not None and prior_sess is not None
                    else None
                ),
                conversions=channel_primary_leads,
            )
        )
    channels.sort(key=lambda x: x.sessions if x.sessions is not None else -1, reverse=True)

    # Pages & High-Intent identification
    pages = []
    high_intent_keywords = {"contact", "book", "schedule", "quote", "pricing", "services", "treatment", "signup", "appointment"}
    for pg in ga4_data.get("pages", []):
        path = pg.get("pagePath", "/")
        sess = _optional_int(pg.get("sessions"))
        prior_raw = pg.get("priorSessions") if comparison_available else None
        prior_sess = _optional_int(prior_raw)
        is_intent = any(kw in path.lower() for kw in high_intent_keywords)
        pages.append(
            PagePerformance(
                page_path=path,
                sessions=sess,
                active_users=_optional_int(pg.get("activeUsers")),
                prior_sessions=prior_sess,
                session_change=sess - prior_sess if sess is not None and prior_sess is not None else None,
                is_high_intent=is_intent,
            )
        )
    pages.sort(key=lambda x: x.sessions if x.sessions is not None else -1, reverse=True)

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

    # The compatibility ``conversion_events`` field mirrors primary leads only;
    # customer actions and form funnel activity remain separate categories.
    events_summary = primary_leads

    engagement_summary = dict(event_groups["engagement_events"])
    engagement_summary.update({
        "sessions": sessions_curr,
        "prior_sessions": sessions_prior,
        "active_users": _optional_number(summary.get("activeUsers")),
        "prior_active_users": _optional_number(prior_summary.get("activeUsers")) if comparison_available else None,
        "page_views": _optional_int((ga4_data.get("events", {}) or {}).get("page_view")) if event_status in {SourceAvailability.AVAILABLE, SourceAvailability.EMPTY} else None,
        "scrolls": _optional_int((ga4_data.get("events", {}) or {}).get("scroll")) if event_status in {SourceAvailability.AVAILABLE, SourceAvailability.EMPTY} else None,
        "user_engagement": _optional_int((ga4_data.get("events", {}) or {}).get("user_engagement")) if event_status in {SourceAvailability.AVAILABLE, SourceAvailability.EMPTY} else None,
        "status": event_status.value,
    })

    if isinstance(website_inquiry_metrics, dict):
        website_inquiry_metrics = WebsiteInquiryMetrics(**website_inquiry_metrics)

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

    effective_source_statuses = dict(source_statuses or {})
    effective_source_statuses.setdefault("ga4", {
        "status": ga4_data.get("status") or summary_status.value,
        "current_status": ga4_data.get("current_status") or summary_status.value,
        "prior_status": ga4_data.get("prior_status") or (prior_summary_status.value if comparison_available else SourceAvailability.NOT_CONFIGURED.value),
    })
    effective_source_statuses.setdefault("gsc", {
        "status": SourceAvailability.AVAILABLE.value if gsc_queries else SourceAvailability.NOT_CONFIGURED.value,
    })
    effective_source_statuses.setdefault("gbp", {
        "status": gbp_data.get("status") or SourceAvailability.NOT_CONFIGURED.value,
    })
    effective_source_statuses["ga4_events"] = {
        "current": event_status.value,
        "prior": prior_event_status.value if comparison_available else SourceAvailability.NOT_CONFIGURED.value,
        "automatic_events_excluded": event_groups["automatic_events_excluded"],
    }
    if website_inquiry_metrics is not None:
        effective_source_statuses["website_inquiries"] = website_inquiry_metrics.status.value

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
        primary_leads=primary_leads,
        customer_actions=customer_actions,
        funnel_activity=funnel_activity,
        abandonment_summary=abandonment_summary,
        engagement_metrics=engagement_summary,
        event_statuses={
            "current": event_status.value,
            "prior": prior_event_status.value if comparison_available else SourceAvailability.NOT_CONFIGURED.value,
            "automatic_events_excluded": event_groups["automatic_events_excluded"],
        },
        website_inquiry_metrics=website_inquiry_metrics,
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
            "abandonment_summary": abandonment_summary,
            "report_mode": report_mode.value,
            "measurement_start_date": measurement_start_date,
            "observed_days": (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1,
            "comparison_suppressed": not comparison_available,
            "comparison_suppression_reason": comparison_suppression_reason,
            "source_statuses": effective_source_statuses,
            "source_diagnostics": source_diagnostics or {},
            "search_comparison_status": search_comparison_status.value,
            "search_comparison_truncated": prior_gsc_truncated,
            "search_comparison_diagnostics": search_comparison_diagnostics,
        },
        source_statuses=effective_source_statuses,
        source_diagnostics=source_diagnostics or {},
        search_comparison_status=search_comparison_status,
        search_comparison_truncated=prior_gsc_truncated,
        search_comparison_diagnostics=search_comparison_diagnostics,
    )
