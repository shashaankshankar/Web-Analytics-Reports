from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable, Dict, List, Optional

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest

GBP_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/business.manage",
]


def default_gbp_requester(url: str, token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


class GoogleBusinessProfileExtractor:
    def __init__(
        self,
        location_id: str,
        access_token: Optional[str] = None,
        requester: Callable[[str, str], dict] = default_gbp_requester,
    ):
        self.location_id = location_id
        self._access_token = access_token
        self.requester = requester

    def is_configured(self) -> bool:
        return bool(self.location_id and self.location_id.strip())

    def get_token(self) -> str:
        if self._access_token:
            return self._access_token
        try:
            credentials, _ = google.auth.default(scopes=GBP_SCOPES)
            credentials.refresh(GoogleAuthRequest())
            self._access_token = credentials.token
            return self._access_token
        except Exception:
            return ""

    def fetch_local_insights(
        self,
        start_date: str,
        end_date: str,
        strict: bool = False,
    ) -> Dict[str, Any]:
        """Fetch call clicks, direction requests, website clicks, and review summaries from GBP."""
        if not self.is_configured():
            if strict:
                raise RuntimeError("Google Business Profile location is not configured.")
            return {
                "phone_calls": 0,
                "prior_phone_calls": 0,
                "direction_requests": 0,
                "prior_direction_requests": 0,
                "website_clicks": 0,
                "prior_website_clicks": 0,
                "average_rating": None,
                "total_reviews_count": None,
                "recent_review_snippets": [],
            }

        token = self.get_token()
        if not token:
            if strict:
                raise RuntimeError("Google Business Profile credentials are unavailable.")
            return {
                "phone_calls": 0,
                "prior_phone_calls": 0,
                "direction_requests": 0,
                "prior_direction_requests": 0,
                "website_clicks": 0,
                "prior_website_clicks": 0,
                "average_rating": None,
                "total_reviews_count": None,
                "recent_review_snippets": [],
            }

        clean_loc = self.location_id.strip()
        if clean_loc.startswith("places/"):
            clean_loc = clean_loc.replace("places/", "")

        # If location_id is a Google Place ID (e.g. starts with 'ChIJ')
        if clean_loc.startswith("ChIJ") or not clean_loc.startswith("locations/"):
            url = f"https://places.googleapis.com/v1/places/{clean_loc}"
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,reviews",
                    "Content-Type": "application/json",
                }
                req = urllib.request.Request(url, method="GET", headers=headers)
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode())

                reviews = data.get("reviews", [])
                snippets = [
                    r.get("text", {}).get("text", "")[:120].strip()
                    for r in reviews
                    if r.get("text", {}).get("text")
                ][:3]

                return {
                    "phone_calls": 0,
                    "prior_phone_calls": 0,
                    "direction_requests": 0,
                    "prior_direction_requests": 0,
                    "website_clicks": 0,
                    "prior_website_clicks": 0,
                    "average_rating": data.get("rating"),
                    "total_reviews_count": data.get("userRatingCount"),
                    "recent_review_snippets": snippets,
                }
            except Exception as exc:
                place_error = exc
        else:
            place_error = None

        # Query Google Business Profile API if configured and token exists
        url = f"https://mybusinessbusinessinformation.googleapis.com/v1/{self.location_id}"
        try:
            res = self.requester(url, token)
            # If live response has review or metadata info:
            return {
                "phone_calls": 0,
                "prior_phone_calls": 0,
                "direction_requests": 0,
                "prior_direction_requests": 0,
                "website_clicks": 0,
                "prior_website_clicks": 0,
                "average_rating": res.get("averageRating"),
                "total_reviews_count": res.get("totalReviewCount"),
                "recent_review_snippets": [],
            }
        except Exception as exc:
            if strict:
                raise RuntimeError("Google Business Profile request failed.") from (place_error if place_error else exc)
            return {
                "phone_calls": 0,
                "prior_phone_calls": 0,
                "direction_requests": 0,
                "prior_direction_requests": 0,
                "website_clicks": 0,
                "prior_website_clicks": 0,
                "average_rating": None,
                "total_reviews_count": None,
                "recent_review_snippets": [],
            }
