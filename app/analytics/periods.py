from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.analytics.contracts import ReportMode


class ReportWindowError(ValueError):
    """Raised when a requested report window cannot be supported by real data."""


@dataclass(frozen=True)
class ReportWindowPlan:
    """Requested and observed windows selected for one report run."""

    mode: ReportMode
    requested_period_start: str
    requested_period_end: str
    requested_comparison_start: str
    requested_comparison_end: str
    observation_start: str
    observation_end: str
    measurement_start_date: str | None = None
    comparison_suppressed: bool = False
    comparison_suppression_reason: str | None = None

    @property
    def observed_days(self) -> int:
        start = date.fromisoformat(self.observation_start)
        end = date.fromisoformat(self.observation_end)
        return (end - start).days + 1


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _date_order_error(start: date, end: date, label: str) -> None:
    if start > end:
        raise ReportWindowError(f"{label} start date must be on or before its end date.")


def select_report_window(
    requested_period_start: str | date,
    requested_period_end: str | date,
    requested_comparison_start: str | date,
    requested_comparison_end: str | date,
    measurement_start_date: str | date | None = None,
    current_covered: bool | None = None,
    comparison_covered: bool | None = None,
) -> ReportWindowPlan:
    """Select comparison or initial-baseline semantics from dates and coverage.

    ``current_covered`` and ``comparison_covered`` are optional so callers can
    select the date-based plan before querying the source, then re-run the same
    decision with observed coverage to enforce the fail-closed gate.
    """
    current_start = _as_date(requested_period_start)
    current_end = _as_date(requested_period_end)
    comparison_start = _as_date(requested_comparison_start)
    comparison_end = _as_date(requested_comparison_end)
    _date_order_error(current_start, current_end, "Current report period")
    _date_order_error(comparison_start, comparison_end, "Comparison period")

    measurement_date = _as_date(measurement_start_date) if measurement_start_date is not None else None
    if measurement_date is not None and current_end < measurement_date:
        raise ReportWindowError(
            "The requested current period ends before measurement_start_date; no current observation is eligible."
        )

    baseline_reason: str | None = None
    if measurement_date is not None:
        current_is_post_measurement = current_start >= measurement_date
        comparison_is_post_measurement = comparison_start >= measurement_date
        if not (current_is_post_measurement and comparison_is_post_measurement):
            baseline_reason = (
                "The comparison period is before measurement began."
                if comparison_end < measurement_date
                else "The comparison period overlaps measurement start and is not a full comparable observation window."
            )

    is_baseline = baseline_reason is not None
    if is_baseline:
        if current_covered is False:
            raise ReportWindowError("No current source evidence is available for the initial measurement baseline.")
        return ReportWindowPlan(
            mode=ReportMode.INITIAL_BASELINE,
            requested_period_start=current_start.isoformat(),
            requested_period_end=current_end.isoformat(),
            requested_comparison_start=comparison_start.isoformat(),
            requested_comparison_end=comparison_end.isoformat(),
            observation_start=measurement_date.isoformat(),
            observation_end=current_end.isoformat(),
            measurement_start_date=measurement_date.isoformat(),
            comparison_suppressed=True,
            comparison_suppression_reason=baseline_reason,
        )

    if current_covered is False:
        raise ReportWindowError("No current source evidence is available for the requested report period.")
    if comparison_covered is False:
        raise ReportWindowError("The comparison period does not have complete source coverage for a normal report.")

    return ReportWindowPlan(
        mode=ReportMode.COMPARISON,
        requested_period_start=current_start.isoformat(),
        requested_period_end=current_end.isoformat(),
        requested_comparison_start=comparison_start.isoformat(),
        requested_comparison_end=comparison_end.isoformat(),
        observation_start=current_start.isoformat(),
        observation_end=current_end.isoformat(),
        measurement_start_date=measurement_date.isoformat() if measurement_date is not None else None,
    )


def coverage_from_ga4(data: dict, period: str) -> bool:
    """Return whether a GA4 summary for a period is actually available."""
    explicit_status = data.get(f"{period}_status")
    if explicit_status is not None:
        return str(getattr(explicit_status, "value", explicit_status)) == "available"
    summary_key = "summary" if period == "current" else "prior_summary"
    return bool(data.get(summary_key))
