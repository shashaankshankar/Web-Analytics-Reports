from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials as UserCredentials

from app.analytics.contracts import SourceAvailability

GBP_SCOPES = [
    "https://www.googleapis.com/auth/business.manage",
]
GBP_TOKEN_URI = "https://oauth2.googleapis.com/token"
GBP_OAUTH_CREDENTIALS_ENV = "GBP_OAUTH_CREDENTIALS_JSON"
GBP_OAUTH_CLIENT_ID_ENV = "GBP_OAUTH_CLIENT_ID"
GBP_OAUTH_CLIENT_SECRET_ENV = "GBP_OAUTH_CLIENT_SECRET"
GBP_OAUTH_REFRESH_TOKEN_ENV = "GBP_OAUTH_REFRESH_TOKEN"
GBP_ALLOW_ADC_FALLBACK_ENV = "GBP_ALLOW_ADC_FALLBACK"

GBP_ACCOUNT_MANAGEMENT_BASE_URL = "https://mybusinessaccountmanagement.googleapis.com/v1"
GBP_BUSINESS_INFORMATION_BASE_URL = "https://mybusinessbusinessinformation.googleapis.com/v1"
GBP_PERFORMANCE_BASE_URL = "https://businessprofileperformance.googleapis.com/v1"
GBP_REVIEWS_BASE_URL = "https://mybusiness.googleapis.com/v4"
GBP_BUSINESS_CALLS_BASE_URL = "https://mybusinessbusinesscalls.googleapis.com/v1"

GBP_LOCATION_READ_MASK = ",".join([
    "name",
    "languageCode",
    "storeCode",
    "title",
    "phoneNumbers",
    "categories",
    "storefrontAddress",
    "websiteUri",
    "regularHours",
    "specialHours",
    "serviceArea",
    "labels",
    "adWordsLocationExtensions",
    "latlng",
    "openInfo",
    "metadata",
    "profile",
    "relationshipData",
    "moreHours",
    "serviceItems",
])

GBP_DAILY_METRICS = (
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "BUSINESS_CONVERSATIONS",
    "BUSINESS_DIRECTION_REQUESTS",
    "CALL_CLICKS",
    "WEBSITE_CLICKS",
    "BUSINESS_BOOKINGS",
    "BUSINESS_FOOD_ORDERS",
    "BUSINESS_FOOD_MENU_CLICKS",
)

GBP_PERFORMANCE_METRIC_LABELS = {
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": "Desktop Maps Impressions",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": "Desktop Search Impressions",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS": "Mobile Maps Impressions",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": "Mobile Search Impressions",
    "BUSINESS_CONVERSATIONS": "Business Conversations",
    "BUSINESS_DIRECTION_REQUESTS": "Direction Requests",
    "CALL_CLICKS": "Call Button Clicks",
    "WEBSITE_CLICKS": "Website Clicks",
    "BUSINESS_BOOKINGS": "Bookings",
    "BUSINESS_FOOD_ORDERS": "Food Orders",
    "BUSINESS_FOOD_MENU_CLICKS": "Food Menu Clicks",
}


class GBPRequestError(RuntimeError):
    """Request failure with a safe HTTP status diagnostic."""

    def __init__(self, status_code: int, message: str = "Google Business Profile request failed"):
        super().__init__(message)
        self.status_code = status_code


def default_gbp_requester(url: str, token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Do not include the response body: Google error bodies can contain
        # account identifiers or other data that should not enter diagnostics.
        raise GBPRequestError(exc.code) from exc


class GoogleBusinessProfileExtractor:
    """Read-only GBP adapter with explicit sub-source availability states."""

    source_name = "gbp"

    def __init__(
        self,
        location_id: str,
        access_token: Optional[str] = None,
        requester: Callable[[str, str], dict] = default_gbp_requester,
        account_id: Optional[str] = None,
        public_place_id: Optional[str] = None,
        candidate_location_ids: Optional[list[str]] = None,
        business_title: Optional[str] = None,
        company_name: Optional[str] = None,
    ):
        self.location_id = location_id
        self.public_place_id = str(public_place_id or "").strip().removeprefix("places/")
        self.candidate_location_ids = [
            str(value).strip()
            for value in (candidate_location_ids or [])
            if str(value).strip().startswith("locations/")
        ]
        if str(location_id or "").strip().startswith("locations/") and str(location_id).strip() not in self.candidate_location_ids:
            self.candidate_location_ids.insert(0, str(location_id).strip())
        self.business_title = (business_title or company_name or "").strip() or None
        self._access_token = access_token
        self.requester = requester
        configured_account = account_id or os.getenv("GBP_ACCOUNT_ID", "")
        self.account_id = self._account_resource_name(configured_account)
        self._resolved_location_id: Optional[str] = None
        self._resolved_account_id: Optional[str] = self.account_id

    def is_configured(self) -> bool:
        return bool(
            (self.location_id and self.location_id.strip())
            or self.candidate_location_ids
            or self.public_place_id
        )

    def get_token(self) -> str:
        if self._access_token:
            return self._access_token

        # Private Business Profile access is authorized as a Google user. The
        # scheduled service must therefore refresh a user OAuth credential,
        # rather than assume that its Cloud Run service account has GBP access.
        oauth_credentials = self._user_oauth_credentials()
        if oauth_credentials is not None:
            try:
                oauth_credentials.refresh(GoogleAuthRequest())
                self._access_token = oauth_credentials.token or ""
                return self._access_token
            except Exception:
                return ""

        # ADC is opt-in for local development only. Production must fail
        # closed when the user OAuth bundle is absent or malformed rather than
        # silently changing the Google principal used for private GBP calls.
        if os.getenv(GBP_ALLOW_ADC_FALLBACK_ENV, "").strip().lower() not in {
            "1",
            "true",
            "yes",
        }:
            return ""
        try:
            credentials, _ = google.auth.default(scopes=GBP_SCOPES)
            credentials.refresh(GoogleAuthRequest())
            self._access_token = credentials.token
            return self._access_token
        except Exception:
            return ""

    @staticmethod
    def _user_oauth_credentials() -> Optional[UserCredentials]:
        """Build refreshable user credentials from injected secret material."""
        credential_data: dict[str, Any] = {}
        raw_bundle = os.getenv(GBP_OAUTH_CREDENTIALS_ENV, "").strip()
        if raw_bundle:
            try:
                parsed = json.loads(raw_bundle)
            except json.JSONDecodeError:
                return None
            if not isinstance(parsed, dict):
                return None
            credential_data = parsed

        client_id = str(credential_data.get("client_id") or os.getenv(GBP_OAUTH_CLIENT_ID_ENV, "")).strip()
        client_secret = str(
            credential_data.get("client_secret") or os.getenv(GBP_OAUTH_CLIENT_SECRET_ENV, "")
        ).strip()
        refresh_token = str(
            credential_data.get("refresh_token") or os.getenv(GBP_OAUTH_REFRESH_TOKEN_ENV, "")
        ).strip()
        if not client_id or not refresh_token:
            return None

        return UserCredentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=str(credential_data.get("token_uri") or GBP_TOKEN_URI),
            client_id=client_id,
            client_secret=client_secret or None,
            scopes=GBP_SCOPES,
        )

    @staticmethod
    def _account_resource_name(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        return text if text.startswith("accounts/") else f"accounts/{text}"

    @staticmethod
    def _status_from_exception(exc: Exception, unavailable_codes: set[int] | None = None) -> SourceAvailability:
        if isinstance(exc, GBPRequestError) and exc.status_code in (unavailable_codes or set()):
            return SourceAvailability.UNAVAILABLE
        return SourceAvailability.ERROR

    @staticmethod
    def _exception_reason(prefix: str, exc: Exception) -> str:
        if isinstance(exc, GBPRequestError):
            return f"{prefix} (HTTP {exc.status_code})."
        return f"{prefix}: {type(exc).__name__}."

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _date_parts(value: str) -> tuple[str, str, str]:
        year, month, day = value.split("-", 2)
        return year, month, day

    @classmethod
    def _month_parts(cls, value: str) -> tuple[str, str]:
        year, month, _ = cls._date_parts(value)
        return year, month

    @staticmethod
    def _query_url(base_url: str, query: list[tuple[str, str]]) -> str:
        return f"{base_url}?{urllib.parse.urlencode(query, doseq=True)}"

    @staticmethod
    def _empty_action_fields() -> dict[str, Any]:
        return {
            "phone_calls": None,
            "direction_requests": None,
            "website_clicks": None,
            "answered_calls": None,
            "missed_calls": None,
        }

    @staticmethod
    def _action_fields_from_performance(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Map the legacy report fields to their authoritative GBP metrics."""
        mapping = {
            "CALL_CLICKS": "phone_calls",
            "BUSINESS_DIRECTION_REQUESTS": "direction_requests",
            "WEBSITE_CLICKS": "website_clicks",
        }
        return {
            output_field: metrics[metric].get("total")
            for metric, output_field in mapping.items()
            if metric in metrics and metrics[metric].get("total") is not None
        }

    def _private_location_resource(self) -> str:
        if self._resolved_location_id:
            return self._resolved_location_id
        clean_location = self.location_id.strip()
        return clean_location if clean_location.startswith("locations/") else ""

    def _private_account_resource(self) -> Optional[str]:
        return self._resolved_account_id or self.account_id

    def _base_result(self, status: SourceAvailability, reason: str) -> Dict[str, Any]:
        return {
            "source": self.source_name,
            "status": status.value,
            "reason": reason,
            "location_id": self.location_id,
            "private_location_id": self._private_location_resource() or None,
            "account_id": self._private_account_resource(),
            **self._empty_action_fields(),
            "average_rating": None,
            "total_reviews_count": None,
            "recent_review_snippets": [],
            "profile_data": {},
            "profile_summary": {},
            "profile_status": SourceAvailability.UNAVAILABLE.value,
            "performance_metrics": {},
            "available_performance_metrics": [],
            "performance_status": SourceAvailability.UNAVAILABLE.value,
            "monthly_search_keywords": [],
            "search_keywords_status": SourceAvailability.UNAVAILABLE.value,
            "reviews": [],
            "reviews_status": SourceAvailability.UNAVAILABLE.value,
            "review_inventory_complete": False,
            "review_response_summary": {},
            "business_calls": {},
            "business_calls_status": SourceAvailability.UNAVAILABLE.value,
            "capabilities": {
                "profile_metadata": status.value,
                "action_metrics": SourceAvailability.UNAVAILABLE.value,
                "search_keywords": SourceAvailability.UNAVAILABLE.value,
                "reviews": SourceAvailability.UNAVAILABLE.value,
                "business_calls": SourceAvailability.UNAVAILABLE.value,
                "period_comparison": SourceAvailability.UNAVAILABLE.value,
            },
            "limitations": [
                "GBP performance, keyword, review, and Business Calls data require a private locations/<id> resource and user OAuth access.",
            ],
        }

    @staticmethod
    def _profile_summary(data: dict[str, Any]) -> dict[str, Any]:
        display_name = data.get("displayName") or {}
        if isinstance(display_name, dict):
            display_name = display_name.get("text")
        phone_numbers = data.get("phoneNumbers") or {}
        categories = data.get("categories") or {}
        metadata = data.get("metadata") or {}
        profile = data.get("profile") or {}
        return {
            "title": data.get("title") or display_name,
            "language_code": data.get("languageCode"),
            "store_code": data.get("storeCode"),
            "primary_phone": phone_numbers.get("primaryPhone"),
            "additional_phones": phone_numbers.get("additionalPhones", []),
            "address": data.get("storefrontAddress"),
            "website_uri": data.get("websiteUri"),
            "regular_hours": data.get("regularHours"),
            "special_hours": data.get("specialHours"),
            "primary_category": categories.get("primaryCategory"),
            "additional_categories": categories.get("additionalCategories", []),
            "service_area": data.get("serviceArea"),
            "services": data.get("serviceItems", []),
            "description": profile.get("description"),
            "labels": data.get("labels", []),
            "latlng": data.get("latlng"),
            "open_info": data.get("openInfo"),
            "maps_uri": metadata.get("mapsUri"),
            "new_review_uri": metadata.get("newReviewUri"),
            "metadata": metadata,
            "more_hours": data.get("moreHours", []),
        }

    @staticmethod
    def _normalize_places_review(review: dict[str, Any]) -> dict[str, Any]:
        text = review.get("text") or {}
        original_text = review.get("originalText") or {}
        author = review.get("authorAttribution") or {}
        return {
            "source": "places",
            "name": review.get("name"),
            "review_id": review.get("name"),
            "comment": text.get("text") if isinstance(text, dict) else text,
            "original_text": original_text.get("text") if isinstance(original_text, dict) else original_text,
            "star_rating": review.get("rating"),
            "create_time": review.get("publishTime"),
            "update_time": review.get("publishTime"),
            "reviewer": author,
            "review_reply": None,
            "reply_status": "unavailable",
            "review_media_items": [],
            "review_reply_url": None,
            "relative_publish_time_description": review.get("relativePublishTimeDescription"),
            "google_maps_uri": review.get("googleMapsUri"),
        }

    @staticmethod
    def _normalize_managed_review(review: dict[str, Any]) -> dict[str, Any]:
        reviewer = review.get("reviewer") or {}
        reply = review.get("reviewReply") or review.get("review_reply")
        if not isinstance(reply, dict) or not reply:
            reply = None
        reply_status = "NOT_REPLIED"
        if reply:
            reply_status = reply.get("reviewReplyState") or reply.get("review_reply_state") or "REPLY_PRESENT"
        return {
            "source": "managed_reviews",
            "name": review.get("name"),
            "review_id": review.get("reviewId") or review.get("review_id"),
            "comment": review.get("comment"),
            "star_rating": review.get("starRating") or review.get("star_rating"),
            "create_time": review.get("createTime") or review.get("create_time"),
            "update_time": review.get("updateTime") or review.get("update_time"),
            "reviewer": reviewer,
            "review_reply": reply,
            "reply_status": reply_status,
            "review_media_items": review.get("reviewMediaItems") or review.get("review_media_items") or [],
            "review_reply_url": review.get("reviewReplyUrl") or review.get("review_reply_url"),
        }

    @staticmethod
    def _review_response_summary(reviews: list[dict[str, Any]], total_review_count: Optional[int]) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        for review in reviews:
            status = str(review.get("reply_status") or "NOT_REPLIED")
            status_counts[status] = status_counts.get(status, 0) + 1
        review_count = total_review_count if total_review_count is not None else len(reviews)
        reply_present_count = sum(
            count for status, count in status_counts.items() if status not in {"NOT_REPLIED", "unavailable"}
        )
        complete = total_review_count is not None and len(reviews) == total_review_count
        return {
            "review_count": review_count,
            "reviews_fetched": len(reviews),
            "reply_present_count": reply_present_count,
            "unreplied_count": status_counts.get("NOT_REPLIED", 0),
            "reply_coverage_percent": (
                round((reply_present_count / review_count) * 100.0, 1)
                if complete and review_count
                else None
            ),
            "reply_status_counts": status_counts,
            "complete": complete,
        }

    def _profile_result(self, data: dict[str, Any], profile_source: str = "private") -> Dict[str, Any]:
        raw_reviews = data.get("reviews") or []
        if profile_source == "places":
            normalized_reviews = [self._normalize_places_review(review) for review in raw_reviews]
            snippets = [
                str(review.get("comment") or "")[:120].strip()
                for review in normalized_reviews
                if review.get("comment")
            ][:3]
        else:
            normalized_reviews = []
            snippets = []
        result = self._base_result(
            SourceAvailability.AVAILABLE,
            "GBP profile metadata was returned; related performance and reputation data are fetched separately.",
        )
        result.update({
            "profile_data": data,
            "profile_summary": self._profile_summary(data),
            "profile_status": SourceAvailability.AVAILABLE.value,
            "average_rating": data.get("rating", data.get("averageRating")),
            "total_reviews_count": data.get("userRatingCount", data.get("totalReviewCount")),
            "recent_review_snippets": snippets,
            "reviews": normalized_reviews,
        })
        result["capabilities"]["profile_metadata"] = SourceAvailability.AVAILABLE.value
        if profile_source == "places":
            result["reviews_status"] = SourceAvailability.AVAILABLE.value if normalized_reviews else SourceAvailability.EMPTY.value
            result["review_inventory_complete"] = False
            result["limitations"] = [
                "Public Places reviews are a limited sample; full review inventory and reply status require the private Reviews API.",
                "GBP performance, keyword, and Business Calls data require a private locations/<id> resource and user OAuth access.",
            ]
        return result

    @staticmethod
    def _performance_series(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Normalize the Performance API's nested time-series response."""
        metrics: dict[str, dict[str, Any]] = {}
        groups = data.get("multiDailyMetricTimeSeries", data.get("multi_daily_metric_time_series", []))
        for group in groups or []:
            entries = group.get("dailyMetricTimeSeries", group.get("daily_metric_time_series", []))
            for entry in entries or []:
                metric = entry.get("dailyMetric", entry.get("daily_metric"))
                if not metric:
                    continue
                time_series = entry.get("timeSeries", entry.get("time_series", {})) or {}
                dated_values = time_series.get("datedValues", time_series.get("dated_values", [])) or []
                series = []
                total = 0
                for point in dated_values:
                    point_date = point.get("date", {}) or {}
                    date_value = "-".join(
                        str(point_date.get(part, "")).zfill(width)
                        for part, width in (("year", 4), ("month", 2), ("day", 2))
                    )
                    raw_value = point.get("value")
                    # The API omits value for a zero daily observation.
                    value = int(raw_value) if raw_value is not None else 0
                    total += value
                    series.append({"date": date_value, "value": value})
                metrics[metric] = {
                    "total": total,
                    "series": series,
                    "daily_sub_entity_type": entry.get("dailySubEntityType", entry.get("daily_sub_entity_type")),
                }
        return metrics

    def fetch_performance_metrics(
        self,
        start_date: str,
        end_date: str,
        metrics: tuple[str, ...] = GBP_DAILY_METRICS,
    ) -> Dict[str, Any]:
        """Fetch daily private GBP performance metrics for one location."""
        result: Dict[str, Any] = {
            "status": SourceAvailability.UNAVAILABLE.value,
            "start_date": start_date,
            "end_date": end_date,
            "requested_metrics": list(metrics),
            "metrics": {},
            "reason": "",
        }
        private_location = self._private_location_resource()
        if not private_location:
            result["reason"] = "Private GBP performance metrics require a locations/<id> resource."
            return result

        token = self.get_token()
        if not token:
            result["reason"] = "Google Business Profile credentials are unavailable."
            return result

        start_year, start_month, start_day = self._date_parts(start_date)
        end_year, end_month, end_day = self._date_parts(end_date)
        query: list[tuple[str, str]] = [("dailyMetrics", metric) for metric in metrics]
        query.extend([
            ("daily_range.start_date.year", start_year),
            ("daily_range.start_date.month", start_month),
            ("daily_range.start_date.day", start_day),
            ("daily_range.end_date.year", end_year),
            ("daily_range.end_date.month", end_month),
            ("daily_range.end_date.day", end_day),
        ])
        url = self._query_url(
            f"{GBP_PERFORMANCE_BASE_URL}/{private_location}:fetchMultiDailyMetricsTimeSeries",
            query,
        )
        try:
            response = self.requester(url, token)
        except Exception as exc:
            result["status"] = self._status_from_exception(exc).value
            result["reason"] = self._exception_reason("GBP Performance API request failed", exc)
            return result

        normalized = self._performance_series(response)
        result["metrics"] = normalized
        result["status"] = SourceAvailability.AVAILABLE.value if normalized else SourceAvailability.EMPTY.value
        result["reason"] = (
            "GBP Performance API returned daily metrics."
            if normalized
            else "GBP Performance API returned no metric series for the requested period."
        )
        return result

    def fetch_search_keywords(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Fetch every monthly GBP search-keyword page covering the date range."""
        result: Dict[str, Any] = {
            "status": SourceAvailability.UNAVAILABLE.value,
            "start_date": start_date,
            "end_date": end_date,
            "monthly_search_keywords": [],
            "page_count": 0,
            "reason": "",
        }
        private_location = self._private_location_resource()
        if not private_location:
            result["reason"] = "GBP search keywords require a locations/<id> resource."
            return result
        token = self.get_token()
        if not token:
            result["reason"] = "Google Business Profile credentials are unavailable."
            return result

        start_year, start_month = self._month_parts(start_date)
        end_year, end_month = self._month_parts(end_date)
        base_url = f"{GBP_PERFORMANCE_BASE_URL}/{private_location}/searchkeywords/impressions/monthly"
        page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        rows: list[dict[str, Any]] = []
        try:
            while True:
                query = [
                    ("monthly_range.start_month.year", start_year),
                    ("monthly_range.start_month.month", start_month),
                    ("monthly_range.end_month.year", end_year),
                    ("monthly_range.end_month.month", end_month),
                    ("pageSize", "100"),
                ]
                if page_token:
                    if page_token in seen_tokens:
                        raise RuntimeError("GBP search keyword pagination returned a repeated page token")
                    seen_tokens.add(page_token)
                    query.append(("pageToken", page_token))
                response = self.requester(self._query_url(base_url, query), token)
                result["page_count"] += 1
                for item in response.get("searchKeywordsCounts", response.get("search_keywords_counts", [])) or []:
                    insights = item.get("insightsValue", item.get("insights_value", {})) or {}
                    value = self._int_or_none(insights.get("value"))
                    threshold = self._int_or_none(insights.get("threshold"))
                    rows.append({
                        "search_keyword": item.get("searchKeyword", item.get("search_keyword")),
                        "insights_value": value,
                        "insights_threshold": threshold,
                        "insights_value_type": "value" if value is not None else "threshold" if threshold is not None else "unknown",
                    })
                page_token = response.get("nextPageToken", response.get("next_page_token"))
                if not page_token:
                    break
        except Exception as exc:
            result["status"] = self._status_from_exception(exc, {403, 404}).value
            result["reason"] = self._exception_reason("GBP search keyword request failed", exc)
            result["monthly_search_keywords"] = rows
            return result

        rows = [row for row in rows if row.get("search_keyword")]
        rows.sort(
            key=lambda row: (
                row.get("insights_value") is not None,
                row.get("insights_value") if row.get("insights_value") is not None else row.get("insights_threshold") or 0,
            ),
            reverse=True,
        )
        result["monthly_search_keywords"] = rows
        result["status"] = SourceAvailability.AVAILABLE.value if rows else SourceAvailability.EMPTY.value
        result["reason"] = (
            "GBP Performance API returned monthly search keywords."
            if rows
            else "GBP Performance API returned no monthly search keywords for the requested months."
        )
        return result

    def fetch_reviews(self) -> Dict[str, Any]:
        """Fetch the complete managed review inventory through all pages."""
        result: Dict[str, Any] = {
            "status": SourceAvailability.UNAVAILABLE.value,
            "reviews": [],
            "average_rating": None,
            "total_review_count": None,
            "page_count": 0,
            "review_inventory_complete": False,
            "review_response_summary": {},
            "reason": "",
        }
        private_location = self._private_location_resource()
        account_name = self._private_account_resource()
        if not private_location:
            result["reason"] = "Managed GBP reviews require a locations/<id> resource."
            return result
        if not account_name:
            result["reason"] = "Managed GBP reviews require the GBP accounts/{account_id} resource name."
            return result
        token = self.get_token()
        if not token:
            result["reason"] = "Google Business Profile credentials are unavailable."
            return result

        base_url = f"{GBP_REVIEWS_BASE_URL}/{account_name}/{private_location}/reviews"
        page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        reviews: list[dict[str, Any]] = []
        total_review_count: Optional[int] = None
        average_rating: Any = None
        try:
            while True:
                query = [("pageSize", "50"), ("orderBy", "updateTime desc")]
                if page_token:
                    if page_token in seen_tokens:
                        raise RuntimeError("GBP review pagination returned a repeated page token")
                    seen_tokens.add(page_token)
                    query.append(("pageToken", page_token))
                response = self.requester(self._query_url(base_url, query), token)
                result["page_count"] += 1
                if total_review_count is None:
                    total_review_count = self._int_or_none(response.get("totalReviewCount", response.get("total_review_count")))
                if average_rating is None:
                    average_rating = response.get("averageRating", response.get("average_rating"))
                for review in response.get("reviews", []) or []:
                    reviews.append(self._normalize_managed_review(review))
                page_token = response.get("nextPageToken", response.get("next_page_token"))
                if not page_token:
                    break
        except Exception as exc:
            result["status"] = self._status_from_exception(exc, {403, 404}).value
            result["reason"] = self._exception_reason("GBP Reviews API request failed", exc)
            result["reviews"] = reviews
            result["total_review_count"] = total_review_count
            result["average_rating"] = average_rating
            result["review_response_summary"] = self._review_response_summary(reviews, total_review_count)
            return result

        complete = total_review_count is not None and len(reviews) == total_review_count
        result["reviews"] = reviews
        result["total_review_count"] = total_review_count
        result["average_rating"] = average_rating
        result["review_inventory_complete"] = complete
        result["review_response_summary"] = self._review_response_summary(reviews, total_review_count)
        result["status"] = (
            SourceAvailability.AVAILABLE.value
            if complete or reviews
            else SourceAvailability.EMPTY.value
        )
        result["reason"] = (
            "GBP Reviews API returned the complete paginated review inventory."
            if complete
            else "GBP Reviews API returned no reviews for the verified location."
            if total_review_count == 0
            else "GBP Reviews API returned reviews, but the inventory completeness could not be confirmed."
        )
        return result

    def fetch_business_calls_settings(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "status": SourceAvailability.UNAVAILABLE.value,
            "settings": {},
            "reason": "",
        }
        private_location = self._private_location_resource()
        if not private_location:
            result["reason"] = "Business Calls requires a locations/<id> resource."
            return result
        token = self.get_token()
        if not token:
            result["reason"] = "Google Business Profile credentials are unavailable."
            return result
        url = f"{GBP_BUSINESS_CALLS_BASE_URL}/{private_location}/businesscallssettings"
        try:
            response = self.requester(url, token)
        except Exception as exc:
            result["status"] = self._status_from_exception(exc, {403, 404}).value
            result["reason"] = self._exception_reason("GBP Business Calls settings request failed", exc)
            return result
        result["settings"] = {
            "name": response.get("name"),
            "calls_state": response.get("callsState", response.get("calls_state")),
            "consent_time": response.get("consentTime", response.get("consent_time")),
        }
        result["status"] = SourceAvailability.AVAILABLE.value
        return result

    def fetch_business_calls_insights(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Fetch answered/missed call insights when Business Calls is enabled."""
        result: Dict[str, Any] = {
            "status": SourceAvailability.UNAVAILABLE.value,
            "start_date": start_date,
            "end_date": end_date,
            "settings": {},
            "insights": [],
            "page_count": 0,
            "aggregate_metrics": {},
            "answered_calls": None,
            "missed_calls": None,
            "reason": "",
        }
        private_location = self._private_location_resource()
        if not private_location:
            result["reason"] = "Business Calls insights require a locations/<id> resource."
            return result
        settings_result = self.fetch_business_calls_settings()
        result["settings"] = settings_result.get("settings", {})
        settings_state = result["settings"].get("calls_state")
        if settings_state and settings_state != "ENABLED":
            result["reason"] = f"Business Calls is {settings_state.lower().replace('_', ' ')} for this location."
            return result
        if settings_result.get("status") != SourceAvailability.AVAILABLE.value:
            result["reason"] = settings_result.get("reason", "Business Calls settings are unavailable.")
            return result

        token = self.get_token()
        if not token:
            result["reason"] = "Google Business Profile credentials are unavailable."
            return result
        base_url = f"{GBP_BUSINESS_CALLS_BASE_URL}/{private_location}/businesscallsinsights"
        filter_value = f'startDate="{start_date}" AND endDate="{end_date}" AND metricType=AGGREGATE_COUNT'
        page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        entries: list[dict[str, Any]] = []
        try:
            while True:
                query = [("filter", filter_value), ("pageSize", "20")]
                if page_token:
                    if page_token in seen_tokens:
                        raise RuntimeError("GBP Business Calls pagination returned a repeated page token")
                    seen_tokens.add(page_token)
                    query.append(("pageToken", page_token))
                response = self.requester(self._query_url(base_url, query), token)
                result["page_count"] += 1
                entries.extend(response.get("businessCallsInsights", response.get("business_calls_insights", [])) or [])
                page_token = response.get("nextPageToken", response.get("next_page_token"))
                if not page_token:
                    break
        except Exception as exc:
            result["status"] = self._status_from_exception(exc, {403, 404}).value
            result["reason"] = self._exception_reason("GBP Business Calls insights request failed", exc)
            return result

        result["insights"] = entries
        if not entries:
            result["status"] = SourceAvailability.EMPTY.value
            result["reason"] = "GBP Business Calls returned no insights for the requested period."
            return result

        aggregates = [
            entry.get("aggregateMetrics", entry.get("aggregate_metrics", {})) or {}
            for entry in entries
        ]

        def summed_count(camel_key: str, snake_key: str) -> Optional[int]:
            values = [
                self._int_or_none(aggregate.get(camel_key, aggregate.get(snake_key)))
                for aggregate in aggregates
            ]
            values = [value for value in values if value is not None]
            return sum(values) if values else None

        hourly_metrics = [
            metric
            for aggregate in aggregates
            for metric in (aggregate.get("hourlyMetrics", aggregate.get("hourly_metrics", [])) or [])
        ]
        weekday_metrics = [
            metric
            for aggregate in aggregates
            for metric in (aggregate.get("weekdayMetrics", aggregate.get("weekday_metrics", [])) or [])
        ]
        normalized_aggregate = {
            "missed_calls": summed_count("missedCallsCount", "missed_calls_count"),
            "answered_calls": summed_count("answeredCallsCount", "answered_calls_count"),
            "hourly_metrics": hourly_metrics,
            "weekday_metrics": weekday_metrics,
            "start_date": aggregates[0].get("startDate", aggregates[0].get("start_date")),
            "end_date": aggregates[0].get("endDate", aggregates[0].get("end_date")),
            "metric_type": entries[0].get("metricType", entries[0].get("metric_type")),
        }
        result["aggregate_metrics"] = normalized_aggregate
        result["answered_calls"] = normalized_aggregate.get("answered_calls")
        result["missed_calls"] = normalized_aggregate.get("missed_calls")
        result["status"] = SourceAvailability.AVAILABLE.value
        result["reason"] = "GBP Business Calls returned aggregate call insights."
        return result

    def fetch_periodic_insights(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Fetch date-bounded GBP data without repeating static profile/review calls."""
        result: Dict[str, Any] = {
            "source": self.source_name,
            "status": SourceAvailability.UNAVAILABLE.value,
            "start_date": start_date,
            "end_date": end_date,
            "location_id": self.location_id,
            "private_location_id": self._private_location_resource() or None,
            "performance_metrics": {},
            "available_performance_metrics": [],
            "performance_status": SourceAvailability.UNAVAILABLE.value,
            "monthly_search_keywords": [],
            "search_keywords_status": SourceAvailability.UNAVAILABLE.value,
            "business_calls": {},
            "business_calls_status": SourceAvailability.UNAVAILABLE.value,
            "answered_calls": None,
            "missed_calls": None,
            "limitations": [],
            "reason": "",
        }
        performance = self.fetch_performance_metrics(start_date, end_date)
        keywords = self.fetch_search_keywords(start_date, end_date)
        calls = self.fetch_business_calls_insights(start_date, end_date)
        result.update({
            "performance_metrics": performance.get("metrics", {}),
            "available_performance_metrics": sorted(performance.get("metrics", {})),
            "performance_status": performance.get("status"),
            "monthly_search_keywords": keywords.get("monthly_search_keywords", []),
            "search_keywords_status": keywords.get("status"),
            "business_calls": calls,
            "business_calls_status": calls.get("status"),
            "answered_calls": calls.get("answered_calls"),
            "missed_calls": calls.get("missed_calls"),
        })
        result.update(self._action_fields_from_performance(result["performance_metrics"]))
        sub_results = [performance, keywords, calls]
        statuses = [str(item.get("status")) for item in sub_results]
        if SourceAvailability.AVAILABLE.value in statuses:
            result["status"] = SourceAvailability.AVAILABLE.value
        elif SourceAvailability.ERROR.value in statuses:
            result["status"] = SourceAvailability.ERROR.value
        elif SourceAvailability.EMPTY.value in statuses:
            result["status"] = SourceAvailability.EMPTY.value
        else:
            result["status"] = SourceAvailability.UNAVAILABLE.value
        result["limitations"] = [
            str(item.get("reason"))
            for item in sub_results
            if item.get("reason") and item.get("status") != SourceAvailability.AVAILABLE.value
        ]
        result["reason"] = "; ".join(result["limitations"])
        return result

    def _list_accounts(self, token: str) -> list[dict[str, Any]]:
        accounts: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        while True:
            # Account Management API accounts.list caps pageSize at 20.
            query: list[tuple[str, str]] = [("pageSize", "20")]
            if page_token:
                if page_token in seen_tokens:
                    raise RuntimeError("GBP account pagination returned a repeated page token")
                seen_tokens.add(page_token)
                query.append(("pageToken", page_token))
            response = self.requester(
                self._query_url(f"{GBP_ACCOUNT_MANAGEMENT_BASE_URL}/accounts", query),
                token,
            )
            accounts.extend(response.get("accounts", []) or [])
            page_token = response.get("nextPageToken", response.get("next_page_token"))
            if not page_token:
                return accounts

    def _list_account_locations(self, account_name: str, token: str) -> list[dict[str, Any]]:
        locations: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        while True:
            query: list[tuple[str, str]] = [
                ("readMask", "name,title,metadata"),
                ("pageSize", "100"),
            ]
            if page_token:
                if page_token in seen_tokens:
                    raise RuntimeError("GBP location pagination returned a repeated page token")
                seen_tokens.add(page_token)
                query.append(("pageToken", page_token))
            response = self.requester(
                self._query_url(f"{GBP_BUSINESS_INFORMATION_BASE_URL}/{account_name}/locations", query),
                token,
            )
            locations.extend(response.get("locations", []) or [])
            page_token = response.get("nextPageToken", response.get("next_page_token"))
            if not page_token:
                return locations

    @staticmethod
    def _titles_match(title1: str, title2: str) -> bool:
        def _norm(s: str) -> str:
            s = s.lower().strip()
            if s.startswith("the "):
                s = s[4:].strip()
            s = re.sub(r"[^\w\s]", "", s)
            return " ".join(s.split())

        n1, n2 = _norm(title1), _norm(title2)
        if not n1 or not n2:
            return False
        return n1 == n2 or n1 in n2 or n2 in n1

    def _resolve_private_location(self, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Resolve a public Place ID to a private account/location resource requiring triple validation."""
        result: Dict[str, Any] = {
            "status": SourceAvailability.UNAVAILABLE.value,
            "location_id": None,
            "account_id": None,
            "reason": "",
        }
        token = access_token or self.get_token()
        if not token:
            result["reason"] = "Google Business Profile credentials are unavailable."
            return result

        configured_location = self.location_id.strip()
        target_place_id = self.public_place_id or (
            configured_location.removeprefix("places/") if configured_location.startswith("places/") else None
        )
        target_private_locations = set(self.candidate_location_ids)
        if configured_location.startswith("locations/"):
            target_private_locations.add(configured_location)
        target_private_location = configured_location if configured_location.startswith("locations/") else None
        expected_title = self.business_title

        try:
            if self._private_account_resource() and target_private_location:
                accounts = [{"name": self._private_account_resource()}]
            else:
                accounts = self._list_accounts(token)
                if self._private_account_resource():
                    accounts = [
                        account for account in accounts
                        if account.get("name") == self._private_account_resource()
                    ] or [{"name": self._private_account_resource()}]
            for account in accounts:
                account_name = self._account_resource_name(account.get("name"))
                if not account_name:
                    continue
                locations = self._list_account_locations(account_name, token)
                for location in locations:
                    location_name = location.get("name")
                    metadata = location.get("metadata") or {}
                    place_id = metadata.get("placeId", metadata.get("place_id"))
                    location_title = str(
                        location.get("title")
                        or (location.get("displayName") if isinstance(location.get("displayName"), str) else (location.get("displayName") or {}).get("text", ""))
                        or ""
                    ).strip()

                    # Triple verification: require ALL available/configured identifiers to match
                    checks = []
                    if target_private_locations:
                        checks.append(location_name in target_private_locations)
                    if target_place_id:
                        checks.append(place_id == target_place_id)
                    if expected_title:
                        checks.append(self._titles_match(location_title, expected_title))

                    if checks and all(checks):
                        self._resolved_location_id = location_name
                        self._resolved_account_id = account_name
                        result.update({
                            "status": SourceAvailability.AVAILABLE.value,
                            "location_id": location_name,
                            "account_id": account_name,
                            "reason": "GBP private location was resolved from the managed account locations.",
                        })
                        return result
        except Exception as exc:
            result["status"] = self._status_from_exception(exc, {403, 404}).value
            result["reason"] = self._exception_reason("GBP private location resolution failed", exc)
            return result

        result["status"] = SourceAvailability.EMPTY.value
        result["reason"] = "No managed GBP location matched all configured identifiers (private location candidate, public Place ID, and business title)."
        return result

    resolve_private_location = _resolve_private_location

    def fetch_local_insights(
        self,
        start_date: str,
        end_date: str,
        strict: bool = False,
    ) -> Dict[str, Any]:
        """Fetch profile, periodic performance, keyword, review, and call data."""
        if not self.is_configured():
            return self._base_result(
                SourceAvailability.NOT_CONFIGURED,
                "Google Business Profile location is not configured.",
            )

        token = self.get_token()
        if not token:
            return self._base_result(
                SourceAvailability.UNAVAILABLE,
                "Google Business Profile credentials are unavailable.",
            )

        clean_location = self.location_id.strip()
        if not clean_location.startswith("locations/") or not self._private_account_resource():
            resolution = self.resolve_private_location(token)
            clean_location = resolution.get("location_id") or clean_location

        if not clean_location.startswith("locations/"):
            public_place_id = self.public_place_id or self.location_id.strip().removeprefix("places/")
            url = f"https://places.googleapis.com/v1/places/{public_place_id}"
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,reviews",
                    "Content-Type": "application/json",
                }
                request = urllib.request.Request(url, method="GET", headers=headers)
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.loads(response.read().decode())
                public_result = self._profile_result(data, profile_source="places")
                public_result["status"] = SourceAvailability.PARTIAL.value
                public_result["reason"] = "Public Places profile data is available; private GBP performance and managed-review data were not verified."
                return public_result
            except Exception as exc:
                return self._base_result(
                    SourceAvailability.ERROR,
                    f"Google Business Profile place request failed: {type(exc).__name__}.",
                )

        self._resolved_location_id = clean_location
        read_mask = urllib.parse.urlencode({"readMask": GBP_LOCATION_READ_MASK})
        url = f"{GBP_BUSINESS_INFORMATION_BASE_URL}/{clean_location}?{read_mask}"
        try:
            profile_response = self.requester(url, token)
        except Exception as exc:
            return self._base_result(
                SourceAvailability.ERROR,
                self._exception_reason("Google Business Profile location request failed", exc),
            )

        result = self._profile_result(profile_response, profile_source="private")
        result["location_id"] = self.location_id
        result["private_location_id"] = clean_location
        result["account_id"] = self._private_account_resource()

        periodic = self.fetch_periodic_insights(start_date, end_date)
        result.update({
            "performance_metrics": periodic.get("performance_metrics", {}),
            "available_performance_metrics": periodic.get("available_performance_metrics", []),
            "performance_status": periodic.get("performance_status"),
            "monthly_search_keywords": periodic.get("monthly_search_keywords", []),
            "search_keywords_status": periodic.get("search_keywords_status"),
            "business_calls": periodic.get("business_calls", {}),
            "business_calls_status": periodic.get("business_calls_status"),
            "answered_calls": periodic.get("answered_calls"),
            "missed_calls": periodic.get("missed_calls"),
        })
        result.update(self._action_fields_from_performance(result["performance_metrics"]))

        reviews = self.fetch_reviews()
        managed_reviews = reviews.get("reviews", [])
        result.update({
            "reviews": managed_reviews,
            "reviews_status": reviews.get("status"),
            "review_inventory_complete": reviews.get("review_inventory_complete", False),
            "review_response_summary": reviews.get("review_response_summary", {}),
        })
        if reviews.get("average_rating") is not None:
            result["average_rating"] = reviews.get("average_rating")
        if reviews.get("total_review_count") is not None:
            result["total_reviews_count"] = reviews.get("total_review_count")
        result["recent_review_snippets"] = [
            str(review.get("comment") or "")[:120].strip()
            for review in managed_reviews
            if review.get("comment")
        ][:3]

        result["capabilities"].update({
            "action_metrics": periodic.get("performance_status"),
            "search_keywords": periodic.get("search_keywords_status"),
            "reviews": reviews.get("status"),
            "business_calls": periodic.get("business_calls_status"),
        })
        result["limitations"] = [
            *periodic.get("limitations", []),
            reviews.get("reason", "") if reviews.get("status") != SourceAvailability.AVAILABLE.value else "",
        ]
        result["limitations"] = [item for item in result["limitations"] if item]
        result["reason"] = "GBP profile and requested sub-sources were queried."
        sub_statuses = [
            result.get("profile_status"),
            result.get("performance_status"),
            result.get("search_keywords_status"),
            result.get("reviews_status"),
            result.get("business_calls_status"),
        ]
        core_statuses = [
            result.get("profile_status"),
            result.get("performance_status"),
        ]
        if any(status == SourceAvailability.ERROR.value for status in sub_statuses):
            result["status"] = SourceAvailability.PARTIAL.value
        elif any(status == SourceAvailability.PARTIAL.value for status in sub_statuses):
            result["status"] = SourceAvailability.PARTIAL.value
        elif any(status in {
            SourceAvailability.PARTIAL.value,
            SourceAvailability.UNAVAILABLE.value,
            SourceAvailability.NOT_CONFIGURED.value,
            SourceAvailability.EMPTY.value,
        } for status in core_statuses) and any(status == SourceAvailability.AVAILABLE.value for status in core_statuses):
            result["status"] = SourceAvailability.PARTIAL.value
        return result
