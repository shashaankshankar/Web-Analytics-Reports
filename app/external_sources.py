from __future__ import annotations

import hashlib
import hmac
import re
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from urllib.parse import urlsplit


SOURCES=("google_ads","search_console","call_tracking","crm_booking")
DIRECT_IDENTIFIER_KEYS=frozenset({"name","email","phone","telephone","address","patient","dob","date_of_birth","recording","transcript"})
SAFE_ID=re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class SourceConnector(Protocol):
    source_type: str
    def validate_access(self) -> dict: ...
    def sync(self, start_date: date, end_date: date) -> list[dict]: ...
    def disable(self) -> None: ...


def _request_json(url: str, token: str, payload: dict, extra_headers: dict[str,str] | None = None) -> object:
    headers={"Authorization":f"Bearer {token}","Content-Type":"application/json",**(extra_headers or {})}
    request=urllib.request.Request(url,data=json.dumps(payload).encode(),method="POST",headers=headers)
    with urllib.request.urlopen(request,timeout=60) as response: return json.loads(response.read())


class GoogleAdsConnector:
    source_type="google_ads"
    api_version="v25"

    def __init__(self, access_token: str, developer_token: str, customer_id: str,
                 login_customer_id: str = "", requester=_request_json):
        self.access_token=access_token; self.developer_token=developer_token
        self.customer_id=safe_source_id(customer_id.replace("-",""))
        self.login_customer_id=safe_source_id(login_customer_id.replace("-","")) if login_customer_id else ""
        self.requester=requester; self._disabled=False

    def _headers(self) -> dict[str,str]:
        if self._disabled: raise RuntimeError("source_connection_disabled")
        if not self.access_token or not self.developer_token: raise RuntimeError("google_ads_credentials_required")
        value={"developer-token":self.developer_token}
        if self.login_customer_id: value["login-customer-id"]=self.login_customer_id
        return value

    def _query(self, query: str) -> list[dict]:
        url=f"https://googleads.googleapis.com/{self.api_version}/customers/{self.customer_id}/googleAds:searchStream"
        batches=self.requester(url,self.access_token,{"query":query},self._headers())
        return [row for batch in batches for row in batch.get("results",[])]

    def validate_access(self) -> dict:
        rows=self._query("SELECT customer.id, customer.currency_code FROM customer LIMIT 1")
        return {"status":"ok" if rows else "empty","customerId":self.customer_id}

    def sync(self, start_date: date, end_date: date) -> list[dict]:
        query=("SELECT segments.date,customer.currency_code,campaign.id,campaign.name,ad_group.id,"
               "metrics.cost_micros,metrics.clicks,metrics.impressions FROM ad_group "
               f"WHERE segments.date BETWEEN '{start_date.isoformat()}' AND '{end_date.isoformat()}' "
               "ORDER BY segments.date,campaign.id,ad_group.id")
        rows=[]
        for value in self._query(query):
            rows.append({"date":value["segments"]["date"],"campaignId":safe_source_id(str(value["campaign"]["id"])),
                         "campaignName":str(value["campaign"].get("name",""))[:500],
                         "adGroupId":safe_source_id(str(value.get("adGroup",{}).get("id","unassigned"))),
                         "costMicros":int(value.get("metrics",{}).get("costMicros",0)),
                         "clicks":int(value.get("metrics",{}).get("clicks",0)),
                         "impressions":int(value.get("metrics",{}).get("impressions",0)),
                         "currency":value["customer"]["currencyCode"]})
        return rows

    def disable(self) -> None:
        self._disabled=True; self.access_token=""; self.developer_token=""


class SearchConsoleConnector:
    source_type="search_console"

    def __init__(self, access_token: str, site_url: str, privacy_approved_queries: bool = False,
                 requester=_request_json, row_limit: int = 25000):
        if not site_url.startswith(("https://","sc-domain:")): raise ValueError("invalid_search_console_site")
        self.access_token=access_token; self.site_url=site_url; self.privacy_approved_queries=privacy_approved_queries
        self.requester=requester; self.row_limit=row_limit; self._disabled=False

    def _url(self) -> str:
        return f"https://searchconsole.googleapis.com/webmasters/v3/sites/{urllib.parse.quote(self.site_url,safe='')}/searchAnalytics/query"

    def validate_access(self) -> dict:
        if self._disabled: raise RuntimeError("source_connection_disabled")
        result=self.requester(self._url(),self.access_token,{"startDate":date.today().isoformat(),"endDate":date.today().isoformat(),"rowLimit":1},None)
        return {"status":"ok","siteUrl":self.site_url,"returnedRows":len(result.get("rows",[]))}

    def sync(self, start_date: date, end_date: date) -> list[dict]:
        if self._disabled: raise RuntimeError("source_connection_disabled")
        output=[]; start_row=0
        while True:
            result=self.requester(self._url(),self.access_token,{"startDate":start_date.isoformat(),"endDate":end_date.isoformat(),"dimensions":["date","query","page"],"rowLimit":self.row_limit,"startRow":start_row,"dataState":"final"},None)
            rows=result.get("rows",[])
            for value in rows:
                day,query,page=value["keys"]
                output.append({"date":day,"queryHash":hashlib.sha256(query.strip().lower().encode()).hexdigest(),
                               "queryText":query if self.privacy_approved_queries else None,
                               "pagePath":safe_page_path(page),"clicks":int(value.get("clicks",0)),
                               "impressions":int(value.get("impressions",0)),"ctr":float(value.get("ctr",0)),
                               "position":float(value.get("position",0)),"privacyApproved":self.privacy_approved_queries})
            if len(rows)<self.row_limit: break
            start_row += self.row_limit
        return output

    def disable(self) -> None:
        self._disabled=True; self.access_token=""


def assert_no_direct_identifiers(value: dict) -> None:
    found=DIRECT_IDENTIFIER_KEYS & {key.lower() for key in value}
    if found: raise ValueError("prohibited_direct_identifier_fields")
    for item in value.values():
        if isinstance(item,dict): assert_no_direct_identifiers(item)
        elif isinstance(item,list):
            for nested in item:
                if isinstance(nested,dict): assert_no_direct_identifiers(nested)


def safe_source_id(value: str) -> str:
    if not SAFE_ID.fullmatch(value): raise ValueError("unsafe_source_identifier")
    return value


def safe_page_path(value: str) -> str:
    parsed=urlsplit(value)
    path=parsed.path or "/"
    if parsed.query or parsed.fragment or not path.startswith("/"): raise ValueError("unsafe_page_path")
    return path


def opaque_record_hash(source_id: str, secret: str) -> str:
    if len(secret)<32: raise ValueError("outcome_hash_secret_required")
    safe_source_id(source_id)
    return hmac.new(secret.encode(),source_id.encode(),hashlib.sha256).hexdigest()


class FirstPartyOutcomeConnector:
    """Normalizes approved call/CRM outcomes without retaining source identifiers."""

    OUTCOMES=frozenset({"generated_lead","qualified_lead","booked_appointment","customer","call_answered","call_qualified","revenue"})

    def __init__(self, source_type: str, hash_secret: str, identity_policy_reference: str):
        if source_type not in {"call_tracking","crm_booking"}: raise ValueError("unsupported_first_party_source")
        if len(hash_secret)<32: raise ValueError("outcome_hash_secret_required")
        if not SAFE_ID.fullmatch(identity_policy_reference): raise ValueError("identity_policy_reference_required")
        self.source_type=source_type; self.hash_secret=hash_secret; self.identity_policy_reference=identity_policy_reference; self._disabled=False

    def validate_access(self) -> dict:
        if self._disabled: raise RuntimeError("source_connection_disabled")
        return {"status":"configuration_valid","source":self.source_type,"identityPolicyReference":self.identity_policy_reference}

    def sync(self, start_date: date, end_date: date) -> list[dict]:
        raise RuntimeError("provider_pull_not_configured")

    def normalize(self, record: dict) -> dict:
        if self._disabled: raise RuntimeError("source_connection_disabled")
        assert_no_direct_identifiers(record)
        allowed={"sourceRecordId","subjectKey","outcomeType","outcomeDate","revenueMinorUnits","currency","attribution"}
        if set(record)-allowed: raise ValueError("unapproved_outcome_fields")
        outcome=record.get("outcomeType")
        if outcome not in self.OUTCOMES: raise ValueError("unapproved_outcome_type")
        outcome_date=date.fromisoformat(record["outcomeDate"])
        revenue=record.get("revenueMinorUnits")
        if (outcome=="revenue") != (isinstance(revenue,int) and revenue>=0): raise ValueError("invalid_revenue_outcome")
        subject=record.get("subjectKey")
        if subject is not None and not re.fullmatch(r"[a-f0-9]{64}",subject): raise ValueError("subject_key_must_be_approved_hmac")
        attribution=record.get("attribution",{})
        if set(attribution)-{"channel","campaignId","source"}: raise ValueError("unapproved_attribution_fields")
        for value in attribution.values(): safe_source_id(str(value))
        return {"sourceRecordHash":opaque_record_hash(record["sourceRecordId"],self.hash_secret),"subjectKey":subject,
                "outcomeType":outcome,"outcomeDate":outcome_date.isoformat(),"revenueMinorUnits":revenue,
                "currency":record.get("currency"),"attribution":attribution,"identityPolicyReference":self.identity_policy_reference}

    def disable(self) -> None:
        self._disabled=True; self.hash_secret=""


@dataclass(frozen=True)
class ExternalSourceState:
    source: str
    status: str
    approval_status: str | None
    last_validated_at: datetime | None
    last_sync_at: datetime | None
    blocker: str | None
    last_attempt_at: datetime | None = None
    last_error_code: str | None = None

    def as_dict(self) -> dict:
        return {"source":self.source,"status":self.status,"approvalStatus":self.approval_status,
                "lastValidatedAt":self.last_validated_at.isoformat() if self.last_validated_at else None,
                "lastSyncAt":self.last_sync_at.isoformat() if self.last_sync_at else None,"blocker":self.blocker,
                "lastAttemptAt":self.last_attempt_at.isoformat() if self.last_attempt_at else None,
                "lastErrorCode":self.last_error_code}
