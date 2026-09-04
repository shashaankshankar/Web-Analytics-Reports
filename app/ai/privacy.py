"""Privacy boundary for values that may be serialized into AI requests.

The source connectors retain richer records for operational use, but model
requests receive only aggregate metrics, safe status text, and non-sensitive
report context. This module deliberately returns new values and never mutates
the source object supplied by a caller.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel


REDACTED_VALUE = "[redacted]"
REDACTED_QUERY = "[redacted query]"
PROVIDER_ERROR_WITHHELD = "Provider error details withheld."

_DROP = object()

_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_PATTERN = re.compile(
    r"(?<![\d-])(?!\d{4}-\d{2}-\d{2}\b)(?:\+?\d[\d().\s-]{6,}\d)(?![\d-])"
)
_STREET_ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.'#-]+\s+){1,6}"
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|"
    r"court|ct|circle|cir|parkway|pkwy|place|pl|way)\b",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(
    r"(?i)(\b(?:authorization\s*:\s*bearer|bearer|api[ _-]?key|token|secret)\b)"
    r"(?:\s*[:=]\s*|\s+)[^\s,;}'\"]+"
)
_HONORIFIC_NAME_PATTERN = re.compile(
    r"(?i)\b(?:dr|mr|mrs|ms|miss)\.?\s+[A-Za-z][A-Za-z'-]+"
    r"(?:\s+[A-Za-z][A-Za-z'-]+){0,2}\b"
)
_PATIENT_QUERY_PATTERN = re.compile(
    r"(?i)\b(?:patient|mrn|medical\s+record|diagnos\w*|symptom\w*|"
    r"prescription\w*|medication\w*|social\s+security|ssn|date\s+of\s+birth|dob)\b"
)

_SAFE_STATUS_KEYS = {
    "status",
    "currentstatus",
    "priorstatus",
    "profilestatus",
    "performancestatus",
    "searchkeywordsstatus",
    "reviewsstatus",
    "businesscallsstatus",
    "eventstatuses",
    "capabilities",
    "availableperformancemetrics",
    "limitations",
    "trackingnote",
    "reason",
}

_SAFE_AGGREGATE_KEYS = {
    "answeredcalls",
    "answeredcallschange",
    "answeredcallschanges",
    "answeredcallspercentagechange",
    "answeredcallsdirection",
    "businesscalls",
    "callbuttonclicks",
    "conversionrate",
    "currentinquiries",
    "directionrequests",
    "directionrequestschange",
    "directionrequestschanges",
    "directionrequestspercentagechange",
    "directionrequestsdirection",
    "emailbounced",
    "emailclick",
    "emailclicks",
    "emaildelivered",
    "emaildelivery",
    "emaildeliveryrate",
    "emailinquiry",
    "emailinquiries",
    "emailsent",
    "emailopen",
    "emailopens",
    "emailevents",
    "appointmentrequest",
    "ctaclick",
    "formstart",
    "formsubmit",
    "generatelead",
    "failedbatches",
    "failedreportcount",
    "missedcalls",
    "missedcallschanges",
    "missedcallspercentagechange",
    "missedcallsdirection",
    "openrate",
    "phonecall",
    "phoneclick",
    "phonecalls",
    "phonecallschange",
    "phonecallschanges",
    "phonecallspercentagechange",
    "phonecallsdirection",
    "replycoveragepercent",
    "replypresentcount",
    "replystatuscounts",
    "retentionexcludedcount",
    "successfulbatches",
    "successfulreportcount",
    "trackedreportcount",
    "trackingrate",
    "unrepliedcount",
    "queriedreportcount",
    "websiteclicks",
    "websiteclickschange",
    "websiteclickspercentagechange",
    "websiteclicksdirection",
}

_DROP_KEYS = {
    "accountid",
    "accountids",
    "additionalphones",
    "address",
    "addressline",
    "addresslines",
    "additionalemail",
    "additionalemails",
    "api_key",
    "apikey",
    "author",
    "authorattribution",
    "authorization",
    "bcc",
    "comment",
    "correlationid",
    "correlationids",
    "credential",
    "credentials",
    "deliveryrecord",
    "deliveryrecords",
    "destination",
    "email",
    "emailaddress",
    "emailaddresses",
    "emailid",
    "emailids",
    "emailrecord",
    "emailrecords",
    "exception",
    "exceptions",
    "fax",
    "from",
    "fromemail",
    "geocode",
    "latitude",
    "latlng",
    "leadid",
    "leadids",
    "location",
    "locationid",
    "locationids",
    "locationname",
    "locationuri",
    "longitude",
    "mailfrom",
    "medicalrecord",
    "message",
    "messageid",
    "messageids",
    "mobile",
    "notes",
    "originaltext",
    "patient",
    "patientid",
    "patientids",
    "patientname",
    "phone",
    "phonenumber",
    "phonenumbers",
    "postalcode",
    "profileid",
    "profileids",
    "providererror",
    "providererrors",
    "providerid",
    "providerids",
    "providerresponse",
    "rawerror",
    "rawerrors",
    "rawprovidererror",
    "rawprovidererrors",
    "rawresponse",
    "recipient",
    "recipients",
    "recipientoverride",
    "replyto",
    "resendemailid",
    "resendemailids",
    "resendid",
    "resendids",
    "reviewer",
    "reviewers",
    "reviewid",
    "reviewids",
    "reviewmediaitems",
    "reviewreply",
    "reviewreplyurl",
    "reviewurl",
    "searchquerytext",
    "secret",
    "secrets",
    "sessionid",
    "sessionids",
    "socialsecuritynumber",
    "ssn",
    "stacktrace",
    "street",
    "streetaddress",
    "targetrecipient",
    "telephone",
    "token",
    "tokens",
    "traceback",
    "traceid",
    "traceids",
    "transactionid",
    "transactionids",
    "userid",
    "userids",
    "userpseudoid",
    "userpseudoids",
    "websiteapi key",
    "websiteapikey",
    "zipcode",
}

_RAW_ERROR_KEYS = {
    "debug",
    "error",
    "errors",
    "exception",
    "exceptions",
    "providererror",
    "providererrors",
    "providerresponse",
    "rawerror",
    "rawerrors",
    "rawprovidererror",
    "rawprovidererrors",
    "rawresponse",
    "diagnostic",
    "diagnostics",
    "sourcediagnostic",
    "sourcediagnostics",
    "stacktrace",
    "traceback",
}

_REVIEW_AGGREGATE_KEYS = {
    "averagerating",
    "reviewcount",
    "reviewinventorycomplete",
    "reviewresponsesummary",
    "reviewsfetched",
    "reviewsstatus",
    "reviewstotal",
    "replycoveragepercent",
    "replypresentcount",
    "replystatuscounts",
    "totalreviewcount",
    "totalreviewscount",
    "unrepliedcount",
}

_PATIENT_FIELD_MARKERS = {
    "appointment",
    "condition",
    "diagnosis",
    "dob",
    "freeform",
    "freetext",
    "insurance",
    "medical",
    "mrn",
    "patient",
    "prescription",
    "symptom",
}


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(key).lower())


def _path_has(path: tuple[str, ...], *markers: str) -> bool:
    return any(marker in path_item for path_item in path for marker in markers)


def _is_safe_aggregate_key(normalized_key: str) -> bool:
    return normalized_key in _SAFE_AGGREGATE_KEYS or normalized_key in _SAFE_STATUS_KEYS


def _is_delivery_context(path: tuple[str, ...]) -> bool:
    return _path_has(path, "delivery", "resend", "emailmetric", "emailrecord")


def _is_review_context(path: tuple[str, ...]) -> bool:
    return _path_has(path, "review", "reviewer", "author", "attribution")


def _is_profile_context(path: tuple[str, ...]) -> bool:
    return _path_has(path, "profile")


_QUERY_KEYS = {"query", "searchquery", "querytext", "searchquerytext", "searchkeyword"}


def _is_sensitive_key(key: Any, path: tuple[str, ...]) -> bool:
    normalized_key = _normalize_key(key)
    if not normalized_key:
        return False

    if normalized_key in _SAFE_STATUS_KEYS or normalized_key in _SAFE_AGGREGATE_KEYS:
        return False
    if normalized_key in _REVIEW_AGGREGATE_KEYS:
        return False
    if normalized_key in _RAW_ERROR_KEYS or normalized_key in _DROP_KEYS:
        return True

    if (
        "providererror" in normalized_key
        or "rawerror" in normalized_key
        or "deliveryrecord" in normalized_key
    ):
        return True

    if "recipient" in normalized_key:
        return True
    if "resend" in normalized_key and ("id" in normalized_key or "record" in normalized_key):
        return True
    if normalized_key.endswith((
        "accountid",
        "customerid",
        "emailid",
        "inquiryid",
        "leadid",
        "locationid",
        "messageid",
        "profileid",
        "providerid",
        "reviewid",
    )):
        return True
    if normalized_key.endswith("userid") or normalized_key.endswith("userpseudoid"):
        return True
    if normalized_key in {"id", "ids"} and (
        _is_review_context(path) or _is_profile_context(path) or _is_delivery_context(path)
    ):
        return True

    if "phone" in normalized_key or "telephone" in normalized_key:
        return True
    if normalized_key in {"fax", "mobile"}:
        return True
    if normalized_key in {
        "address",
        "addresslines",
        "street",
        "streetaddress",
        "locality",
        "sublocality",
        "administrativearea",
        "regioncode",
        "postalcode",
        "zipcode",
        "latitude",
        "longitude",
        "latlng",
        "coordinates",
    }:
        return True
    if "address" in normalized_key:
        return True

    if "reviewer" in normalized_key or "authorattribution" in normalized_key:
        return True
    if normalized_key.startswith("review") and normalized_key not in _REVIEW_AGGREGATE_KEYS:
        return True

    if _is_review_context(path):
        if normalized_key in {"name", "displayname", "fullname", "firstname", "lastname", "url"}:
            return True
        if "review" in normalized_key or normalized_key in {"author", "comment", "text"}:
            return normalized_key not in _REVIEW_AGGREGATE_KEYS

    if _is_profile_context(path) and normalized_key in {
        "id",
        "name",
        "displayname",
        "fullname",
        "owner",
        "ownername",
    }:
        return True

    if normalized_key in {"email", "emailaddress", "emailaddresses"}:
        return True
    if normalized_key in {"to", "cc", "bcc", "replyto", "from", "sender", "mailfrom"}:
        return True

    if normalized_key in {"reviews", "recentreviewsnippets", "recentreviewsnippet"}:
        return True
    if normalized_key in {"rawsummary", "rawsummarystats"}:
        return False

    if _is_delivery_context(path) and normalized_key in {
        "record",
        "records",
        "email",
        "emails",
        "message",
        "messages",
    }:
        return True

    if any(marker in normalized_key for marker in _PATIENT_FIELD_MARKERS):
        # A metric such as ``appointment_requests`` is an aggregate count and
        # is safe; a row/detail field with the same marker is not.
        if normalized_key.endswith(("count", "counts", "rate", "rates", "status", "statuses")):
            return False
        return True

    if normalized_key in {"description", "details", "body", "text"} and (
        _is_review_context(path) or _is_profile_context(path)
    ):
        return True
    return False


def _redact_text(value: str) -> str:
    text = value
    text = _EMAIL_PATTERN.sub(REDACTED_VALUE, text)
    text = _SSN_PATTERN.sub(REDACTED_VALUE, text)
    text = _PHONE_PATTERN.sub(REDACTED_VALUE, text)
    text = _STREET_ADDRESS_PATTERN.sub(REDACTED_VALUE, text)
    text = _BEARER_PATTERN.sub(r"\1: " + REDACTED_VALUE, text)
    # Reviewer/provider names are removed by key whenever possible. This
    # honorific fallback also protects a name embedded in a status/reason.
    text = _HONORIFIC_NAME_PATTERN.sub(REDACTED_VALUE, text)
    return text


def is_unsafe_gsc_query(value: Any) -> bool:
    """Return whether a Search Console query is too specific to expose."""

    text = str(value or "").strip()
    if not text:
        return False
    if _EMAIL_PATTERN.search(text) or _SSN_PATTERN.search(text) or _PHONE_PATTERN.search(text):
        return True
    if _STREET_ADDRESS_PATTERN.search(text) or _HONORIFIC_NAME_PATTERN.search(text):
        return True
    return bool(_PATIENT_QUERY_PATTERN.search(text))


def scrub_gsc_query(value: Any) -> str:
    """Keep generic search intent while replacing an unsafe query value."""

    text = str(value or "").strip()
    if is_unsafe_gsc_query(text):
        return REDACTED_QUERY
    return _redact_text(text)


def scrub_gsc_query_filter(value: Any) -> str:
    """Store a safe representation of a model-supplied GSC query filter."""

    return scrub_gsc_query(value)


def _is_search_row(value: Mapping[Any, Any], path: tuple[str, ...]) -> bool:
    keys = {_normalize_key(key) for key in value}
    has_query = bool(keys & _QUERY_KEYS)
    has_metrics = bool(keys & {"clicks", "impressions", "ctr", "position", "insightsvalue", "insightsthreshold"})
    return has_query and (has_metrics or _path_has(path, "row", "query"))


def is_unsafe_patient_specific_row(value: Mapping[Any, Any], source: str = "") -> bool:
    """Identify rows containing direct or patient-specific source detail."""

    source_name = str(source).lower()
    for raw_key, raw_value in value.items():
        key = _normalize_key(raw_key)
        if key in {
            "patient",
            "patientid",
            "patientname",
            "userid",
            "userpseudoid",
            "customerid",
            "leadid",
            "inquiryid",
            "email",
            "emailaddress",
            "phone",
            "phonenumber",
            "address",
            "streetaddress",
            "medicalrecord",
            "mrn",
            "diagnosis",
            "condition",
            "symptom",
            "prescription",
            "appointmentdetails",
            "freeform",
            "freetext",
            "notes",
        } or key.endswith(("userid", "userpseudoid", "patientid", "leadid", "inquiryid")):
            return True
        if key in _QUERY_KEYS:
            if is_unsafe_gsc_query(raw_value):
                return True
        if isinstance(raw_value, Mapping):
            if is_unsafe_patient_specific_row(raw_value, source=source_name):
                return True
        elif isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes, bytearray)):
            if any(
                isinstance(item, Mapping) and is_unsafe_patient_specific_row(item, source=source_name)
                for item in raw_value
            ):
                return True
        elif isinstance(raw_value, str):
            if _EMAIL_PATTERN.search(raw_value) or _SSN_PATTERN.search(raw_value):
                return True
            if _PHONE_PATTERN.search(raw_value) and key not in _SAFE_AGGREGATE_KEYS:
                return True
            if source_name == "gsc" and key in _QUERY_KEYS and is_unsafe_gsc_query(raw_value):
                return True
    return False


def sanitize_for_ai(value: Any, *, _path: tuple[str, ...] = ()) -> Any:
    """Recursively return a JSON-compatible, privacy-safe AI value.

    Direct identifiers and raw provider/delivery details are removed. Known
    identifiers embedded in free text are redacted, while safe aggregate
    counts, rates, and source status text remain available to the model.
    """

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif isinstance(value, Enum):
        return sanitize_for_ai(value.value, _path=_path)

    if isinstance(value, Mapping):
        if _is_search_row(value, _path) and is_unsafe_patient_specific_row(value, source="gsc"):
            return _DROP

        output: dict[str, Any] = {}
        normalized_path = tuple((*_path,))
        for raw_key, raw_value in value.items():
            key_text = str(raw_key)
            normalized_key = _normalize_key(raw_key)
            if _is_sensitive_key(raw_key, normalized_path):
                continue

            if normalized_key in _QUERY_KEYS:
                if is_unsafe_gsc_query(raw_value):
                    if _is_search_row(value, _path):
                        return _DROP
                    output[key_text] = scrub_gsc_query_filter(raw_value)
                    continue
                raw_value = scrub_gsc_query(raw_value)

            if normalized_key in _RAW_ERROR_KEYS:
                continue

            child_path = (*normalized_path, normalized_key)
            sanitized = sanitize_for_ai(raw_value, _path=child_path)
            if sanitized is not _DROP:
                output[key_text] = sanitized
        return output

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        output_list: list[Any] = []
        for item in value:
            sanitized = sanitize_for_ai(item, _path=_path)
            if sanitized is not _DROP:
                output_list.append(sanitized)
        return output_list

    if isinstance(value, str):
        if _path and _normalize_key(_path[-1]) in _RAW_ERROR_KEYS:
            return PROVIDER_ERROR_WITHHELD
        return _redact_text(value)

    return value


def sanitize_gsc_rows(rows: Any) -> list[dict[str, Any]]:
    """Drop unsafe GSC rows and scrub retained query values."""

    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or is_unsafe_patient_specific_row(row, source="gsc"):
            continue
        sanitized = sanitize_for_ai(row, _path=("gsc", "row"))
        if isinstance(sanitized, dict):
            output.append(sanitized)
    return output


def sanitize_source_rows(rows: Any, source: str = "") -> list[dict[str, Any]]:
    """Sanitize structured source rows before facts or candidates are built."""

    if str(source).lower() == "gsc":
        return sanitize_gsc_rows(rows)
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or is_unsafe_patient_specific_row(row, source=source):
            continue
        sanitized = sanitize_for_ai(row, _path=(str(source).lower(), "row"))
        if isinstance(sanitized, dict):
            output.append(sanitized)
    return output


def serialize_for_ai(value: Any) -> str:
    """Serialize only the privacy-safe copy of a value for an AI request."""

    sanitized = sanitize_for_ai(value)
    if sanitized is _DROP:
        sanitized = None
    return json.dumps(sanitized, ensure_ascii=False, default=str)


# Descriptive aliases keep call sites readable and make the boundary easy to
# reuse for future AI request types without exposing the source records.
sanitize_ai_payload = sanitize_for_ai
sanitize_outbound_payload = sanitize_for_ai
serialize_ai_payload = serialize_for_ai


__all__ = [
    "PROVIDER_ERROR_WITHHELD",
    "REDACTED_QUERY",
    "REDACTED_VALUE",
    "is_unsafe_gsc_query",
    "is_unsafe_patient_specific_row",
    "sanitize_ai_payload",
    "sanitize_for_ai",
    "sanitize_gsc_rows",
    "sanitize_outbound_payload",
    "sanitize_source_rows",
    "scrub_gsc_query",
    "scrub_gsc_query_filter",
    "serialize_ai_payload",
    "serialize_for_ai",
]
