from __future__ import annotations

from html.parser import HTMLParser

from app.product_ui import (
    API_ROUTES,
    ONBOARDING_STEPS,
    agency_shell_html,
    client_onboarding_html,
    client_portal_html,
    login_html,
)


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.ids: set[str] = set()
        self.roles: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"] or "")
        if values.get("role"):
            self.roles.add(values["role"] or "")


def parse(markup: str) -> _Markup:
    parser = _Markup()
    parser.feed(markup)
    return parser


def test_login_checks_established_portal_identity_without_connector_oauth() -> None:
    markup = login_html(next_path="/agency", notice="Use your approved identity")
    parsed = parse(markup)

    assert "<main" in markup
    assert "id=\"login-form\"" in markup
    assert "Established portal identity" in markup
    assert "Check portal access" in markup
    assert API_ROUTES["portal_identity"] in markup
    assert "api(UI.apiRoutes.portal_identity)" in markup
    assert "/api/oauth/google/status" not in markup
    assert "/api/oauth/google/authorize" not in markup
    assert "oauth_authorize" not in markup
    assert "aria-live=\"polite\"" in markup
    assert "No report is generated or sent" in markup
    assert "main" in parsed.tags
    assert "status" in parsed.roles


def test_agency_role_visibility_is_explicit() -> None:
    owner = agency_shell_html(role="agency_owner", organization_name="Northstar Agency")
    analyst = agency_shell_html(role="agency_analyst")
    client = agency_shell_html(role="client_viewer")

    assert "Set up a client" in owner
    assert "Manage team access" in owner
    assert "/api/memberships" in owner
    assert "Set up a client" not in analyst
    assert "Client onboarding" not in analyst
    assert "Manage team access" not in analyst
    assert "Connect approved Analytics identity" not in analyst
    assert "Agency workspace access is restricted" in client
    assert "Set up a client" not in client
    assert "Other clients" in client


def test_onboarding_contains_all_six_steps_and_only_documented_browser_routes() -> None:
    markup = client_onboarding_html(
        role="agency_admin",
        company={"name": "<Example & Co>", "id": "company-1"},
        site={"id": "site-1", "canonical_domain": "https://client.example"},
        workflow_id="workflow-1",
        connection_id="connection-1",
        replay_job_id="job-1",
    )

    for number, _, label, _ in ONBOARDING_STEPS:
        assert f'data-step-button="{number}"' in markup
        assert label.replace("&", "&amp;") in markup
    assert f'data-step-panel="{number}"' in markup
    assert "/api/oauth/google/authorize" in markup
    assert API_ROUTES["oauth_assign"] in markup
    assert "connection-1" in markup
    for route_key in (
        "onboarding_create",
        "onboarding_workflow",
        "onboarding_checklist",
        "onboarding_governance",
        "onboarding_connections",
        "onboarding_first_sync",
        "onboarding_memberships",
        "onboarding_handoff",
    ):
        assert API_ROUTES[route_key] in markup
    for action in (
        "api(UI.apiRoutes.onboarding_create",
        'workflowRoute("onboarding_workflow")',
        'workflowRoute("onboarding_governance")',
        'workflowRoute("onboarding_connections")',
        'firstSync("request")',
        'workflowRoute("onboarding_memberships")',
        'workflowRoute("onboarding_handoff")',
    ):
        assert action in markup
    assert "localStorage" not in markup
    assert "/api/memberships" not in markup
    assert "reportRecipients" not in markup
    assert "/api/operations/sync-jobs/{job_id}/replay" not in markup
    assert "/internal/sync" not in markup
    assert "External gates stay explicit" in markup
    assert "No client recipient has been contacted" in markup


def test_client_portal_has_tenant_surface_reports_goals_sources_and_access_states() -> None:
    admin = client_portal_html(
        role="client_admin",
        company="Example Client",
        company_id="company-1",
        website_id="site-1",
        domain="client.example",
        health={"state": "warning", "detail": "Search Console is partial"},
        reports=[{"name": "28-day report", "status": "available"}],
        goals=[{"metric": "qualified_leads", "target": 50, "effectiveFrom": "2026-08-01"}],
        sources=[{"sourceType": "search_console", "status": "partial_data", "state": "warning"}],
    )
    viewer = client_portal_html(role="client_viewer", website_id="site-1")

    for landmark in ("overview", "reports", "goals", "sources", "support"):
        assert f'id="{landmark}"' in admin
    for route in ("overview", "measurement_health", "sync_status", "goals", "external_sources", "reports_pdf"):
        assert API_ROUTES[route].split("{")[0] in admin
    assert "id=\"goal-form\"" in admin
    assert "Save goal" in admin
    assert "id=\"goal-form\"" not in viewer
    assert "Viewer access is read-only" in viewer
    assert "this UI does not contact a client" in admin
    assert "Unavailable means unavailable, not zero" in admin


def test_dynamic_content_is_escaped_and_no_external_assets_are_required() -> None:
    payload = '<img src=x onerror="alert(1)">&"\'\n'
    markup = client_portal_html(company=payload, domain=payload, support_email="")

    assert payload not in markup
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in markup
    assert "<img" not in markup
    assert "<link" not in markup
    assert "@import" not in markup
    assert "window.__MEASUREMENT_UI__" in markup
    assert "\\u003c" in markup or "&lt;" in markup


def test_accessible_focus_states_and_mobile_css_are_present() -> None:
    markup = agency_shell_html()

    assert ":focus-visible" in markup
    assert "@media (max-width: 640px)" in markup
    assert "grid-template-columns: 1fr" in markup
    assert "@media (prefers-reduced-motion: reduce)" in markup
    assert "min-height: 2.75rem" in markup
    assert "Skip to main content" in markup
    assert 'aria-label="Agency navigation"' in markup


def test_empty_loading_and_error_states_are_rendered_as_reviewable_states() -> None:
    portal = client_portal_html(health={"state": "error", "detail": "Sync failed"})
    agency = agency_shell_html(clients=[])
    onboarding = client_onboarding_html(gates={"governance": "blocked", "contract": "pending"})

    assert "state-error" in portal
    assert "Loading health" in portal
    assert "No reports yet" in portal
    assert "No active alerts" in agency
    assert "state-blocked" in onboarding
    assert "Review required" in onboarding
