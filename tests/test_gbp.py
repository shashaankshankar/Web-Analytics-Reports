from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.sources.gbp import GBP_SCOPES, GoogleBusinessProfileExtractor


def test_private_gbp_performance_metrics_are_normalized_and_mapped():
    requested_urls: list[str] = []

    def requester(url: str, token: str) -> dict:
        requested_urls.append(url)
        if ":fetchMultiDailyMetricsTimeSeries?" in url:
            return {
                "multiDailyMetricTimeSeries": [{
                    "dailyMetricTimeSeries": [
                        {
                            "dailyMetric": "CALL_CLICKS",
                            "timeSeries": {
                                "datedValues": [
                                    {"date": {"year": 2026, "month": 8, "day": 1}, "value": "2"},
                                    {"date": {"year": 2026, "month": 8, "day": 2}, "value": "3"},
                                ]
                            },
                        },
                        {
                            "dailyMetric": "BUSINESS_DIRECTION_REQUESTS",
                            "timeSeries": {
                                "datedValues": [
                                    {"date": {"year": 2026, "month": 8, "day": 1}},
                                ]
                            },
                        },
                        {
                            "dailyMetric": "WEBSITE_CLICKS",
                            "timeSeries": {"datedValues": []},
                        },
                    ]
                }]
            }
        return {
            "name": "locations/123456789",
            "title": "The House of Dental",
            "metadata": {"placeId": "ChIJexample"},
        }

    extractor = GoogleBusinessProfileExtractor(
        "locations/123456789",
        access_token="test-token",
        requester=requester,
    )
    result = extractor.fetch_local_insights("2026-08-01", "2026-08-02")

    assert result["status"] == "available"
    assert result["performance_status"] == "available"
    assert result["phone_calls"] == 5
    assert result["direction_requests"] == 0
    assert result["website_clicks"] == 0
    assert result["performance_metrics"]["CALL_CLICKS"]["series"][0]["date"] == "2026-08-01"
    assert any("readMask=name%2ClanguageCode%2CstoreCode%2Ctitle%2CphoneNumbers" in url for url in requested_urls)
    assert any("dailyMetrics=CALL_CLICKS" in url for url in requested_urls)


def test_private_performance_requires_private_location_resource():
    extractor = GoogleBusinessProfileExtractor("places/ChIJexample", access_token="test-token")

    result = extractor.fetch_performance_metrics("2026-08-01", "2026-08-02")

    assert result["status"] == "unavailable"
    assert result["metrics"] == {}
    assert "locations/<id>" in result["reason"]


def test_oauth_bundle_uses_business_manage_scope(monkeypatch):
    monkeypatch.setenv(
        "GBP_OAUTH_CREDENTIALS_JSON",
        '{"client_id":"client-id","client_secret":"client-secret","refresh_token":"refresh-token"}',
    )

    credentials = GoogleBusinessProfileExtractor._user_oauth_credentials()

    assert credentials is not None
    assert credentials.client_id == "client-id"
    assert credentials.refresh_token == "refresh-token"
    assert credentials.scopes == GBP_SCOPES


def test_gbp_does_not_fall_back_to_adc_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("GBP_OAUTH_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("GBP_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GBP_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GBP_OAUTH_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("GBP_ALLOW_ADC_FALLBACK", raising=False)

    extractor = GoogleBusinessProfileExtractor("locations/123")

    assert extractor.get_token() == ""


def test_private_gbp_profile_keywords_reviews_and_calls_are_normalized_and_paginated():
    requested_urls: list[str] = []

    def requester(url: str, token: str) -> dict:
        requested_urls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if ":fetchMultiDailyMetricsTimeSeries" in url:
            return {
                "multiDailyMetricTimeSeries": [{
                    "dailyMetricTimeSeries": [
                        {
                            "dailyMetric": "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
                            "timeSeries": {"datedValues": [{"date": {"year": 2026, "month": 8, "day": 1}, "value": "10"}]},
                        },
                        {
                            "dailyMetric": "BUSINESS_BOOKINGS",
                            "timeSeries": {"datedValues": [{"date": {"year": 2026, "month": 8, "day": 1}}]},
                        },
                    ]
                }]
            }
        if "/searchkeywords/impressions/monthly" in url:
            if query.get("pageToken") == ["next-keyword-page"]:
                return {"searchKeywordsCounts": [{"searchKeyword": "emergency dentist", "insightsValue": {"threshold": "5"}}]}
            return {
                "searchKeywordsCounts": [
                    {"searchKeyword": "dentist winter park", "insightsValue": {"value": "22"}},
                    {"searchKeyword": "cosmetic dentist", "insightsValue": {"threshold": "10"}},
                ],
                "nextPageToken": "next-keyword-page",
            }
        if "/businesscallssettings" in url:
            return {"name": "locations/123/businesscallssettings", "callsState": "ENABLED"}
        if "/businesscallsinsights" in url:
            return {
                "businessCallsInsights": [{
                    "metricType": "AGGREGATE_COUNT",
                    "aggregateMetrics": {
                        "answeredCallsCount": "7",
                        "missedCallsCount": "2",
                        "hourlyMetrics": [{"hour": 10, "answeredCallsCount": "3"}],
                        "weekdayMetrics": [{"weekday": "MONDAY", "answeredCallsCount": "4"}],
                        "startDate": "2026-08-01",
                        "endDate": "2026-08-02",
                    },
                }]
            }
        if "/reviews" in url:
            if query.get("pageToken") == ["next-review-page"]:
                return {
                    "reviews": [{
                        "name": "accounts/abc/locations/123/reviews/r2",
                        "reviewId": "r2",
                        "reviewer": {"displayName": "Second Patient"},
                        "starRating": "FOUR",
                        "comment": "A second review",
                        "createTime": "2026-07-02T00:00:00Z",
                        "updateTime": "2026-07-03T00:00:00Z",
                    }],
                }
            return {
                "averageRating": 4.6,
                "totalReviewCount": 2,
                "reviews": [{
                    "name": "accounts/abc/locations/123/reviews/r1",
                    "reviewId": "r1",
                    "reviewer": {"displayName": "First Patient"},
                    "starRating": "FIVE",
                    "comment": "A first review",
                    "createTime": "2026-07-01T00:00:00Z",
                    "updateTime": "2026-07-01T00:00:00Z",
                    "reviewReply": {
                        "comment": "Thank you",
                        "updateTime": "2026-07-01T12:00:00Z",
                        "reviewReplyState": "REPLIED",
                    },
                }],
                "nextPageToken": "next-review-page",
            }
        if "locations/123?" in url:
            return {
                "name": "locations/123",
                "languageCode": "en",
                "title": "The House of Dental",
                "phoneNumbers": {"primaryPhone": "+1 407 555 0100", "additionalPhones": ["+1 407 555 0101"]},
                "categories": {
                    "primaryCategory": {"name": "categories/dentist", "displayName": "Dentist"},
                    "additionalCategories": [{"name": "categories/cosmetic", "displayName": "Cosmetic Dentist"}],
                },
                "storefrontAddress": {
                    "addressLines": ["123 Main Street"],
                    "locality": "Winter Park",
                    "administrativeArea": "FL",
                    "postalCode": "32789",
                },
                "websiteUri": "https://example.com",
                "regularHours": {"periods": [{
                    "openDay": "MONDAY", "openTime": {"hours": 8, "minutes": 0},
                    "closeDay": "MONDAY", "closeTime": {"hours": 17, "minutes": 0},
                }]},
                "serviceItems": [{"structuredServiceItem": {"serviceTypeId": "teeth_whitening", "description": "Teeth whitening"}}],
                "profile": {"description": "Dental care description"},
                "metadata": {"placeId": "ChIJprivate", "mapsUri": "https://maps.google.com/example"},
            }
        raise AssertionError(f"Unexpected GBP URL: {url}")

    extractor = GoogleBusinessProfileExtractor(
        "locations/123",
        access_token="test-token",
        account_id="accounts/abc",
        requester=requester,
    )
    result = extractor.fetch_local_insights("2026-08-01", "2026-08-02")

    assert result["profile_summary"]["primary_phone"] == "+1 407 555 0100"
    assert result["profile_summary"]["address"]["locality"] == "Winter Park"
    assert result["profile_summary"]["regular_hours"]["periods"][0]["openDay"] == "MONDAY"
    assert result["profile_summary"]["primary_category"]["displayName"] == "Dentist"
    assert result["profile_summary"]["services"][0]["structuredServiceItem"]["description"] == "Teeth whitening"
    assert result["performance_metrics"]["BUSINESS_IMPRESSIONS_DESKTOP_MAPS"]["total"] == 10
    assert result["performance_metrics"]["BUSINESS_BOOKINGS"]["total"] == 0
    assert len(result["monthly_search_keywords"]) == 3
    assert result["monthly_search_keywords"][0]["insights_value_type"] == "value"
    assert any(item["insights_value_type"] == "threshold" for item in result["monthly_search_keywords"])
    assert result["reviews_status"] == "available"
    assert result["review_inventory_complete"] is True
    assert len(result["reviews"]) == 2
    assert result["reviews"][0]["reply_status"] == "REPLIED"
    assert result["reviews"][1]["reply_status"] == "NOT_REPLIED"
    assert result["review_response_summary"]["unreplied_count"] == 1
    assert result["review_response_summary"]["reply_coverage_percent"] == 50.0
    assert result["business_calls_status"] == "available"
    assert result["answered_calls"] == 7
    assert result["missed_calls"] == 2
    assert any("pageToken=next-keyword-page" in url for url in requested_urls)
    assert any("pageToken=next-review-page" in url for url in requested_urls)


def test_public_place_id_can_resolve_to_managed_private_location_and_account():
    requested_urls: list[str] = []

    def requester(url: str, token: str) -> dict:
        requested_urls.append(url)
        if url.startswith("https://mybusinessaccountmanagement.googleapis.com/v1/accounts"):
            return {"accounts": [{"name": "accounts/abc"}]}
        if url.startswith("https://mybusinessbusinessinformation.googleapis.com/v1/accounts/abc/locations"):
            return {"locations": [{"name": "locations/123", "metadata": {"placeId": "ChIJtarget"}}]}
        raise AssertionError(f"Unexpected resolver URL: {url}")

    extractor = GoogleBusinessProfileExtractor(
        "places/ChIJtarget",
        access_token="test-token",
        requester=requester,
    )
    resolved = extractor.resolve_private_location()

    assert resolved == {
        "status": "available",
        "location_id": "locations/123",
        "account_id": "accounts/abc",
        "reason": "GBP private location was resolved from the managed account locations.",
    }
    assert any("pageSize=20" in url for url in requested_urls if "accountmanagement" in url)
    assert any("readMask=name%2Ctitle%2Cmetadata" in url for url in requested_urls)
