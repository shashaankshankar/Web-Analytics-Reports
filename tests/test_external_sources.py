import pytest

from datetime import date

from app.external_sources import GoogleAdsConnector, SearchConsoleConnector, assert_no_direct_identifiers, opaque_record_hash, safe_page_path, safe_source_id


def test_external_source_normalization_rejects_identifiers_and_unsafe_urls():
    assert_no_direct_identifiers({"campaign_id":"123","clicks":5})
    with pytest.raises(ValueError,match="prohibited_direct_identifier"): assert_no_direct_identifiers({"email":"person@example.com"})
    assert safe_page_path("/contact") == "/contact"
    with pytest.raises(ValueError,match="unsafe_page_path"): safe_page_path("/contact?patient=1")
    with pytest.raises(ValueError,match="unsafe_source_identifier"): safe_source_id("not allowed / identifier")


def test_first_party_record_ids_are_one_way_hmac_values():
    value=opaque_record_hash("crm-record-123","s"*32)
    assert len(value)==64 and "crm-record-123" not in value
    with pytest.raises(ValueError,match="secret_required"): opaque_record_hash("crm-record-123","short")


def test_google_ads_v25_connector_normalizes_cost_clicks_campaign_and_ad_group():
    captured={}
    def request(url,token,payload,headers):
        captured.update(url=url,query=payload["query"],headers=headers)
        return [{"results":[{"segments":{"date":"2026-08-01"},"customer":{"currencyCode":"USD"},"campaign":{"id":"123","name":"Search"},"adGroup":{"id":"456"},"metrics":{"costMicros":"1250000","clicks":"3","impressions":"30"}}]}]
    connector=GoogleAdsConnector("access","developer","123-456-7890","111-222-3333",request)
    rows=connector.sync(date(2026,8,1),date(2026,8,2))
    assert "/v25/" in captured["url"] and "segments.date BETWEEN '2026-08-01' AND '2026-08-02'" in captured["query"]
    assert captured["headers"]["login-customer-id"]=="1112223333"
    assert rows[0]["costMicros"]==1250000 and rows[0]["campaignId"]=="123" and rows[0]["adGroupId"]=="456"


def test_search_console_connector_paginates_and_hides_queries_by_default():
    calls=[]
    def request(url,token,payload,headers):
        calls.append(payload)
        if payload["startRow"]==0: return {"rows":[{"keys":["2026-08-01","dentist near me","https://example.com/contact"],"clicks":2,"impressions":10,"ctr":.2,"position":3.5}]}
        return {"rows":[]}
    connector=SearchConsoleConnector("access","https://example.com",False,request,row_limit=1)
    rows=connector.sync(date(2026,8,1),date(2026,8,1))
    assert len(calls)==2 and calls[1]["startRow"]==1 and calls[0]["dataState"]=="final"
    assert rows[0]["queryText"] is None and len(rows[0]["queryHash"])==64 and rows[0]["pagePath"]=="/contact"
