from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest

from app.analytics.contracts import SourceAvailability

SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def default_gsc_requester(url: str, token: str, payload: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read())


class SearchConsoleExtractor:
    """Read-only Search Console adapter with explicit query state."""

    source_name = "gsc"

    def __init__(
        self,
        site_url: str,
        access_token: Optional[str] = None,
        requester: Callable[[str, str, dict], dict] = default_gsc_requester,
    ):
        self.site_url = site_url
        self._access_token = access_token
        self.requester = requester

    def is_configured(self) -> bool:
        return bool(self.site_url and self.site_url.startswith(("https://", "http://", "sc-domain:")))

    def get_token(self) -> str:
        if self._access_token:
            return self._access_token
        try:
            credentials, _ = google.auth.default(scopes=[SEARCH_CONSOLE_SCOPE])
            credentials.refresh(GoogleAuthRequest())
            self._access_token = credentials.token
            return self._access_token
        except Exception:
            return ""

    def fetch_search_analytics(
        self,
        start_date: str,
        end_date: str,
        row_limit: int = 1000,
        data_state: str = "final",
        query_filter: Optional[str] = None,
        strict: bool = False,
    ) -> Dict[str, Any]:
        """Fetch queries and preserve empty, unavailable, and error states."""
        query = {
            "dimensions": ["query"],
            "row_limit": row_limit,
            "data_state": data_state,
        }
        if query_filter:
            query["query_filter"] = query_filter

        result: Dict[str, Any] = {
            "source": self.source_name,
            "status": SourceAvailability.UNAVAILABLE.value,
            "start_date": start_date,
            "end_date": end_date,
            "query": query,
            "rows": [],
            "row_count": 0,
            "truncated": False,
        }
        if not self.is_configured():
            result["status"] = SourceAvailability.NOT_CONFIGURED.value
            result["reason"] = "Search Console site is not configured."
            return result

        token = self.get_token()
        if not token:
            result["reason"] = "Search Console credentials are unavailable."
            return result

        encoded_site = urllib.parse.quote(self.site_url, safe="")
        url = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
        payload = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["query"],
            "rowLimit": row_limit,
            "dataState": data_state,
        }
        if query_filter:
            payload["dimensionFilterGroups"] = [{
                "filters": [{
                    "dimension": "query",
                    "operator": "contains",
                    "expression": query_filter,
                }]
            }]

        try:
            response = self.requester(url, token, payload)
        except Exception as exc:
            result["status"] = SourceAvailability.ERROR.value
            result["reason"] = f"Search Console request failed: {type(exc).__name__}."
            return result

        rows = []
        for row in response.get("rows", []):
            query_value = row.get("keys", [""])[0]
            rows.append({
                "query": query_value,
                "clicks": int(row.get("clicks", 0)),
                "impressions": int(row.get("impressions", 0)),
                "ctr": float(row.get("ctr", 0.0)),
                "position": round(float(row.get("position", 0.0)), 1),
            })
        result["rows"] = rows
        result["row_count"] = len(rows)
        # Search Console does not return a total row count. A full page at the
        # requested limit is therefore treated conservatively as truncated.
        result["truncated"] = len(rows) >= row_limit if row_limit > 0 else False
        result["status"] = (
            SourceAvailability.AVAILABLE.value if rows else SourceAvailability.EMPTY.value
        )
        if not rows:
            result["reason"] = "Search Console returned no query rows for the requested period."
        return result

    def fetch_comparative_search_analytics(
        self,
        start_date: str,
        end_date: str,
        prior_start_date: str,
        prior_end_date: str,
        row_limit: int = 1000,
        strict: bool = False,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Fetch current and prior query snapshots without treating missing data as zero."""
        current = self.fetch_search_analytics(start_date, end_date, row_limit=row_limit, strict=strict)
        prior = self.fetch_search_analytics(prior_start_date, prior_end_date, row_limit=row_limit, strict=strict)
        return current, prior


def filter_search_rows(result: Dict[str, Any], query_regex: str = "", min_impressions: int = 10, limit: int = 15) -> list[dict[str, Any]]:
    """Apply an explorer filter to already fetched Search Console rows."""
    if result.get("status") != SourceAvailability.AVAILABLE.value:
        return []
    pattern = re.compile(query_regex, re.IGNORECASE) if query_regex else None
    filtered = [
        row for row in result.get("rows", [])
        if int(row.get("impressions", 0)) >= min_impressions
        and (pattern.search(str(row.get("query", ""))) if pattern else True)
    ]
    return filtered[:limit]
