import json
import subprocess
from pathlib import Path

import pytest

from app.privacy import route_status, validate_contract_fixtures, validate_event


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "measurement/privacy/run_fixtures.py"


def test_versioned_contract_fixtures_execute_against_runtime_validator():
    result = validate_contract_fixtures()
    assert result["ok"], result
    assert result["fixtureCount"] == 8


def test_executable_runner_executes_every_declared_fixture():
    completed = subprocess.run(
        [str(RUNNER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    validation = json.loads(
        (ROOT / "measurement/contracts/local_service_v1/validation.json").read_text(encoding="utf-8")
    )
    privacy = json.loads(
        (ROOT / "measurement/privacy/fixtures.json").read_text(encoding="utf-8")
    )
    expected_counts = {
        "validation": sum(len(entries) for entries in validation["fixtures"].values()),
        "privacy": sum(len(entries) for entries in privacy.values() if isinstance(entries, list)),
    }
    assert result["ok"] is True
    assert result["sourceCounts"] == expected_counts
    assert result["fixtureCount"] == sum(expected_counts.values())
    assert len(result["executed"]) == result["fixtureCount"]


@pytest.mark.parametrize(
    ("route", "expected_status"),
    [
        ("/services", "approved"),
        ("/services.html", "prohibited"),
        ("/appointment/request", "requires_review"),
        ("/patient/12345", "prohibited"),
        ("/not-in-manifest", "prohibited"),
        ("/unclassified", "prohibited"),
    ],
)
def test_non_approved_route_classes_fail_closed(route, expected_status):
    assert route_status(route) == expected_status
    result = validate_event(route, "phone_click", {}, True)
    assert result["allowed"] is (expected_status == "approved"), result


def test_privacy_manifest_remains_fail_closed_without_consent_or_safe_payload():
    assert not validate_event("/services", "phone_click", {"location": "hero"}, False)["allowed"]
    assert not validate_event("/contact?email=john@example.com", "generate_lead", {}, True)["allowed"]
    assert not validate_event("/contact", "generate_lead", {"email": "john@example.com"}, True)["allowed"]
    assert not validate_event("/patient/12345", "phone_click", {}, True)["allowed"]


def test_canonical_services_path_is_the_only_approved_services_path():
    safe = validate_event(
        "/services",
        "cta_click",
        {"location": "body", "cta_id": "book_consultation", "link_type": "internal"},
        True,
    )
    legacy = validate_event(
        "/services.html",
        "cta_click",
        {"location": "body", "cta_id": "book_consultation", "link_type": "internal"},
        True,
    )
    assert safe["allowed"] is True, safe
    assert legacy["allowed"] is False, legacy


def test_debugview_status_matches_the_repository_evidence_policy():
    validation = json.loads(
        (ROOT / "measurement/contracts/local_service_v1/validation.json").read_text(encoding="utf-8")
    )
    assert validation["evidence_policy"]["debugview_required"] is False
    assert validation["approval"]["status"] == "approved_governance_and_live_evidence"
