from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.auth import IAP_ISSUER, TenantContext
from app.config import Settings, load_site
from app.main import create_app


AUDIENCE = "/projects/123456789/global/backendServices/987654321"
TOKEN = "x" * 32


class PortalDatabase:
    configured = True

    def __init__(self) -> None:
        configured_site = load_site()
        self.configured_site_id = configured_site.site_id
        self.configured_company_id = configured_site.company_id
        self.authorized_calls: list[tuple[str, str | None]] = []
        self.website_ids = {configured_site.site_id, "tenant-b-site"}
        self.company_ids = {configured_site.company_id, "tenant-b-company"}

    def close(self) -> None:
        pass

    def health(self) -> dict:
        return {"status": "ok", "database": "measurement", "migrated": True}

    def authorize_context(self, email: str, organization_id: str | None = None) -> TenantContext:
        self.authorized_calls.append((email, organization_id))
        roles = {
            "agency@example.com": ("agency-tenant", "agency-user", "agency_owner"),
            "client@example.com": ("client-tenant", "client-user", "client_viewer"),
            "admin@example.com": ("client-tenant", "client-admin", "client_admin"),
        }
        if email not in roles:
            raise PermissionError("membership_not_found")
        organization, user, role = roles[email]
        if organization_id is not None and organization_id != organization:
            raise PermissionError("membership_not_found")
        return TenantContext(organization, user, email, role)

    def website_authorized(self, context: TenantContext, website_id: str) -> bool:
        return website_id in self.website_ids and context.organization_id in {"agency-tenant", "client-tenant"}

    def company_authorized(self, context: TenantContext, company_id: str) -> bool:
        return company_id in self.company_ids and context.organization_id in {"agency-tenant", "client-tenant"}

    def latest_snapshot(self, context: TenantContext, website_id: str, view: str, period: str) -> dict:
        return {"websiteId": website_id, "view": view, "period": period, "metrics": []}

    def portal_resources(self, context: TenantContext) -> list[dict]:
        if context.role in {"client_admin", "client_viewer"}:
            return [
                {
                    "company_id": "tenant-b-company",
                    "company": "Tenant B",
                    "website_id": "tenant-b-site",
                    "canonical_domain": "tenant-b.example",
                }
            ]
        return [
            {
                "company_id": "tenant-b-company",
                "company": "Tenant B",
                "website_id": "tenant-b-site",
                "canonical_domain": "tenant-b.example",
            }
        ]

    def portfolio_summary(self, context: TenantContext, period: str) -> dict:
        context.require_role(frozenset({"agency_owner", "agency_admin", "agency_analyst"}))
        return {
            "organizationId": context.organization_id,
            "period": period,
            "websites": [
                {
                    "websiteId": "tenant-b-site",
                    "company": "Tenant B",
                    "canonicalDomain": "tenant-b.example",
                    "syncHealth": "ready",
                    "lastCompleteDate": "2026-08-14",
                    "actionRequired": False,
                    "alerts": [],
                }
            ],
        }


def portal_settings() -> Settings:
    site = load_site()
    return Settings(
        "live",
        True,
        True,
        site.property_id,
        site.stream_id,
        TOKEN,
        "127.0.0.1",
        3000,
        database_url="postgresql://configured",
        operator_email="operator@example.com",
        portal_iap_mode="direct",
        portal_iap_expected_audience=AUDIENCE,
    )


def iap_headers(
    email: str,
    *,
    assertion: str = "signed-iap",
    authorization: str | None = None,
    x_serverless_authorization: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-Goog-IAP-JWT-Assertion": assertion,
        "X-Goog-Authenticated-User-Email": f"accounts.google.com:{email}",
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    if x_serverless_authorization is not None:
        headers["X-Serverless-Authorization"] = x_serverless_authorization
    return headers


def install_iap_verifier(monkeypatch):
    now = int(time.time())
    calls: list[tuple[str, str]] = []

    def verify(token: str, audience: str) -> dict:
        calls.append((token, audience))
        return {
            "iss": IAP_ISSUER,
            "aud": AUDIENCE,
            "email": "client@example.com",
            "iat": now - 10,
            "exp": now + 120,
        }

    monkeypatch.setattr("app.auth.iap_token_claims", verify)
    return calls


def test_portal_uses_verified_iap_identity_and_ignores_user_org_header(monkeypatch):
    calls = install_iap_verifier(monkeypatch)
    database = PortalDatabase()

    with TestClient(create_app(portal_settings(), database=database)) as client:
        response = client.get(
            "/dashboard?website_id=tenant-b-site&company_id=tenant-b-company",
            headers={
                **iap_headers(
                    "client@example.com",
                    authorization="Bearer transport-authorization",
                    x_serverless_authorization="Bearer transport-service-token",
                ),
                "X-Organization-ID": "attacker-tenant",
            },
        )

    assert response.status_code == 200
    assert "Client portal" in response.text
    assert calls == [("signed-iap", AUDIENCE)]
    assert database.authorized_calls == [("client@example.com", None)]


def test_second_tenant_is_resolved_from_db_resources_not_boot_site(monkeypatch):
    install_iap_verifier(monkeypatch)
    database = PortalDatabase()

    with TestClient(create_app(portal_settings(), database=database)) as client:
        resources = client.get("/portal/access/resources", headers=iap_headers("client@example.com"))
        continued = client.post(
            "/portal/access/continue?website_id=tenant-b-site&company_id=tenant-b-company",
            headers=iap_headers("client@example.com"),
        )
        portal = client.get(
            "/portal/client/tenant-b-site?company_id=tenant-b-company",
            headers=iap_headers("client@example.com"),
        )
        overview = client.get(
            "/api/companies/tenant-b-company/overview?period=28d",
            headers=iap_headers("client@example.com"),
        )

    assert resources.status_code == 200
    assert resources.json()["resources"] == [
        {
            "companyId": "tenant-b-company",
            "company": "Tenant B",
            "websiteId": "tenant-b-site",
            "canonicalDomain": "tenant-b.example",
        }
    ]
    assert continued.status_code == 200
    assert "tenant-b-site" in continued.json()["redirect"]
    assert "tenant-b-company" in continued.json()["redirect"]
    assert portal.status_code == 200
    assert "tenant-b.example" in portal.text
    assert overview.status_code == 200
    assert overview.json()["websiteId"] == "tenant-b-site"


def test_portal_requires_signed_iap_identity_and_ignores_transport_only_headers(monkeypatch):
    install_iap_verifier(monkeypatch)
    database = PortalDatabase()

    with TestClient(create_app(portal_settings(), database=database)) as client:
        absent = client.get("/portal/access/status")
        transport_only = client.get(
            "/portal/access/status",
            headers={
                "Authorization": "Bearer transport-authorization",
                "X-Serverless-Authorization": "Bearer transport-service-token",
            },
        )

    assert absent.status_code == 401
    assert absent.json()["detail"] == "identity_assertion_required"
    assert transport_only.status_code == 401
    assert transport_only.json()["detail"] == "identity_assertion_required"
    assert database.authorized_calls == []


def test_portal_rejects_invalid_signed_iap_claims(monkeypatch):
    now = int(time.time())
    database = PortalDatabase()

    def verify(token: str, audience: str) -> dict:
        return {
            "iss": "https://accounts.google.com" if token == "wrong-issuer" else IAP_ISSUER,
            "aud": "wrong-audience" if token == "wrong-audience" else AUDIENCE,
            "email": "client@example.com",
            "iat": now - 10,
            "exp": now if token == "expired" else now + 120,
        }

    monkeypatch.setattr("app.auth.iap_token_claims", verify)
    with TestClient(create_app(portal_settings(), database=database)) as client:
        responses = [
            client.get("/portal/access/status", headers=iap_headers("client@example.com", assertion=token))
            for token in ("wrong-issuer", "wrong-audience", "expired")
        ]

    assert [response.status_code for response in responses] == [401, 401, 401]
    assert all(response.json()["detail"] == "invalid_portal_identity" for response in responses)
    assert database.authorized_calls == []


def test_portal_routes_agency_onboarding_and_client_site_boundaries(monkeypatch):
    database = PortalDatabase()
    now = int(time.time())

    def verify(token: str, audience: str) -> dict:
        email = "agency@example.com" if token == "agency-iap" else "client@example.com"
        return {"iss": IAP_ISSUER, "aud": AUDIENCE, "email": email, "iat": now - 10, "exp": now + 120}

    monkeypatch.setattr("app.auth.iap_token_claims", verify)
    with TestClient(create_app(portal_settings(), database=database)) as client:
        agency = client.get("/agency", headers=iap_headers("agency@example.com", assertion="agency-iap"))
        onboarding = client.get("/agency/onboarding", headers=iap_headers("agency@example.com", assertion="agency-iap"))
        client_agency = client.get("/agency", headers=iap_headers("client@example.com"))
        client_onboarding = client.get("/agency/onboarding", headers=iap_headers("client@example.com"))
        client_portal = client.get(
            "/portal/client/tenant-b-site?company_id=tenant-b-company",
            headers=iap_headers("client@example.com"),
        )
        unauthorized_site = client.get(
            "/portal/client/other-site?company_id=other-company",
            headers=iap_headers("client@example.com"),
        )

    assert agency.status_code == 200 and "Portfolio overview" in agency.text
    assert onboarding.status_code == 200 and "Client onboarding wizard" in onboarding.text
    assert client_agency.status_code == 200 and "Agency workspace access is restricted" in client_agency.text
    assert client_onboarding.status_code == 403
    assert client_portal.status_code == 200 and "Client portal" in client_portal.text
    assert unauthorized_site.status_code == 403


def test_portal_login_is_access_only_and_does_not_present_connector_oauth_as_identity(monkeypatch):
    install_iap_verifier(monkeypatch)
    with TestClient(create_app(portal_settings(), database=PortalDatabase())) as client:
        response = client.get("/portal/login?next_path=/portal")

    assert response.status_code == 200
    assert "/api/portal/identity" in response.text
    assert "/portal/access/status" not in response.text
    assert "/portal/access/continue" not in response.text
    assert "/api/oauth/google" not in response.text
    assert "Portal access is separate from Analytics connector authorization." in response.text


def test_generic_application_modules_contain_no_first_client_identifier():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    generic_modules = (root / "app" / "main.py", root / "app" / "auth.py", root / "app" / "config.py", root / "app" / "product_ui.py")
    source = "\n".join(path.read_text() for path in generic_modules)

    assert "House of Dental" not in source
    assert "company_house_of_dental" not in source
    assert "website_house_of_dental" not in source

    main_source = (root / "app" / "main.py").read_text()
    portal_start = main_source.index("    async def portal_site_selection(")
    portal_end = main_source.index("    def require_agency(", portal_start)
    portal_routes = main_source[portal_start:portal_end]
    assert "site.company_id" not in portal_routes
    assert "site.site_id" not in portal_routes


def test_callback_only_mode_still_leaves_health_and_callback_routes_available(monkeypatch):
    install_iap_verifier(monkeypatch)
    settings = Settings(**{**portal_settings().__dict__, "oauth_callback_only": True})
    with TestClient(create_app(settings, database=PortalDatabase())) as client:
        health = client.get("/health")
        callback = client.get("/oauth/google/callback")
        portal = client.get("/portal/login")

    assert health.status_code == 200
    assert callback.status_code == 400
    assert portal.status_code == 404


def test_react_portal_static_assets_serving():
    """Verify that the compiled React SPA index.html and assets are served by FastAPI."""
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/portal")
        assert response.status_code == 200
        assert "<div id=\"root\"></div>" in response.text or "vite" in response.text.lower() or "html" in response.text.lower()
        assert response.headers.get("content-type", "").startswith("text/html")
