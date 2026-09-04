"""Small deterministic formatters for source-backed GBP report sections."""

from __future__ import annotations

from typing import Any

from app.ai.privacy import scrub_gsc_query
from app.analytics.contracts import LocalInteractionData


def _display_name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("text", "displayName", "localizedDisplayName", "label", "description", "name"):
            if value.get(key):
                return str(value[key])
        return ""
    return str(value)


def _time(value: Any) -> str:
    if isinstance(value, dict):
        hours = value.get("hours")
        minutes = value.get("minutes", 0)
        if hours is not None:
            try:
                return f"{int(hours):02d}:{int(minutes or 0):02d}"
            except (TypeError, ValueError):
                return ""
    return str(value or "")


def _address(value: Any) -> str:
    if not isinstance(value, dict):
        return _display_name(value)
    lines = [str(item) for item in value.get("addressLines", []) if item]
    locality = value.get("locality")
    administrative_area = value.get("administrativeArea")
    postal_code = value.get("postalCode")
    locality_line = ", ".join(str(item) for item in (locality, administrative_area) if item)
    if postal_code:
        locality_line = f"{locality_line} {postal_code}".strip()
    if locality_line:
        lines.append(locality_line)
    return ", ".join(lines)


def _hours(value: Any) -> str:
    if not isinstance(value, dict):
        return _display_name(value)
    periods = value.get("periods") or []
    formatted: list[str] = []
    for period in periods:
        if not isinstance(period, dict):
            continue
        open_day = str(period.get("openDay") or period.get("open_day") or "").title()
        close_day = str(period.get("closeDay") or period.get("close_day") or "").title()
        opening = _time(period.get("openTime", period.get("open_time")))
        closing = _time(period.get("closeTime", period.get("close_time")))
        day = open_day if open_day == close_day or not close_day else f"{open_day} to {close_day}"
        formatted.append(f"{day}: {opening or 'Open'}-{closing or 'Closed'}".strip(" :"))
    return "; ".join(formatted)


def _category(value: Any) -> str:
    if not isinstance(value, dict):
        return _display_name(value)
    return _display_name(value.get("displayName") or value.get("name"))


def _services(value: Any) -> str:
    if not isinstance(value, list):
        return _display_name(value)
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        structured = item.get("structuredServiceItem") or item.get("structured_service_item") or {}
        free_form = item.get("freeFormServiceItem") or item.get("free_form_service_item") or {}
        name = (
            structured.get("description")
            or structured.get("serviceTypeId")
            or free_form.get("label")
            or free_form.get("category")
        )
        if name:
            names.append(str(name))
    return ", ".join(names)


def profile_rows(local: LocalInteractionData) -> list[tuple[str, str]]:
    """Return the useful NAP, hours, category, and service fields verbatim-ish."""
    profile = local.profile or {}
    if local.profile_status != "available" or not profile:
        return []
    categories = [
        item for item in (
            _category(profile.get("primary_category")),
            *[_category(value) for value in profile.get("additional_categories", []) or []],
        ) if item
    ]
    rows = [
        ("Name", _display_name(profile.get("title"))),
        ("Primary phone", _display_name(profile.get("primary_phone"))),
        ("Address", _address(profile.get("address"))),
        ("Website", _display_name(profile.get("website_uri"))),
        ("Hours", _hours(profile.get("regular_hours"))),
        ("Category", ", ".join(categories)),
        ("Services", _services(profile.get("services"))),
    ]
    return [(label, value) for label, value in rows if value]


def performance_rows(local: LocalInteractionData) -> list[dict[str, Any]]:
    """Expose every metric returned by the normalized Performance API contract."""
    rows: list[dict[str, Any]] = []
    for metric in local.performance_metric_deltas:
        rows.append({
            "label": metric.display_name,
            "current": metric.current_value,
            "prior": metric.prior_value,
            "absolute_change": metric.absolute_change,
            "change": metric.percentage_change,
            "direction": metric.direction,
        })
    return rows


def keyword_rows(local: LocalInteractionData, limit: int = 10) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in (local.monthly_search_keywords or [])[:limit]:
        keyword = scrub_gsc_query(item.get("search_keyword"))
        if not keyword:
            continue
        value = item.get("insights_value")
        threshold = item.get("insights_threshold")
        if value is not None:
            display_value = f"{value:,}"
            value_type = "exact"
        elif threshold is not None:
            display_value = f"Privacy threshold: {threshold:,}"
            value_type = "threshold"
        else:
            display_value = "Not reported"
            value_type = "unknown"
        rows.append({"keyword": keyword, "value": display_value, "value_type": value_type})
    return rows


def review_rows(local: LocalInteractionData, limit: int = 5) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for review in (local.reviews or [])[:limit]:
        rating = review.get("star_rating")
        status = str(review.get("reply_status") or "unavailable")
        status = status.replace("_", " ").title()
        rows.append({
            "rating": str(rating) if rating is not None else "Not reported",
            "reply_status": status,
            "updated": str(review.get("update_time") or review.get("create_time") or "")[:10],
            "comment": "Comment withheld for privacy",
        })
    return rows


def calls_rows(local: LocalInteractionData) -> list[tuple[str, str]]:
    if local.business_calls_status != "available":
        return []
    rows = []
    if local.answered_calls is not None:
        rows.append(("Answered calls", f"{local.answered_calls:,}"))
    if local.missed_calls is not None:
        rows.append(("Missed calls", f"{local.missed_calls:,}"))
    return rows
