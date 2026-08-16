"""Fail-closed measurement validation, ported from the former Node runtime."""
from __future__ import annotations
import json, re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
ROOT=Path(__file__).resolve().parents[1]
ROUTES=json.loads((ROOT/"measurement/eligibility/routes.json").read_text()); RULES=json.loads((ROOT/"measurement/privacy/prohibited-data.json").read_text())
VALIDATION=json.loads((ROOT/"measurement/contracts/local_service_v1/validation.json").read_text())
EVENTS={"form_start","form_submit","generate_lead","phone_click","email_click","appointment_request","cta_click"}; NAMES={x.lower() for x in RULES["prohibited_parameter_names"]}; ALLOWED=set(RULES["url_rules"]["allowed_campaign_parameters"]); PATTERNS=[re.compile(x.removeprefix("(?i)"),re.I) for x in RULES["prohibited_value_patterns"]]
def prohibited(value): return value is not None and any(p.search(str(value)) for p in PATTERNS)
def route_status(path):
    if path in ROUTES["routes"]: return ROUTES["routes"][path]
    for entry in ROUTES["patterns"]:
        pattern=entry["pattern"]
        if path.startswith(pattern[:-1]) if pattern.endswith("*") else path==pattern: return entry["status"]
    return ROUTES["default_behavior"]
def validate_event(route,event,parameters=None,consent_granted=False):
    parsed=urlsplit(route); violations=[]; status=route_status(parsed.path)
    if not consent_granted: violations.append("consent_required")
    elif ROUTES["status"]!="approved": violations.append("manifest_not_approved")
    elif status!="approved": violations.append(f"route_{status}")
    if event not in EVENTS: violations.append(f"event_not_allowed:{event}")
    if parsed.fragment: violations.append("url_fragment")
    if any(re.fullmatch(r"\d{4,}|[0-9a-f-]{27,}",part,re.I) for part in parsed.path.split("/")): violations.append("identifier_like_path")
    for key,value in parse_qsl(parsed.query):
        if key not in ALLOWED: violations.append(f"query_parameter:{key}")
        elif prohibited(value): violations.append(f"query_value:{key}")
    for key,value in (parameters or {}).items():
        if key.lower() in NAMES: violations.append(f"prohibited_parameter:{key}")
        elif prohibited(value): violations.append(f"prohibited_value:{key}")
    return {"allowed":not violations,"violations":violations}


def validate_contract_fixtures() -> dict:
    """Execute the versioned contract fixtures with the production validator."""
    failures=[]
    for expected_allowed, bucket in ((True, "pass"), (False, "fail")):
        for index, fixture in enumerate(VALIDATION["fixtures"][bucket]):
            result=validate_event(
                fixture["route"], fixture.get("event", ""), fixture.get("parameters"),
                consent_granted=True,
            )
            if result["allowed"] is not expected_allowed:
                failures.append({
                    "bucket": bucket,
                    "index": index,
                    "route": fixture["route"],
                    "event": fixture.get("event"),
                    "expectedAllowed": expected_allowed,
                    "actualAllowed": result["allowed"],
                    "violations": result["violations"],
                })
    return {
        "ok": not failures,
        "validationVersion": VALIDATION["validation_version"],
        "fixtureCount": sum(len(VALIDATION["fixtures"][bucket]) for bucket in ("pass", "fail")),
        "failures": failures,
    }
