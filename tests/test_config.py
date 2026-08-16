from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import DEFAULT_SITE_CONFIG_ENV, SITE_CONFIG_ENV, load_site


ROOT = Path(__file__).resolve().parents[1]


def _site_config(site_id: str = "website_example", company_id: str = "company_example") -> dict:
    return {
        "siteId": site_id,
        "companyId": company_id,
        "company": "Example Client",
        "canonicalDomain": "https://example.invalid",
        "deployment": {"status": "configured"},
        "businessTimezone": "UTC",
        "ga4": {
            "propertyId": "123456789",
            "webStreamId": "987654321",
            "measurementId": "G-EXAMPLE123",
            "collectionStatus": "pending",
            "propertyTimezone": "UTC",
        },
        "governance": {"status": "pending"},
    }


def test_load_site_uses_explicit_environment_selected_config(tmp_path):
    path = tmp_path / "client-a.json"
    path.write_text(json.dumps(_site_config()), encoding="utf-8")

    site = load_site({SITE_CONFIG_ENV: str(path)})

    assert site.site_id == "website_example"
    assert site.company_id == "company_example"
    assert site.canonical_domain == "https://example.invalid"
    assert site.property_id == "123456789"
    assert site.stream_id == "987654321"
    assert site.measurement_id == "G-EXAMPLE123"


def test_named_default_site_config_is_only_used_when_primary_selection_is_absent(tmp_path):
    path = tmp_path / "example-site.json"
    path.write_text(json.dumps(_site_config("website_default")), encoding="utf-8")

    site = load_site({DEFAULT_SITE_CONFIG_ENV: str(path)})

    assert site.site_id == "website_default"


def test_implicit_single_file_fallback_requires_unambiguous_site_directory(monkeypatch, tmp_path):
    (tmp_path / "one.json").write_text(json.dumps(_site_config("website_one")), encoding="utf-8")
    (tmp_path / "two.json").write_text(json.dumps(_site_config("website_two")), encoding="utf-8")

    import app.config as config

    monkeypatch.setattr(config, "SITE_CONFIG_DIRECTORY", tmp_path)
    with pytest.raises(RuntimeError, match="site_config_selection_required"):
        load_site({})


def test_generic_contract_and_env_template_contain_no_first_client_identifiers():
    first_client = json.loads((ROOT / "measurement/sites/house-of-dental.json").read_text(encoding="utf-8"))
    contract_text = (ROOT / "measurement/contracts/local_service_v1/contract.json").read_text(encoding="utf-8").casefold()
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8").casefold()
    ga4 = first_client["ga4"]

    for identifier in (
        first_client["company"],
        first_client["canonicalDomain"],
        str(ga4["propertyId"]),
        str(ga4["webStreamId"]),
        ga4["measurementId"],
    ):
        assert identifier.casefold() not in contract_text
        assert identifier.casefold() not in env_text

    contract = json.loads((ROOT / "measurement/contracts/local_service_v1/contract.json").read_text(encoding="utf-8"))
    assert contract["scope"]["site_config_required"] is True
    assert contract["scope"]["site_config_env"] == SITE_CONFIG_ENV
    assert "site_config_fields" in contract["scope"]
