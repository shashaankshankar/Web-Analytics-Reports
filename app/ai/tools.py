from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from app.config import ClientConfig
from app.sources.ga4 import GA4Extractor
from app.sources.gsc import SearchConsoleExtractor
from app.sources.gbp import GoogleBusinessProfileExtractor


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]


class MultiSourceAnalyticsToolkit:
    """Provides dynamic exploratory data tools across GA4, Search Console, and Google Business Profile."""

    def __init__(
        self,
        client: ClientConfig,
        start_date: str,
        end_date: str,
        prior_start_date: str,
        prior_end_date: str,
        ga4_extractor: Optional[GA4Extractor] = None,
        gsc_extractor: Optional[SearchConsoleExtractor] = None,
        gbp_extractor: Optional[GoogleBusinessProfileExtractor] = None,
        mock_data: bool = False,
    ):
        self.client_config = client
        self.start_date = start_date
        self.end_date = end_date
        self.prior_start_date = prior_start_date
        self.prior_end_date = prior_end_date
        self.mock_data = mock_data

        self.ga4_extractor = ga4_extractor or GA4Extractor(client.ga4_property_id)
        self.gsc_extractor = gsc_extractor or SearchConsoleExtractor(client.gsc_site_url)
        self.gbp_extractor = gbp_extractor or GoogleBusinessProfileExtractor(client.gbp_location_id)

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return OpenAI / OpenRouter function calling schemas."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "query_ga4_dimensions",
                    "description": "Query Google Analytics 4 with flexible dimensions (e.g. deviceCategory, city, pagePath, sessionSourceMedium) and metrics (e.g. sessions, activeUsers, conversions, bounceRate) to discover segments with high growth or drop-offs.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "dimensions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "GA4 dimension names, e.g. ['deviceCategory'], ['city'], ['sessionSourceMedium']",
                            },
                            "metrics": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "GA4 metric names, e.g. ['sessions', 'conversions', 'engagementRate']",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max number of rows to return (default 10)",
                                "default": 10,
                            },
                        },
                        "required": ["dimensions", "metrics"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_gsc_search_queries",
                    "description": "Inspect Google Search Console keyword queries, filtering by keyword intent regex or minimum impressions to find high-opportunity long-tail queries or unexpected search term spikes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query_regex": {
                                "type": "string",
                                "description": "Optional substring/regex filter for search queries (e.g. 'near me', 'cost', 'reviews')",
                            },
                            "min_impressions": {
                                "type": "integer",
                                "description": "Minimum impression threshold to filter out noise",
                                "default": 10,
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max queries to return",
                                "default": 15,
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_gbp_local_reputation",
                    "description": "Inspect Google Business Profile local customer actions (call clicks, direction requests, website clicks) and review sentiment/ratings for the client location.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
        ]

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool dynamically and return the stringified result."""
        try:
            if tool_name == "query_ga4_dimensions":
                return self._exec_ga4(arguments)
            elif tool_name == "query_gsc_search_queries":
                return self._exec_gsc(arguments)
            elif tool_name == "query_gbp_local_reputation":
                return self._exec_gbp(arguments)
            else:
                return json.dumps({"error": f"Unknown tool name: {tool_name}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _exec_ga4(self, args: Dict[str, Any]) -> str:
        dimensions = args.get("dimensions", ["deviceCategory"])
        metrics = args.get("metrics", ["sessions", "conversions"])
        limit = args.get("limit", 10)

        if self.mock_data or not self.ga4_extractor.is_configured():
            if "deviceCategory" in dimensions:
                return json.dumps({
                    "data": [
                        {"deviceCategory": "mobile", "sessions": 1150, "conversions": 34, "conversionRate": 0.029},
                        {"deviceCategory": "desktop", "sessions": 620, "conversions": 13, "conversionRate": 0.021},
                        {"deviceCategory": "tablet", "sessions": 80, "conversions": 1, "conversionRate": 0.012},
                    ],
                    "note": f"Mobile accounts for 62% of traffic and 71% of high-intent conversions for {self.client_config.company_name}.",
                })
            elif "city" in dimensions:
                return json.dumps({
                    "data": [
                        {"city": "Metro Core", "sessions": 840, "conversions": 28},
                        {"city": "Suburban East", "sessions": 510, "conversions": 16},
                        {"city": "Suburban North", "sessions": 210, "conversions": 3},
                        {"city": "Outlying West", "sessions": 140, "conversions": 1},
                    ],
                    "note": f"Suburban East demonstrates highest conversion density per session (3.1%) for {self.client_config.company_name}.",
                })
            else:
                return json.dumps({
                    "data": [
                        {"dimension": "Segment A", "sessions": 950, "conversions": 28},
                        {"dimension": "Segment B", "sessions": 450, "conversions": 12},
                    ],
                })

        res = self.ga4_extractor.run_report(self.start_date, self.end_date, dimensions, metrics, limit=limit)
        # Format raw rows into structured key-value dictionaries
        structured_rows = []
        for row in res.get("rows", []):
            dim_vals = row.get("dimensions", [])
            met_vals = row.get("metrics", [])
            entry = {}
            for idx, d_name in enumerate(dimensions):
                entry[d_name] = dim_vals[idx] if idx < len(dim_vals) else ""
            for idx, m_name in enumerate(metrics):
                entry[m_name] = met_vals[idx] if idx < len(met_vals) else "0"
            structured_rows.append(entry)
        return json.dumps({"data": structured_rows, "total_rows": res.get("row_count", len(structured_rows))})

    def _exec_gsc(self, args: Dict[str, Any]) -> str:
        query_regex = args.get("query_regex", "").lower()
        min_impressions = args.get("min_impressions", 10)
        limit = args.get("limit", 15)

        if self.mock_data or not self.gsc_extractor.is_configured():
            mock_pool = [
                {"query": f"{self.client_config.company_name.lower()} reviews", "clicks": 85, "impressions": 420, "ctr": 0.202, "position": 2.1},
                {"query": f"best {self.client_config.industry.replace('_', ' ')} near me", "clicks": 28, "impressions": 1450, "ctr": 0.019, "position": 11.4},
                {"query": f"emergency {self.client_config.industry.replace('_', ' ')} cost", "clicks": 14, "impressions": 980, "ctr": 0.014, "position": 14.2},
                {"query": f"top rated {self.client_config.industry.replace('_', ' ')}", "clicks": 19, "impressions": 1120, "ctr": 0.017, "position": 9.8},
                {"query": "consultation booking online", "clicks": 12, "impressions": 650, "ctr": 0.018, "position": 16.5},
                {"query": f"affordable {self.client_config.industry.replace('_', ' ')} payment plans", "clicks": 9, "impressions": 540, "ctr": 0.016, "position": 13.1},
            ]
            filtered = [
                q for q in mock_pool
                if q["impressions"] >= min_impressions and (not query_regex or query_regex in q["query"])
            ][:limit]
            return json.dumps({"queries": filtered, "count": len(filtered)})

        raw_queries = self.gsc_extractor.fetch_search_analytics(self.start_date, self.end_date, row_limit=limit * 3)
        filtered = [
            q for q in raw_queries
            if q["impressions"] >= min_impressions and (not query_regex or query_regex in q["query"].lower())
        ][:limit]
        return json.dumps({"queries": filtered, "count": len(filtered)})

    def _exec_gbp(self, args: Dict[str, Any]) -> str:
        if self.mock_data or not self.gbp_extractor.is_configured():
            return json.dumps({
                "location_id": self.client_config.gbp_location_id or "loc-mock-123",
                "phone_calls": 42,
                "direction_requests": 68,
                "website_clicks": 112,
                "average_rating": 4.9,
                "total_reviews": 94,
                "recent_reviews": [
                    {"rating": 5, "comment": "Outstanding experience! Very gentle team."},
                    {"rating": 5, "comment": "Fast appointment scheduling."},
                ],
            })

        insights = self.gbp_extractor.fetch_local_insights(self.start_date, self.end_date)
        return json.dumps(insights)
