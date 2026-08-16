#!/usr/bin/env python3
"""Execute every declared privacy and contract validation fixture."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.privacy import route_status, validate_event  # noqa: E402


VALIDATION_PATH = ROOT / "measurement/contracts/local_service_v1/validation.json"
PRIVACY_PATH = ROOT / "measurement/privacy/fixtures.json"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"fixture document must be an object: {path}")
    return value


def _fixture_entries(document: dict[str, Any], source: str) -> list[tuple[str, int, dict[str, Any]]]:
    collection = document.get("fixtures") if source == "validation" else document
    if not isinstance(collection, dict):
        raise ValueError(f"{source} fixture collection must be an object")

    entries: list[tuple[str, int, dict[str, Any]]] = []
    for bucket, fixtures in collection.items():
        if not isinstance(fixtures, list):
            continue
        for index, fixture in enumerate(fixtures):
            if not isinstance(fixture, dict):
                raise ValueError(f"{source}.{bucket}[{index}] must be an object")
            entries.append((bucket, index, fixture))
    return entries


def _expected_allowed(source: str, bucket: str, fixture: dict[str, Any]) -> bool:
    expected = fixture.get("expected_allowed")
    if isinstance(expected, bool):
        return expected
    if source == "validation" and bucket in {"pass", "fail"}:
        return bucket == "pass"
    raise ValueError(f"{source}.{bucket} fixture is missing boolean expected_allowed")


def _effective_route(fixture: dict[str, Any]) -> str:
    route = fixture.get("route")
    if not isinstance(route, str) or not route:
        raise ValueError("fixture route must be a non-empty string")
    query = fixture.get("query")
    if query:
        query_text = str(query)
        if "?" not in route and not query_text.startswith("?"):
            query_text = "?" + query_text
        route += query_text
    fragment = fixture.get("fragment")
    if fragment:
        fragment_text = str(fragment)
        if not fragment_text.startswith("#"):
            fragment_text = "#" + fragment_text
        route += fragment_text
    return route


def run_all() -> dict[str, Any]:
    documents = {
        "validation": _load(VALIDATION_PATH),
        "privacy": _load(PRIVACY_PATH),
    }
    executed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}

    for source, document in documents.items():
        entries = _fixture_entries(document, source)
        source_counts[source] = len(entries)
        for bucket, index, fixture in entries:
            declared_route = fixture.get("route")
            case = {
                "source": source,
                "bucket": bucket,
                "index": index,
                "name": fixture.get("name"),
                "route": declared_route,
            }
            try:
                route = _effective_route(fixture)
                expected_allowed = _expected_allowed(source, bucket, fixture)
                result = validate_event(
                    route,
                    fixture.get("event") or "phone_click",
                    fixture.get("parameters") or {},
                    consent_granted=True,
                )
                actual_status = route_status(route.split("?", 1)[0].split("#", 1)[0])
                case.update(
                    {
                        "expected_allowed": expected_allowed,
                        "actual_allowed": result["allowed"],
                        "violations": result["violations"],
                        "route_status": actual_status,
                        "effective_route": route,
                    }
                )
                expected_status = fixture.get("expected_route_status")
                if result["allowed"] is not expected_allowed:
                    failures.append({**case, "failure": "allowed_mismatch"})
                if expected_status is not None and actual_status != expected_status:
                    failures.append(
                        {
                            **case,
                            "failure": "route_status_mismatch",
                            "expected_route_status": expected_status,
                        }
                    )
            except (KeyError, TypeError, ValueError) as error:
                failures.append({**case, "failure": str(error)})
            executed.append(case)

    return {
        "ok": not failures,
        "fixtureCount": len(executed),
        "sourceCounts": source_counts,
        "executed": executed,
        "failures": failures,
    }


def main() -> int:
    try:
        result = run_all()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = {"ok": False, "fixtureCount": 0, "sourceCounts": {}, "executed": [], "failures": [{"failure": str(error)}]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
