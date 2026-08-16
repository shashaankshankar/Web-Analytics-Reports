from __future__ import annotations

import json
from html import escape
from pathlib import Path

import pytest

from app.config import Site
from app.dashboard import agency_dashboard_html, dashboard_html


def _site(
    *,
    site_id: str,
    company_id: str,
    company: str,
    domain: str,
) -> Site:
    return Site(
        site_id=site_id,
        company_id=company_id,
        company=company,
        canonical_domain=domain,
        deployment_status="configured",
        business_timezone="UTC",
        property_id="123456789",
        stream_id="987654321",
        measurement_id="G-SYNTHETIC1",
        property_timezone="UTC",
        collection_status="pending",
        governance_status="pending",
    )


SYNTHETIC_SITES = (
    _site(
        site_id="site-northwind/outdoor",
        company_id="company-northwind?outdoor",
        company="Northwind Outdoor",
        domain="https://northwind.example",
    ),
    _site(
        site_id="site-lumen-arts",
        company_id="company-lumen-arts",
        company='Lumen <Arts> & "Studio"',
        domain="https://lumen.example",
    ),
)


def _javascript_string(value: str) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


@pytest.mark.parametrize("site", SYNTHETIC_SITES)
def test_dashboard_uses_passed_tenant_ids_for_every_api_path(site: Site) -> None:
    markup = dashboard_html(site)

    assert f"const siteId={_javascript_string(site.site_id)};" in markup
    assert f"const companyId={_javascript_string(site.company_id)};" in markup
    assert "/api/companies/${encodeURIComponent(companyId)}/overview?period=${p}" in markup
    assert "/api/companies/company_house_of_dental" not in markup
    assert "/api/websites/${encodeURIComponent(siteId)}/" in markup
    assert "/api/websites/${site}" not in markup
    assert "encodeURIComponent(window.start)" in markup
    assert "encodeURIComponent(window.end)" in markup
    assert f"<title>{escape(site.company, quote=True)} Analytics</title>" in markup
    assert escape(site.canonical_domain, quote=True) in markup


def test_dashboard_keeps_unavailable_distinct_from_measured_zero() -> None:
    markup = dashboard_html(SYNTHETIC_SITES[0])

    assert "business.outcomeDataStatus==='available'" in markup
    assert "paid.dataStatus==='available'&&paid.totals" in markup
    assert "Unavailable" in markup
    assert "Google Ads data is unavailable; this is not a zero." in markup
    assert "Confirmed business outcomes are unavailable" in markup


def test_agency_dashboard_escapes_company_and_encodes_site_paths() -> None:
    site = SYNTHETIC_SITES[1]
    markup = agency_dashboard_html(site)

    assert f"{escape(site.company, quote=True)} portfolio" in markup
    assert site.company not in markup
    assert "pilot portfolio" not in markup
    assert f"const siteId={_javascript_string(site.site_id)};" in markup
    assert "/api/websites/${encodeURIComponent(siteId)}/annotations" in markup
    assert "/api/websites/${site}" not in markup


def test_legacy_dashboard_module_has_no_first_client_identifiers() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "dashboard.py").read_text(encoding="utf-8").casefold()

    for identifier in ("house of dental", "house_of_dental", "thehouseofdental"):
        assert identifier not in source
