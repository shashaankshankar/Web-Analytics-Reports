from __future__ import annotations

from typing import Any, Dict, List, Optional

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
    Metric,
    RunReportRequest,
)

from app.analytics.contracts import SourceAvailability


class GA4Extractor:
    """Read-only GA4 adapter with explicit query state and period provenance."""

    source_name = "ga4"

    def __init__(self, property_id: str, client: Optional[BetaAnalyticsDataClient] = None):
        self.property_id = str(property_id).replace("properties/", "")
        self._client = client

    @property
    def client(self) -> BetaAnalyticsDataClient:
        if self._client is None:
            self._client = BetaAnalyticsDataClient()
        return self._client

    def is_configured(self) -> bool:
        return bool(self.property_id and self.property_id.strip())

    def run_report(
        self,
        start_date: str,
        end_date: str,
        dimensions: List[str],
        metrics: List[str],
        limit: int = 10000,
        comparison_start_date: Optional[str] = None,
        comparison_end_date: Optional[str] = None,
        event_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run one GA4 query without hiding empty, unavailable, or error states."""
        result: Dict[str, Any] = {
            "source": self.source_name,
            "status": SourceAvailability.UNAVAILABLE.value,
            "start_date": start_date,
            "end_date": end_date,
            "request": {
                "dimensions": list(dimensions),
                "metrics": list(metrics),
                "limit": limit,
            },
            "rows": [],
            "row_count": 0,
        }
        if comparison_start_date and comparison_end_date:
            result["comparison_start_date"] = comparison_start_date
            result["comparison_end_date"] = comparison_end_date

        if not self.is_configured():
            result["status"] = SourceAvailability.NOT_CONFIGURED.value
            result["reason"] = "GA4 property is not configured."
            return result

        date_ranges = [DateRange(start_date=start_date, end_date=end_date)]
        if comparison_start_date and comparison_end_date:
            date_ranges.append(DateRange(start_date=comparison_start_date, end_date=comparison_end_date))

        try:
            request_kwargs = {
                "property": f"properties/{self.property_id}",
                "date_ranges": date_ranges,
                "dimensions": [Dimension(name=d) for d in dimensions],
                "metrics": [Metric(name=m) for m in metrics],
                "limit": limit,
            }
            if event_name:
                request_kwargs["dimension_filter"] = FilterExpression(
                    filter=Filter(
                        field_name="eventName",
                        string_filter=Filter.StringFilter(
                            match_type=Filter.StringFilter.MatchType.EXACT,
                            value=event_name,
                        ),
                    )
                )
            request = RunReportRequest(**request_kwargs)
            response = self.client.run_report(request=request)
        except Exception as exc:
            result["status"] = SourceAvailability.ERROR.value
            result["reason"] = f"GA4 request failed: {type(exc).__name__}."
            return result

        rows = [
            {
                "dimensions": [dim.value for dim in row.dimension_values],
                "metrics": [met.value for met in row.metric_values],
            }
            for row in response.rows
        ]
        result["rows"] = rows
        result["row_count"] = int(response.row_count)
        result["status"] = (
            SourceAvailability.AVAILABLE.value if rows else SourceAvailability.EMPTY.value
        )
        if not rows:
            result["reason"] = "GA4 returned no rows for the requested period and query."
        return result

    def fetch_metrics_and_channels(
        self,
        start_date: str,
        end_date: str,
        prior_start_date: Optional[str] = None,
        prior_end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch the report inputs required by the deterministic aggregator."""
        comparison_enabled = bool(prior_start_date and prior_end_date)
        if not self.is_configured():
            return {
                "source": self.source_name,
                "status": SourceAvailability.NOT_CONFIGURED.value,
                "reason": "GA4 property is not configured.",
                "summary": {},
                "prior_summary": {},
                "channels": [],
                "pages": [],
                "events": {},
                "prior_events": {},
                "errors": [],
                "row_counts": {},
                "query_statuses": {},
                "events_status": SourceAvailability.NOT_CONFIGURED.value,
                "prior_events_status": SourceAvailability.NOT_CONFIGURED.value,
            }

        # Do not use GA4's broad conversions metric. Primary leads are derived
        # from the explicit generate_lead event query.
        metrics_list = ["activeUsers", "sessions", "engagementRate", "bounceRate"]
        current_queries = {
            "summary": self.run_report(start_date, end_date, [], metrics_list),
            "channels": self.run_report(
                start_date,
                end_date,
                ["sessionDefaultChannelGroup"],
                ["sessions", "activeUsers"],
            ),
            "pages": self.run_report(
                start_date,
                end_date,
                ["landingPagePlusQueryString"],
                ["sessions", "activeUsers", "eventCount"],
                limit=50,
            ),
            "events": self.run_report(start_date, end_date, ["eventName"], ["eventCount"]),
        }
        prior_queries = (
            {
                "summary": self.run_report(prior_start_date, prior_end_date, [], metrics_list),
                "channels": self.run_report(
                    prior_start_date,
                    prior_end_date,
                    ["sessionDefaultChannelGroup"],
                    ["sessions", "activeUsers"],
                ),
                "pages": self.run_report(
                    prior_start_date,
                    prior_end_date,
                    ["landingPagePlusQueryString"],
                    ["sessions"],
                    limit=50,
                ),
                "events": self.run_report(prior_start_date, prior_end_date, ["eventName"], ["eventCount"]),
            }
            if comparison_enabled
            else {}
        )

        def parse_summary(raw: Dict[str, Any]) -> Dict[str, Optional[float]]:
            if raw.get("status") != SourceAvailability.AVAILABLE.value or not raw.get("rows"):
                return {}
            values = raw["rows"][0].get("metrics", [])
            return {
                "activeUsers": float(values[0]) if len(values) > 0 else None,
                "sessions": float(values[1]) if len(values) > 1 else None,
                "engagementRate": float(values[2]) if len(values) > 2 else None,
                "bounceRate": float(values[3]) if len(values) > 3 else None,
            }

        current_summary = current_queries["summary"]
        current_channels = current_queries["channels"]
        current_pages = current_queries["pages"]
        current_events = current_queries["events"]
        prior_summary = prior_queries.get("summary")
        prior_channels = prior_queries.get("channels")
        prior_pages = prior_queries.get("pages")
        prior_events = prior_queries.get("events")

        prior_channel_map = {
            row.get("dimensions", [""])[0] or "(not set)": {
                "sessions": int(float(row.get("metrics", [0])[0])) if row.get("metrics") else None,
                "activeUsers": int(float(row.get("metrics", [0, 0])[1])) if len(row.get("metrics", [])) > 1 else None,
            }
            for row in (prior_channels or {}).get("rows", [])
        }
        channels = []
        for row in current_channels.get("rows", []):
            dimensions = row.get("dimensions", [])
            metrics = row.get("metrics", [])
            channel = dimensions[0] if dimensions else "(not set)"
            sessions = int(float(metrics[0])) if metrics else None
            active_users = int(float(metrics[1])) if len(metrics) > 1 else None
            prior_info = prior_channel_map.get(channel)
            channels.append({
                "channel": channel,
                "sessions": sessions,
                "activeUsers": active_users,
                "primaryLeads": None,
                "priorSessions": prior_info["sessions"] if prior_info else None,
                "sessionChange": sessions - prior_info["sessions"] if sessions is not None and prior_info and prior_info["sessions"] is not None else None,
            })

        prior_pages_map = {
            row.get("dimensions", [""])[0]: int(float(row.get("metrics", [0])[0])) if row.get("metrics") else None
            for row in (prior_pages or {}).get("rows", [])
        }
        pages = []
        for row in current_pages.get("rows", []):
            dimensions = row.get("dimensions", [])
            metrics = row.get("metrics", [])
            raw_path = dimensions[0] if dimensions else "/"
            clean_path = raw_path.split("?")[0].split("#")[0] or "/"
            sessions = int(float(metrics[0])) if metrics else None
            active_users = int(float(metrics[1])) if len(metrics) > 1 else None
            prior_sessions = prior_pages_map.get(raw_path)
            pages.append({
                "pagePath": clean_path,
                "sessions": sessions,
                "activeUsers": active_users,
                "priorSessions": prior_sessions,
                "sessionChange": sessions - prior_sessions if sessions is not None and prior_sessions is not None else None,
            })

        events = {
            row.get("dimensions", [""])[0]: int(float(row.get("metrics", [0])[0])) if row.get("metrics") else None
            for row in current_events.get("rows", [])
            if row.get("dimensions")
        }
        prior_events_map = {
            row.get("dimensions", [""])[0]: int(float(row.get("metrics", [0])[0])) if row.get("metrics") else None
            for row in (prior_events or {}).get("rows", [])
            if row.get("dimensions")
        }

        errors = [
            raw.get("reason", "GA4 query failed")
            for raw in list(current_queries.values()) + list(prior_queries.values())
            if raw.get("status") == SourceAvailability.ERROR.value
        ]
        query_statuses = {
            name: {
                "current": current_queries[name].get("status"),
                "prior": prior_queries[name].get("status") if name in prior_queries else None,
                "current_reason": current_queries[name].get("reason"),
                "prior_reason": prior_queries[name].get("reason") if name in prior_queries else None,
            }
            for name in current_queries
        }

        current_statuses = {query.get("status") for query in current_queries.values()}
        current_summary_status = current_summary.get("status", SourceAvailability.NOT_CONFIGURED.value)
        if current_summary_status == SourceAvailability.ERROR.value:
            overall_status = SourceAvailability.ERROR.value
            reason = "GA4 current summary query failed."
        elif current_summary_status in {
            SourceAvailability.NOT_CONFIGURED.value,
            SourceAvailability.UNAVAILABLE.value,
            SourceAvailability.EMPTY.value,
        }:
            overall_status = current_summary_status
            reason = "GA4 did not return a current summary row."
        elif errors or any(status != SourceAvailability.AVAILABLE.value for status in current_statuses):
            overall_status = SourceAvailability.PARTIAL.value
            reason = "GA4 returned a current summary, but one or more supporting queries were incomplete."
        elif comparison_enabled and (
            prior_summary is None or prior_summary.get("status") != SourceAvailability.AVAILABLE.value
        ):
            overall_status = SourceAvailability.PARTIAL.value
            reason = "GA4 current data is available; the prior summary is incomplete, so comparison fields are limited."
        else:
            overall_status = SourceAvailability.AVAILABLE.value
            reason = None if comparison_enabled else "GA4 current observation returned without a comparison period."

        return {
            "source": self.source_name,
            "status": overall_status,
            "reason": reason,
            "summary": parse_summary(current_summary),
            "prior_summary": parse_summary(prior_summary or {}),
            "channels": channels,
            "pages": pages,
            "events": events,
            "prior_events": prior_events_map,
            "errors": errors,
            "row_counts": {
                name: {
                    "current": current_queries[name].get("row_count", 0),
                    "prior": prior_queries[name].get("row_count", 0) if name in prior_queries else None,
                }
                for name in current_queries
            },
            "query_statuses": query_statuses,
            "periods": {
                "current": {"start_date": start_date, "end_date": end_date},
                "prior": (
                    {"start_date": prior_start_date, "end_date": prior_end_date}
                    if comparison_enabled
                    else None
                ),
            },
            "current_status": current_summary.get("status"),
            "prior_status": prior_summary.get("status") if prior_summary else None,
            "events_status": current_events.get("status"),
            "prior_events_status": prior_events.get("status") if prior_events else None,
            "comparison_enabled": comparison_enabled,
        }
