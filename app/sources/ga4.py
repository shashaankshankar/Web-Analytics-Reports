from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest


class GA4Extractor:
    def __init__(self, property_id: str, client: Optional[BetaAnalyticsDataClient] = None):
        self.property_id = str(property_id).replace("properties/", "")
        self._client = client

    @property
    def client(self) -> BetaAnalyticsDataClient:
        if self._client is None:
            self._client = BetaAnalyticsDataClient()
        return self._client

    def is_configured(self) -> bool:
        return bool(self.property_id and self.property_id.strip() and self.property_id != "mock")

    def run_report(
        self,
        start_date: str,
        end_date: str,
        dimensions: List[str],
        metrics: List[str],
        limit: int = 10000,
        comparison_start_date: Optional[str] = None,
        comparison_end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.is_configured():
            return {"rows": [], "row_count": 0}

        date_ranges = [DateRange(start_date=start_date, end_date=end_date)]
        if comparison_start_date and comparison_end_date:
            date_ranges.append(DateRange(start_date=comparison_start_date, end_date=comparison_end_date))

        try:
            request = RunReportRequest(
                property=f"properties/{self.property_id}",
                date_ranges=date_ranges,
                dimensions=[Dimension(name=d) for d in dimensions],
                metrics=[Metric(name=m) for m in metrics],
                limit=limit,
            )
            response = self.client.run_report(request=request)
        except Exception as e:
            return {"rows": [], "row_count": 0, "error": str(e)}
        
        rows = [
            {
                "dimensions": [dim.value for dim in row.dimension_values],
                "metrics": [met.value for met in row.metric_values],
            }
            for row in response.rows
        ]
        return {"rows": rows, "row_count": response.row_count}

    def fetch_metrics_and_channels(
        self,
        start_date: str,
        end_date: str,
        prior_start_date: str,
        prior_end_date: str,
    ) -> Dict[str, Any]:
        """Fetch summary metrics, channels, landing pages, and key conversion events."""
        if not self.is_configured():
            return {
                "summary": {"activeUsers": 0, "sessions": 0, "engagementRate": 0.0, "conversions": 0},
                "prior_summary": {"activeUsers": 0, "sessions": 0, "engagementRate": 0.0, "conversions": 0},
                "channels": [],
                "pages": [],
                "events": {},
                "prior_events": {},
            }

        # 1. Summary core metrics
        metrics_list = ["activeUsers", "sessions", "engagementRate", "bounceRate", "conversions"]
        curr_summary_raw = self.run_report(start_date, end_date, [], metrics_list)
        prior_summary_raw = self.run_report(prior_start_date, prior_end_date, [], metrics_list)

        def parse_summary(raw: Dict[str, Any]) -> Dict[str, float]:
            if not raw["rows"]:
                return {"activeUsers": 0, "sessions": 0, "engagementRate": 0.0, "bounceRate": 0.0, "conversions": 0}
            vals = raw["rows"][0]["metrics"]
            return {
                "activeUsers": float(vals[0]) if len(vals) > 0 else 0,
                "sessions": float(vals[1]) if len(vals) > 1 else 0,
                "engagementRate": float(vals[2]) if len(vals) > 2 else 0.0,
                "bounceRate": float(vals[3]) if len(vals) > 3 else 0.0,
                "conversions": float(vals[4]) if len(vals) > 4 else 0,
            }

        # 2. Acquisition channels
        curr_channels_raw = self.run_report(start_date, end_date, ["sessionDefaultChannelGroup"], ["sessions", "activeUsers", "conversions"])
        prior_channels_raw = self.run_report(prior_start_date, prior_end_date, ["sessionDefaultChannelGroup"], ["sessions", "activeUsers", "conversions"])

        prior_channel_map = {
            r["dimensions"][0] or "(not set)": {
                "sessions": int(float(r["metrics"][0])),
                "activeUsers": int(float(r["metrics"][1])),
                "conversions": int(float(r["metrics"][2])) if len(r["metrics"]) > 2 else 0,
            }
            for r in prior_channels_raw.get("rows", [])
        }

        channels = []
        for r in curr_channels_raw.get("rows", []):
            ch_name = r["dimensions"][0] or "(not set)"
            sessions = int(float(r["metrics"][0]))
            users = int(float(r["metrics"][1]))
            conversions = int(float(r["metrics"][2])) if len(r["metrics"]) > 2 else 0
            prior_info = prior_channel_map.get(ch_name, {"sessions": 0, "activeUsers": 0, "conversions": 0})
            channels.append({
                "channel": ch_name,
                "sessions": sessions,
                "activeUsers": users,
                "conversions": conversions,
                "priorSessions": prior_info["sessions"],
                "sessionChange": sessions - prior_info["sessions"],
            })

        # 3. Landing pages
        curr_pages_raw = self.run_report(start_date, end_date, ["landingPagePlusQueryString"], ["sessions", "activeUsers", "eventCount"], limit=50)
        prior_pages_raw = self.run_report(prior_start_date, prior_end_date, ["landingPagePlusQueryString"], ["sessions"], limit=50)
        prior_pages_map = {r["dimensions"][0]: int(float(r["metrics"][0])) for r in prior_pages_raw.get("rows", [])}

        pages = []
        for r in curr_pages_raw.get("rows", []):
            raw_path = r["dimensions"][0] or "/"
            clean_path = raw_path.split("?")[0].split("#")[0] or "/"
            sessions = int(float(r["metrics"][0]))
            users = int(float(r["metrics"][1]))
            prior_sess = prior_pages_map.get(raw_path, 0)
            pages.append({
                "pagePath": clean_path,
                "sessions": sessions,
                "activeUsers": users,
                "priorSessions": prior_sess,
                "sessionChange": sessions - prior_sess,
            })

        # 4. Events count (e.g. generate_lead, form_submit, phone_click, etc.)
        curr_events_raw = self.run_report(start_date, end_date, ["eventName"], ["eventCount"])
        prior_events_raw = self.run_report(prior_start_date, prior_end_date, ["eventName"], ["eventCount"])

        errors = [
            raw.get("error")
            for raw in (curr_summary_raw, prior_summary_raw, curr_channels_raw, prior_channels_raw,
                        curr_pages_raw, prior_pages_raw, curr_events_raw, prior_events_raw)
            if raw.get("error")
        ]

        events = {r["dimensions"][0]: int(float(r["metrics"][0])) for r in curr_events_raw.get("rows", [])}
        prior_events = {r["dimensions"][0]: int(float(r["metrics"][0])) for r in prior_events_raw.get("rows", [])}

        return {
            "summary": parse_summary(curr_summary_raw),
            "prior_summary": parse_summary(prior_summary_raw),
            "channels": channels,
            "pages": pages,
            "events": events,
            "prior_events": prior_events,
            "errors": errors,
            "row_counts": {
                "summary": curr_summary_raw.get("row_count", 0),
                "channels": curr_channels_raw.get("row_count", 0),
                "pages": curr_pages_raw.get("row_count", 0),
                "events": curr_events_raw.get("row_count", 0),
            },
        }
