from __future__ import annotations
import re
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import CheckCompatibilityRequest, DateRange, Dimension, Metric, RunReportRequest
from google.protobuf.json_format import MessageToDict
from .config import Site

PERIODS = {"7d", "28d", "90d", "this_month", "last_month"}
EVENT_FIELDS = (("generated_leads", "generate_lead"), ("appointment_requests", "appointment_request"), ("form_intent", "form_start"), ("technical_submissions", "form_submit"), ("phone_intent", "phone_click"), ("email_intent", "email_click"), ("cta_engagement", "cta_click"))
ACCESS_PROBE_TIMEOUT_SECONDS = 5.0
ACCESS_VERIFICATION_TTL_SECONDS = 300
ACCESS_ERROR_RETRY_SECONDS = 60


def period_dates(period, today=None):
    if period not in PERIODS: raise ValueError("unsupported_period")
    end = (today or date.today()) - timedelta(days=1)
    if period == "this_month": return end.replace(day=1).isoformat(), end.isoformat()
    if period == "last_month":
        end = end.replace(day=1) - timedelta(days=1); return end.replace(day=1).isoformat(), end.isoformat()
    return (end - timedelta(days=int(period[:-1])-1)).isoformat(), end.isoformat()


def previous_dates(period, today=None):
    start, end = (date.fromisoformat(value) for value in period_dates(period, today))
    days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    return (previous_end - timedelta(days=days - 1)).isoformat(), previous_end.isoformat()


def freshness_for(end_date: str, today=None):
    age = ((today or date.today()) - date.fromisoformat(end_date)).days
    if age <= 0: return "realtime"
    if age == 1: return "provisional"
    if age <= 14: return "reconciling"
    return "stable"


def safe_landing_page(value: str) -> str:
    """Drop query/fragment data and redact identifier-like paths before storage."""
    parsed = urlsplit(value if "://" in value else f"https://placeholder.invalid{value}")
    path = parsed.path or "/"
    if any(re.fullmatch(r"\d{4,}|[0-9a-f-]{27,}", part, re.I) for part in path.split("/")):
        return "[redacted_identifier_path]"
    return path[:500]


def _message(value):
    if value is None: return {}
    raw = getattr(value, "_pb", value)
    try: return MessageToDict(raw, preserving_proto_field_name=True)
    except Exception: return {}


def _utc_now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    if value is None: return None
    if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalise_clock(value):
    if value.tzinfo is None: return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _access_error_code(error):
    """Return a bounded provider-independent code without exposing upstream text."""
    name = type(error).__name__.lower()
    detail = str(error).lower()
    if any(token in name or token in detail for token in ("permissiondenied", "permission_denied", "forbidden", "access denied")):
        return "access_denied"
    if any(token in name or token in detail for token in ("unauthenticated", "invalid_grant", "authentication")):
        return "authentication_failed"
    if any(token in name or token in detail for token in ("resourceexhausted", "quota", "rate limit", "ratelimit")):
        return "quota_exceeded"
    if any(token in name or token in detail for token in ("deadline", "timeout", "temporarily", "unavailable")):
        return "provider_unavailable"
    return "verification_failed"


def assignment_health(site: Site, runtime_property_id: str = "", runtime_stream_id: str = "") -> dict:
    """Describe configured assignment separately from any live access evidence."""
    property_id = str(getattr(site, "property_id", "") or "")
    stream_id = str(getattr(site, "stream_id", "") or "")
    measurement_id = str(getattr(site, "measurement_id", "") or "")
    configured = bool(property_id and stream_id and measurement_id)
    runtime_mismatch = bool(
        configured and ((runtime_property_id and str(runtime_property_id) != property_id)
                        or (runtime_stream_id and str(runtime_stream_id) != stream_id))
    )
    state = "mismatch" if runtime_mismatch else ("configured" if configured else "unconfigured")
    return {
        "state": state,
        "property": f"properties/{property_id}" if property_id else None,
        "stream": f"properties/{property_id}/dataStreams/{stream_id}" if property_id and stream_id else None,
        "measurementId": measurement_id or None,
        "detail": "runtime assignment does not match the configured site assignment" if runtime_mismatch else (
            "property, web stream, and Measurement ID are configured" if configured else
            "property, web stream, and Measurement ID are required"
        ),
    }


def unverified_access_health(reason: str = "access_has_not_been_verified") -> dict:
    return {
        "state": "unverified",
        "checkedAt": None,
        "lastVerifiedAt": None,
        "expiresAt": None,
        "errorCode": None,
        "reason": reason,
        "probe": None,
        "cache": {"hit": False, "ttlSeconds": ACCESS_VERIFICATION_TTL_SECONDS, "ageSeconds": None},
    }


def access_error_health(error, checked_at=None) -> dict:
    checked_at = _normalise_clock(checked_at or _utc_now())
    return {
        "state": "error",
        "checkedAt": _timestamp(checked_at),
        "lastVerifiedAt": None,
        "expiresAt": None,
        "errorCode": _access_error_code(error),
        "reason": "bounded_ga4_access_probe_failed",
        "probe": None,
        "cache": {"hit": False, "ttlSeconds": ACCESS_VERIFICATION_TTL_SECONDS, "ageSeconds": 0},
    }


class GA4AccessVerifier:
    """Cache bounded access evidence so health reads do not become GA4 traffic."""

    def __init__(self, reporter, ttl_seconds: int = ACCESS_VERIFICATION_TTL_SECONDS,
                 error_retry_seconds: int = ACCESS_ERROR_RETRY_SECONDS, clock=None):
        self.reporter = reporter
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.error_retry_seconds = max(1, int(error_retry_seconds))
        self.clock = clock or _utc_now
        self._cached = None
        self._last_attempt_at = None
        self._last_verified_at = None
        self._lock = Lock()

    def _now(self):
        return _normalise_clock(self.clock())

    def _cached_result(self, now, hit=True, state=None):
        result = dict(self._cached)
        if state: result["state"] = state
        age_source = self._last_verified_at or self._last_attempt_at
        age = max(0, int((now - age_source).total_seconds())) if age_source else None
        result["cache"] = {"hit": hit, "ttlSeconds": self.ttl_seconds, "ageSeconds": age}
        return result

    def _unverified(self, reason, now, hit=False):
        result = unverified_access_health(reason)
        result["cache"] = {"hit": hit, "ttlSeconds": self.ttl_seconds, "ageSeconds": None}
        return result

    def check(self, refresh: bool = True) -> dict:
        with self._lock:
            now = self._now()
            if self._cached:
                if self._cached["state"] == "verified":
                    age = (now - self._last_verified_at).total_seconds()
                    if age <= self.ttl_seconds:
                        return self._cached_result(now)
                    if not refresh:
                        result = self._cached_result(now, state="stale")
                        result["reason"] = "ga4_access_verification_stale"
                        return result
                elif self._cached["state"] == "error":
                    attempt_age = (now - self._last_attempt_at).total_seconds() if self._last_attempt_at else self.error_retry_seconds
                    if not refresh or attempt_age < self.error_retry_seconds:
                        return self._cached_result(now)
                elif not refresh:
                    return self._cached_result(now)

            if not refresh:
                return self._unverified("access_has_not_been_verified", now)

            probe = getattr(self.reporter, "verify_access", None)
            if not callable(probe):
                self._cached = self._unverified("bounded_access_verification_unavailable", now)
                self._last_attempt_at = now
                return self._cached_result(now, hit=False)

            self._last_attempt_at = now
            try:
                evidence = probe(timeout=ACCESS_PROBE_TIMEOUT_SECONDS)
            except Exception as error:
                result = access_error_health(error, now)
                result["lastVerifiedAt"] = _timestamp(self._last_verified_at)
                self._cached = result
                return self._cached_result(now, hit=False)

            verified_at = now
            result = {
                "state": "verified",
                "checkedAt": _timestamp(now),
                "lastVerifiedAt": _timestamp(verified_at),
                "expiresAt": _timestamp(now + timedelta(seconds=self.ttl_seconds)),
                "errorCode": None,
                "reason": "bounded_ga4_access_probe_succeeded",
                "probe": evidence if isinstance(evidence, dict) else {"result": "succeeded"},
                "cache": {"hit": False, "ttlSeconds": self.ttl_seconds, "ageSeconds": 0},
            }
            self._last_verified_at = verified_at
            self._cached = result
            return result

    def status(self, refresh: bool = True) -> dict:
        return self.check(refresh=refresh)


class GA4Reporter:
    def __init__(self, site: Site, client=None): self.site, self.client = site, client or BetaAnalyticsDataClient()
    def today(self): return datetime.now(ZoneInfo(self.site.property_timezone or self.site.business_timezone)).date()
    def verify_access(self, timeout: float = ACCESS_PROBE_TIMEOUT_SECONDS):
        """Make one small, read-only Data API request proving assigned-property access."""
        if not self.site.property_id: raise RuntimeError("ga4_property_not_configured")
        probe_date = (self.today() - timedelta(days=1)).isoformat()
        request = RunReportRequest(
            property=f"properties/{self.site.property_id}",
            date_ranges=[DateRange(start_date=probe_date, end_date=probe_date)],
            metrics=[Metric(name="activeUsers")],
            limit=1,
            return_property_quota=True,
        )
        response = self.client.run_report(request=request, retry=None, timeout=timeout)
        return {
            "property": request.property,
            "dateRange": {"start": probe_date, "end": probe_date},
            "metric": "activeUsers",
            "rowCount": int(getattr(response, "row_count", 0) or 0),
        }

    def report_range(self, start, end, dimensions, metrics):
        request = RunReportRequest(property=f"properties/{self.site.property_id}", date_ranges=[DateRange(start_date=start, end_date=end)], dimensions=[Dimension(name=x) for x in dimensions], metrics=[Metric(name=x) for x in metrics], return_property_quota=True)
        response = self.client.run_report(request=request)
        metadata = response.metadata
        return {
            "rows":[{"dimensions":[x.value for x in row.dimension_values], "metrics":[x.value for x in row.metric_values]} for row in response.rows],
            "row_count":response.row_count,
            "property_timezone":metadata.time_zone or self.site.property_timezone,
            "currency_code":metadata.currency_code or None,
            "data_loss_from_other_row":bool(metadata.data_loss_from_other_row),
            "empty_reason":str(metadata.empty_reason) if getattr(metadata, "empty_reason", None) else ("no_rows" if response.row_count == 0 else None),
            "schema_restriction":_message(getattr(metadata, "schema_restriction_response", None)),
            "subject_to_thresholding":bool(getattr(metadata, "subject_to_thresholding", False)),
            "sampling_metadata":_message(getattr(metadata, "sampling_metadatas", None)),
            "property_quota":_message(getattr(response, "property_quota", None)),
            "date_range":{"start":start,"end":end},
            "request":{"property":request.property,"dateRanges":[{"start":start,"end":end}],"dimensions":list(dimensions),"metrics":list(metrics),"returnPropertyQuota":True},
        }

    def report(self, period, dimensions, metrics):
        return self.report_range(*period_dates(period,self.today()), dimensions, metrics)

    def check_compatibility(self, dimensions, metrics):
        response = self.client.check_compatibility(request=CheckCompatibilityRequest(
            property=f"properties/{self.site.property_id}",
            dimensions=[Dimension(name=value) for value in dimensions],
            metrics=[Metric(name=value) for value in metrics],
        ))
        requested_dimensions=set(dimensions)
        requested_metrics=set(metrics)
        incompatible_dimensions = [item.dimension_metadata.api_name for item in response.dimension_compatibilities if item.dimension_metadata.api_name in requested_dimensions and item.compatibility.name != "COMPATIBLE"]
        incompatible_metrics = [item.metric_metadata.api_name for item in response.metric_compatibilities if item.metric_metadata.api_name in requested_metrics and item.compatibility.name != "COMPATIBLE"]
        return {"compatible":not incompatible_dimensions and not incompatible_metrics,"incompatibleDimensions":incompatible_dimensions,"incompatibleMetrics":incompatible_metrics}
    @staticmethod
    def num(value): return int(value or "0")
    def overview(self, period):
        start, end = period_dates(period,self.today()); pstart, pend = previous_dates(period,self.today())
        summary, events = self.report_range(start,end, [], ["activeUsers", "sessions", "eventCount"]), self.report_range(start,end, ["eventName"], ["eventCount"])
        prior_summary, prior_events = self.report_range(pstart,pend, [], ["activeUsers", "sessions", "eventCount"]), self.report_range(pstart,pend, ["eventName"], ["eventCount"])
        counts = {r["dimensions"][0]:self.num(r["metrics"][0]) for r in events["rows"]}; values = summary["rows"][0]["metrics"] if summary["rows"] else ["0", "0", "0"]; sessions = self.num(values[1])
        prior_counts = {r["dimensions"][0]:self.num(r["metrics"][0]) for r in prior_events["rows"]}; prior_values = prior_summary["rows"][0]["metrics"] if prior_summary["rows"] else ["0","0","0"]; prior_sessions = self.num(prior_values[1])
        def metric(slug,value,previous,aggregation,warnings=None):
            return {"metric":slug,"definitionVersion":1,"value":value,"previousValue":previous,"period":{"start":start,"end":end},"comparisonPeriod":{"start":pstart,"end":pend},"source":"ga4_reporting_api","aggregation":aggregation,"warnings":warnings or []}
        metrics = [metric(field,counts.get(event,0),prior_counts.get(event,0),"SUM") for field,event in EVENT_FIELDS]
        metrics += [metric("active_users",self.num(values[0]),self.num(prior_values[0]),"WINDOWED_UNIQUE"),metric("sessions",sessions,prior_sessions,"SNAPSHOT"),metric("appointment_request_rate",counts.get("appointment_request",0)/sessions if sessions else None,prior_counts.get("appointment_request",0)/prior_sessions if prior_sessions else None,"RATIO",["denominator_zero"] if not sessions else [])]
        empty = not summary["rows"] and not events["rows"]
        return {"websiteId":self.site.site_id,"period":period,"property":f"properties/{self.site.property_id}","stream":f"properties/{self.site.property_id}/dataStreams/{self.site.stream_id}","measurementId":self.site.measurement_id,"propertyTimezone":summary["property_timezone"],"dataStatus":"empty_complete" if empty else "complete","metrics":metrics,"api":{"summaryRows":len(summary["rows"]),"eventRows":len(events["rows"]),"rowCount":summary["row_count"]+events["row_count"],"dataLossFromOtherRow":summary["data_loss_from_other_row"] or events["data_loss_from_other_row"],"subjectToThresholding":summary["subject_to_thresholding"] or events["subject_to_thresholding"],"emptyReason":"successful_complete_query_returned_no_rows" if empty else None},"_provenance":[summary,events,prior_summary,prior_events]}
    def acquisition(self, period):
        report = self.report(period,["sessionDefaultChannelGroup"],["sessions","activeUsers"])
        prior = self.report_range(*previous_dates(period,self.today()),["sessionDefaultChannelGroup"],["sessions","activeUsers"])
        prior_sessions={r["dimensions"][0] or "(not set)":self.num(r["metrics"][0]) for r in prior["rows"]}
        rows=[]
        for r in report["rows"]:
            channel=r["dimensions"][0] or "(not set)"; sessions=self.num(r["metrics"][0]); previous=prior_sessions.get(channel,0)
            rows.append({"channel":channel,"sessions":sessions,"activeUsers":self.num(r["metrics"][1]),"previousSessions":previous,"sessionChange":sessions-previous,"approvedConversions":None,"conversionRate":None})
        return {"websiteId":self.site.site_id,"period":period,"dataStatus":"empty_complete" if not report["rows"] else "complete","propertyTimezone":report["property_timezone"],"rows":rows,"warnings":["approved_conversion_mapping_pending"],"api":{"rowCount":report["row_count"],"dataLossFromOtherRow":report["data_loss_from_other_row"]},"_provenance":[report,prior]}

    def bundle(self, period):
        start, end = period_dates(period,self.today())
        overview = self.overview(period)
        acquisition = self.acquisition(period)
        pages_report = self.report(period,["landingPagePlusQueryString"],["sessions","activeUsers","eventCount"])
        prior_pages_report = self.report_range(*previous_dates(period,self.today()),["landingPagePlusQueryString"],["sessions","activeUsers","eventCount"])
        daily_report = self.report(period,["date"],["activeUsers","sessions","eventCount"])
        daily_events = self.report(period,["date","eventName"],["eventCount"])
        daily_channels = self.report(period,["date","sessionDefaultChannelGroup"],["sessions","activeUsers"])
        daily_pages = self.report(period,["date","landingPagePlusQueryString"],["sessions","activeUsers","eventCount"])
        events_report = self.report(period,["eventName"],["eventCount"])
        previous_pages={safe_landing_page(r["dimensions"][0]):self.num(r["metrics"][0]) for r in prior_pages_report["rows"]}
        pages = {"websiteId":self.site.site_id,"period":period,"dataStatus":"empty_complete" if not pages_report["rows"] else "complete","rows":[{"landingPage":safe_landing_page(r["dimensions"][0]),"sessions":self.num(r["metrics"][0]),"activeUsers":self.num(r["metrics"][1]),"eventCount":self.num(r["metrics"][2]),"previousSessions":previous_pages.get(safe_landing_page(r["dimensions"][0]),0),"approvedConversions":None} for r in pages_report["rows"]],"warnings":["approved_conversion_mapping_pending"],"api":{"rowCount":pages_report["row_count"],"dataLossFromOtherRow":pages_report["data_loss_from_other_row"]},"_provenance":[pages_report,prior_pages_report]}
        counts = {r["dimensions"][0]:self.num(r["metrics"][0]) for r in events_report["rows"]}
        overview_metrics={item["metric"]:item for item in overview["metrics"]}
        expected = [{"event":event,"metric":field,"count":counts.get(event,0),"previousCount":overview_metrics[field]["previousValue"],"trend":counts.get(event,0)-(overview_metrics[field]["previousValue"] or 0),"businessInterpretation":field.replace("_"," "),"state":"present" if counts.get(event,0) else "not_observed"} for field,event in EVENT_FIELDS]
        events = {"websiteId":self.site.site_id,"period":period,"dataStatus":"empty_complete" if not events_report["rows"] else "complete","expectedEvents":expected,"otherObservedEvents":[{"event":name,"count":count} for name,count in counts.items() if name not in {event for _,event in EVENT_FIELDS}],"prohibitedEvents":[],"_provenance":[events_report]}
        metric_values = {item["metric"]:item["value"] for item in overview["metrics"]}
        conversion = {"websiteId":self.site.site_id,"period":period,"dataStatus":overview["dataStatus"],"funnel":[{"stage":"form_start","value":metric_values["form_intent"]},{"stage":"form_submit","value":metric_values["technical_submissions"]},{"stage":"generate_lead","value":metric_values["generated_leads"]},{"stage":"appointment_request","value":metric_values["appointment_requests"]}],"warnings":["generate_lead_governance_blocked"] if self.site.governance_status != "approved" else []}
        provenance = overview.pop("_provenance") + acquisition.pop("_provenance") + pages.pop("_provenance") + events.pop("_provenance") + [daily_report,daily_events,daily_channels,daily_pages]
        quality = {
            "status":"warning" if all(not item["rows"] for item in provenance) else "ok",
            "freshness":freshness_for(end,self.today()),
            "empty":all(not item["rows"] for item in provenance),
            "dataLossFromOtherRow":any(item["data_loss_from_other_row"] for item in provenance),
            "subjectToThresholding":any(item["subject_to_thresholding"] for item in provenance),
            "schemaRestrictions":[item["schema_restriction"] for item in provenance if item["schema_restriction"]],
            "emptyReasons":[item["empty_reason"] for item in provenance if item["empty_reason"]],
        }
        return {"period":period,"dateRange":{"start":start,"end":end},"views":{"overview":overview,"acquisition":acquisition,"landing-pages":pages,"events":events,"conversion":conversion},"daily":{"property":daily_report,"events":daily_events,"channels":daily_channels,"pages":daily_pages},"quality":quality,"provenance":provenance}
