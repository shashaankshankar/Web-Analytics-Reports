from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest

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
    ) -> List[Dict[str, Any]]:
        """Fetch search queries, clicks, impressions, CTR, and average position."""
        if not self.is_configured():
            return []

        token = self.get_token()
        if not token:
            return []

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
            result = self.requester(url, token, payload)
        except Exception:
            return []

        rows = result.get("rows", [])
        output = []
        for row in rows:
            query = row.get("keys", [""])[0]
            clicks = int(row.get("clicks", 0))
            impressions = int(row.get("impressions", 0))
            ctr = float(row.get("ctr", 0.0))
            position = float(row.get("position", 0.0))
            output.append({
                "query": query,
                "clicks": clicks,
                "impressions": impressions,
                "ctr": ctr,
                "position": round(position, 1),
            })
        return output

    def fetch_comparative_search_analytics(
        self,
        start_date: str,
        end_date: str,
        prior_start_date: str,
        prior_end_date: str,
        row_limit: int = 1000,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch both current and prior period search queries."""
        current = self.fetch_search_analytics(start_date, end_date, row_limit=row_limit)
        prior = self.fetch_search_analytics(prior_start_date, prior_end_date, row_limit=row_limit)
        return current, prior
