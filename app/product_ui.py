"""Server-rendered product surfaces for the measurement platform.

This module deliberately has no framework dependency and does not register routes.
The application can mount these generators wherever its auth and tenant middleware
are ready.  The browser code only calls API routes that already exist in
``app.main``; missing provisioning capabilities are shown as explicit operator
gates instead of being represented as successful actions.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any
from urllib.parse import quote


AGENCY_ROLES = frozenset({"agency_owner", "agency_admin", "agency_analyst"})
CLIENT_ROLES = frozenset({"client_admin", "client_viewer"})
KNOWN_ROLES = AGENCY_ROLES | CLIENT_ROLES

ONBOARDING_STEPS = (
    (1, "company-site", "Company & site", "Confirm the client and reporting boundary."),
    (2, "privacy-contract", "Privacy & contract", "Review eligibility, consent, and the versioned contract."),
    (3, "analytics-connection", "Analytics connection", "Connect read-only Analytics access and assign the approved property."),
    (4, "first-sync", "First sync", "Verify stored data and freshness before handoff."),
    (5, "client-access", "Client access", "Invite the client with the smallest appropriate role."),
    (6, "handoff", "Handoff", "Review the gates and share the portal entry point."),
)

# These are the stable routes currently exposed by app.main.  Keeping them in
# one manifest makes the UI contract reviewable and keeps browser code from
# inventing internal or provider-specific endpoints.
API_ROUTES = {
    # Portal access is established by Cloud Run/IAP before application routes
    # execute.  This endpoint is an application-level read of that identity;
    # it is intentionally separate from every provider connector route.
    "portal_identity": "/api/portal/identity",
    "portal_session": "/api/portal/identity",
    "portfolio": "/api/portfolio/summary",
    "overview": "/api/companies/{company_id}/overview",
    "acquisition": "/api/websites/{website_id}/acquisition",
    "conversion": "/api/websites/{website_id}/conversion",
    "landing_pages": "/api/websites/{website_id}/landing-pages",
    "measurement_health": "/api/websites/{website_id}/measurement-health",
    "sync_status": "/api/websites/{website_id}/sync-status",
    "goals": "/api/websites/{website_id}/goals",
    "external_sources": "/api/websites/{website_id}/external-sources",
    "reports_pdf": "/api/websites/{website_id}/reports/pdf",
    "recurring_reports": "/api/websites/{website_id}/recurring-reports",
    "memberships": "/api/memberships",
    "oauth_status": "/api/oauth/google/status",
    "oauth_authorize": "/api/oauth/google/authorize",
    "oauth_connections": "/api/oauth/google/connections/{connection_id}/properties",
    "oauth_assign": "/api/oauth/google/connections/{connection_id}/assign",
    "sync_replay": "/api/operations/sync-jobs/{job_id}/replay",
    # Resumable, tenant-scoped onboarding workflow contract.
    "onboarding_create": "/api/onboarding/workflows",
    "onboarding_workflow": "/api/onboarding/workflows/{workflow_id}",
    "onboarding_checklist": "/api/onboarding/workflows/{workflow_id}/checklist",
    "onboarding_handoff": "/api/onboarding/workflows/{workflow_id}/handoff",
    "onboarding_governance": "/api/onboarding/workflows/{workflow_id}/governance",
    "onboarding_connections": "/api/onboarding/workflows/{workflow_id}/connections",
    "onboarding_first_sync": "/api/onboarding/workflows/{workflow_id}/first-sync",
    "onboarding_memberships": "/api/onboarding/workflows/{workflow_id}/memberships",
}


PORTAL_API_ROUTES = {
    key: API_ROUTES[key]
    for key in (
        "portal_identity",
        "overview",
        "measurement_health",
        "sync_status",
        "goals",
        "external_sources",
        "reports_pdf",
    )
}


ONBOARDING_API_ROUTES = {
    key: API_ROUTES[key]
    for key in (
        "onboarding_create",
        "onboarding_workflow",
        "onboarding_checklist",
        "onboarding_handoff",
        "onboarding_governance",
        "onboarding_connections",
        "onboarding_first_sync",
        "onboarding_memberships",
        "oauth_status",
        "oauth_authorize",
        "oauth_connections",
        "oauth_assign",
    )
}


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _e(value: Any, default: str = "") -> str:
    return escape(_text(value, default), quote=True)


def _items(value: Any) -> list[Any]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []


def _safe_path(value: Any, default: str) -> str:
    path = _text(value).strip()
    if not path.startswith("/") or path.startswith("//") or any(ord(c) < 32 for c in path):
        return default
    return path


def _safe_json(value: Any) -> str:
    encoded = json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    # The payload is placed inside an inline script.  Escaping these characters
    # prevents a value containing </script> from closing the element early.
    return (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _route(template: str, **values: Any) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", quote(_text(value), safe=""))
    return result


def _normal_role(role: Any) -> str:
    value = _text(role, "agency_analyst").strip().lower()
    return value if value in KNOWN_ROLES else "agency_analyst"


def _status_chip(label: Any, state: Any = "info", detail: Any = "") -> str:
    normalized = _text(state, "info").lower().replace(" ", "-")
    if normalized not in {"ok", "ready", "warning", "pending", "blocked", "error", "info", "neutral"}:
        normalized = "info"
    detail_html = f'<span class="chip-detail">{_e(detail)}</span>' if detail else ""
    return f'<span class="chip chip-{normalized}" data-state="{normalized}"><span class="chip-dot" aria-hidden="true"></span>{_e(label)}{detail_html}</span>'


def _state_card(title: Any, detail: Any, state: str = "info", *, element_id: str = "") -> str:
    id_attr = f' id="{_e(element_id)}"' if element_id else ""
    return (
        f'<div{id_attr} class="state state-{_e(state)}" data-state="{_e(state)}" role="status">'
        f'<strong>{_e(title)}</strong><span>{_e(detail)}</span></div>'
    )


BASE_CSS = r"""
:root {
  --ink: #17201c;
  --muted: #63706a;
  --paper: #f3f0e8;
  --card: #fffdf9;
  --green: #174d3a;
  --green-deep: #103b2c;
  --green-soft: #e4eee9;
  --gold: #c99b45;
  --gold-soft: #f7ecd5;
  --line: #dfe4df;
  --warn: #9a5b10;
  --danger: #a43c35;
  --shadow: 0 18px 45px rgba(23, 61, 43, .08);
}
* { box-sizing: border-box; }
html { color-scheme: light; scroll-behavior: smooth; }
body {
  margin: 0;
  min-width: 18rem;
  background: var(--paper);
  color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: var(--green); text-underline-offset: .17em; }
a:hover { color: var(--green-deep); }
button, input, select, textarea { font: inherit; }
button, .button {
  border: 1px solid var(--green);
  border-radius: .58rem;
  background: var(--green);
  color: #fff;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: .45rem;
  min-height: 2.75rem;
  padding: .62rem .9rem;
  text-decoration: none;
  transition: background .16s ease, border-color .16s ease, transform .16s ease;
}
button:hover, .button:hover { background: var(--green-deep); border-color: var(--green-deep); color: #fff; }
button.secondary, .button.secondary { background: transparent; color: var(--green); }
button.secondary:hover, .button.secondary:hover { background: var(--green-soft); color: var(--green-deep); }
button.ghost, .button.ghost { background: transparent; border-color: transparent; color: var(--green); }
button[disabled], .button[aria-disabled="true"] { cursor: not-allowed; opacity: .52; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: 3px solid rgba(201, 155, 69, .72);
  outline-offset: 3px;
}
input, select, textarea {
  width: 100%;
  border: 1px solid #bdc9c1;
  border-radius: .55rem;
  background: #fff;
  color: var(--ink);
  padding: .66rem .72rem;
}
textarea { min-height: 7rem; resize: vertical; }
label { display: block; font-weight: 650; margin: .75rem 0 .34rem; }
fieldset { border: 0; margin: 0; padding: 0; }
legend { font-weight: 700; margin-bottom: .65rem; }
.skip-link { position: absolute; left: .8rem; top: .8rem; transform: translateY(-180%); z-index: 20; }
.skip-link:focus { transform: translateY(0); }
.sr-only { border: 0; clip: rect(0 0 0 0); clip-path: inset(50%); height: 1px; margin: -1px; overflow: hidden; padding: 0; position: absolute; white-space: nowrap; width: 1px; }
.app-header { background: var(--green); color: #fff; }
.header-inner, .page { width: min(1180px, calc(100% - 2.4rem)); margin-inline: auto; }
.header-inner { min-height: 4.8rem; display: flex; align-items: center; justify-content: space-between; gap: 1.2rem; padding-block: .8rem; }
.brand { align-items: center; color: #fff; display: inline-flex; gap: .68rem; font: 650 1.06rem/1.15 Georgia, serif; text-decoration: none; }
.brand:hover { color: #fff; }
.brand-mark { align-items: center; background: var(--gold); border-radius: .55rem; color: var(--green-deep); display: inline-flex; font: 750 .9rem/1 Georgia, serif; height: 2rem; justify-content: center; letter-spacing: -.05em; width: 2rem; }
.header-meta { align-items: center; display: flex; flex-wrap: wrap; gap: .7rem 1rem; justify-content: flex-end; }
.header-meta small { color: rgba(255,255,255,.78); }
.page { padding-block: 1.5rem 3rem; }
.page-narrow { width: min(720px, calc(100% - 2.4rem)); }
.eyebrow { color: var(--green); font-size: .72rem; font-weight: 800; letter-spacing: .12em; margin: 0 0 .4rem; text-transform: uppercase; }
h1, h2, h3 { letter-spacing: -.015em; }
h1 { font: 650 clamp(1.65rem, 3.3vw, 2.55rem)/1.1 Georgia, serif; margin: 0; }
h2 { font: 650 1.32rem/1.2 Georgia, serif; margin: 0 0 .65rem; }
h3 { font-size: 1rem; margin: 0 0 .35rem; }
.lead { color: var(--muted); font-size: 1.02rem; margin: .65rem 0 0; max-width: 70ch; }
.page-heading { align-items: flex-end; display: flex; flex-wrap: wrap; gap: 1rem; justify-content: space-between; margin-bottom: 1.35rem; }
.page-heading-actions { align-items: center; display: flex; flex-wrap: wrap; gap: .55rem; }
.shell-grid { display: grid; gap: 1.1rem; grid-template-columns: 15.3rem minmax(0, 1fr); }
.sidebar { align-self: start; background: rgba(255,253,249,.72); border: 1px solid var(--line); border-radius: .85rem; padding: .7rem; position: sticky; top: 1rem; }
.sidebar nav { display: grid; gap: .18rem; }
.sidebar a { border-radius: .48rem; color: var(--ink); padding: .62rem .7rem; text-decoration: none; }
.sidebar a:hover, .sidebar a[aria-current="page"] { background: var(--green-soft); color: var(--green-deep); }
.sidebar a[aria-current="page"] { font-weight: 700; }
.sidebar-note { border-top: 1px solid var(--line); color: var(--muted); font-size: .82rem; margin-top: .7rem; padding: .75rem .7rem .25rem; }
.section-stack { display: grid; gap: 1rem; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: .88rem; box-shadow: var(--shadow); padding: 1.05rem; }
.card-muted { background: rgba(255,253,249,.58); box-shadow: none; }
.card-heading { align-items: flex-start; display: flex; gap: .8rem; justify-content: space-between; margin-bottom: .75rem; }
.card-heading p { color: var(--muted); margin: .2rem 0 0; }
.card-actions { align-items: center; display: flex; flex-wrap: wrap; gap: .55rem; }
.metrics-grid { display: grid; gap: 1rem; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.metric-value { font: 650 1.85rem/1.05 Georgia, serif; margin-top: .45rem; }
.metric-label { color: var(--muted); font-size: .74rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.metric-note, .helper, .meta { color: var(--muted); font-size: .86rem; }
.two-column { display: grid; gap: 1rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.three-column { display: grid; gap: 1rem; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; min-width: 34rem; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: .68rem .42rem; text-align: left; vertical-align: top; }
th { color: var(--muted); font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; }
td strong { display: block; }
.chip { align-items: center; background: var(--green-soft); border-radius: 99px; color: var(--green-deep); display: inline-flex; font-size: .76rem; font-weight: 750; gap: .38rem; padding: .27rem .58rem; white-space: nowrap; }
.chip-dot { background: currentColor; border-radius: 50%; height: .46rem; width: .46rem; }
.chip-warning, .chip-pending { background: var(--gold-soft); color: var(--warn); }
.chip-blocked, .chip-error { background: #f8e4e0; color: var(--danger); }
.chip-neutral, .chip-info { background: #edf0ed; color: var(--muted); }
.chip-detail { font-weight: 500; margin-left: .1rem; }
.state { align-items: flex-start; border: 1px solid var(--line); border-radius: .62rem; display: flex; flex-direction: column; gap: .18rem; padding: .8rem .9rem; }
.state strong { font-size: .92rem; }
.state span { color: var(--muted); font-size: .87rem; }
.state-loading { background: #f7f8f6; }
.state-warning, .state-pending { background: var(--gold-soft); border-color: #ead29d; }
.state-error, .state-blocked { background: #fff1ee; border-color: #e8b8b0; }
.state-success, .state-ok { background: var(--green-soft); border-color: #bfd8c9; }
.notice { border-left: .28rem solid var(--gold); background: var(--gold-soft); border-radius: .45rem; color: #684414; padding: .75rem .9rem; }
.notice strong { display: block; margin-bottom: .15rem; }
.error { border-left-color: var(--danger); background: #fff1ee; color: #762923; }
.form-grid { display: grid; gap: .2rem 1rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.form-grid .full { grid-column: 1 / -1; }
.form-actions { align-items: center; display: flex; flex-wrap: wrap; gap: .6rem; justify-content: space-between; margin-top: 1rem; }
.checkbox-row { align-items: flex-start; display: flex; gap: .6rem; margin: .8rem 0; }
.checkbox-row input { flex: 0 0 auto; margin-top: .25rem; width: 1.1rem; }
.checkbox-row label { font-weight: 500; margin: 0; }
.auth-card { margin: 7vh auto 0; max-width: 31rem; }
.auth-card .brand { background: var(--green); border-radius: .65rem; display: inline-flex; padding: .55rem .7rem; }
.auth-actions { display: grid; gap: .55rem; margin-top: 1.1rem; }
.wizard-layout { display: grid; gap: 1rem; grid-template-columns: 16.5rem minmax(0, 1fr); }
.wizard-steps { align-self: start; background: rgba(255,253,249,.72); border: 1px solid var(--line); border-radius: .85rem; padding: .7rem; }
.step-button { align-items: flex-start; background: transparent; border: 1px solid transparent; color: var(--ink); display: flex; gap: .65rem; justify-content: flex-start; margin: .1rem 0; min-height: auto; padding: .68rem .58rem; text-align: left; width: 100%; }
.step-button:hover { background: var(--green-soft); color: var(--green-deep); }
.step-button[aria-current="step"] { background: var(--green-soft); border-color: #bfd8c9; color: var(--green-deep); }
.step-number { align-items: center; background: #eef1ed; border-radius: 50%; color: var(--muted); display: inline-flex; flex: 0 0 auto; font-size: .76rem; font-weight: 800; height: 1.6rem; justify-content: center; width: 1.6rem; }
.step-button[aria-current="step"] .step-number, .step-button[data-complete="true"] .step-number { background: var(--gold); color: var(--green-deep); }
.step-copy { display: grid; gap: .08rem; }
.step-copy small { color: var(--muted); font-size: .74rem; line-height: 1.35; }
.progress-track { background: #e7ebe6; border-radius: 99px; height: .42rem; overflow: hidden; }
.progress-fill { background: var(--gold); height: 100%; transition: width .2s ease; width: 16.66%; }
.portal-nav { align-items: center; display: flex; flex-wrap: wrap; gap: .25rem .8rem; }
.portal-nav a { color: rgba(255,255,255,.82); text-decoration: none; }
.portal-nav a:hover, .portal-nav a[aria-current="page"] { color: #fff; text-decoration: underline; }
.footer-note { color: var(--muted); font-size: .84rem; margin-top: 1.6rem; }
[hidden] { display: none !important; }
@media (max-width: 960px) {
  .metrics-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .shell-grid, .wizard-layout { grid-template-columns: 1fr; }
  .sidebar, .wizard-steps { position: static; }
  .sidebar nav { display: flex; flex-wrap: wrap; }
  .sidebar-note { display: none; }
}
@media (max-width: 640px) {
  .header-inner, .page, .page-narrow { width: min(100% - 1.3rem, 1180px); }
  .header-inner { align-items: flex-start; flex-direction: column; }
  .header-meta { justify-content: flex-start; }
  .page { padding-block: 1.1rem 2.2rem; }
  .metrics-grid, .two-column, .three-column, .form-grid { grid-template-columns: 1fr; }
  .form-grid .full { grid-column: auto; }
  .page-heading { align-items: flex-start; flex-direction: column; }
  .page-heading-actions, .page-heading-actions .button, .page-heading-actions button { width: 100%; }
  .page-heading-actions .button, .page-heading-actions button { flex: 1 1 100%; }
  .card { padding: .88rem; }
  .wizard-steps { overflow-x: auto; }
  .wizard-steps nav { display: flex; min-width: 45rem; }
  .step-button { width: 13.2rem; }
  .form-actions { align-items: stretch; flex-direction: column-reverse; }
  .form-actions button, .form-actions .button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; }
}
"""


COMMON_JS = r"""
(() => {
  const UI = window.__MEASUREMENT_UI__ || {};
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
  const text = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : esc(value);
  const route = (template, values = {}) => Object.entries(values).reduce(
    (result, [key, value]) => result.replace(`{${key}}`, encodeURIComponent(String(value ?? ""))), template
  );
  const api = async (path, init = {}) => {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...init,
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...(init.headers || {}) }
    });
    const raw = await response.text();
    let payload = {};
    try { payload = raw ? JSON.parse(raw) : {}; } catch (_) { payload = { detail: "The server returned an unreadable response." }; }
    if (!response.ok) throw new Error(String(payload.detail || `Request failed (${response.status})`));
    return payload;
  };
  const setStatus = (node, message, state = "info") => {
    if (!node) return;
    node.className = `state state-${state}`;
    node.dataset.state = state;
    node.setAttribute("role", state === "error" ? "alert" : "status");
    node.innerHTML = `<strong>${esc(state === "error" ? "Action could not be completed" : state === "loading" ? "Loading" : "Status")}</strong><span>${esc(message)}</span>`;
  };
  const number = (value) => value === null || value === undefined || value === "" ? "Unavailable" : new Intl.NumberFormat().format(Number(value));
  const stateFrom = (value) => ({ ok: "success", ready: "success", verified: "success", warning: "warning", pending: "pending", blocked: "blocked", error: "error" }[String(value || "").toLowerCase()] || "info");
  window.MeasurementUI = { UI, esc, text, route, api, setStatus, number, stateFrom };
})();
"""


def _page(
    title: Any,
    body: str,
    *,
    config: Mapping[str, Any] | None = None,
    script: str = "",
    api_routes: Mapping[str, str] | None = None,
) -> str:
    payload = dict(config or {})
    payload.setdefault("apiRoutes", dict(api_routes or API_ROUTES))
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        f"<title>{_e(title)}</title><style>{BASE_CSS}</style></head>"
        f'<body><a class="skip-link button secondary" href="#main-content">Skip to main content</a>{body}'
        f'<script>window.__MEASUREMENT_UI__={_safe_json(payload)};</script><script>{COMMON_JS}{script}</script>'
        "</body></html>"
    )


def _header(
    *,
    subtitle: Any = "",
    role: Any = "",
    user: Any = "",
    home_path: str = "/",
    nav: str = "",
    action: str = "",
) -> str:
    role_html = _status_chip(_text(role).replace("_", " ").title(), "info") if role else ""
    user_html = f'<small>{_e(user)}</small>' if user else ""
    return (
        '<header class="app-header"><div class="header-inner">'
        f'<a class="brand" href="{_e(_safe_path(home_path, "/"))}" aria-label="Measurement and Reporting Platform home">'
        '<span class="brand-mark" aria-hidden="true">M/R</span><span>Measurement &amp; Reporting</span></a>'
        f'<div class="header-meta">{nav}{role_html}{user_html}{action}</div>'
        "</div></header>"
    )


def _sidebar_links(links: Sequence[tuple[str, str, bool]]) -> str:
    return "".join(
        f'<a href="{_e(_safe_path(href, "/"))}"{(" aria-current=\"page\"" if current else "")}>{_e(label)}</a>'
        for label, href, current in links
    )


def _role_note(role: str) -> str:
    notes = {
        "agency_owner": "Full agency control: clients, connections, reports, and team access.",
        "agency_admin": "Operations control: client setup, connections, reports, and sync repair.",
        "agency_analyst": "Read and annotate approved client reporting; setup and access changes are restricted.",
        "client_admin": "Client administrator: approved reporting, goals, and support requests.",
        "client_viewer": "Client viewer: approved reporting is read-only.",
    }
    return notes.get(role, "Access is limited to approved tenant data.")


def login_html(
    context: Mapping[str, Any] | None = None,
    *,
    title: str = "Sign in",
    next_path: str = "/agency",
    auth_mode: str = "cloud_run",
    status: str = "ready",
    error: str = "",
    notice: str = "",
    user_email: str = "",
) -> str:
    """Render the access page for an identity already approved by Cloud Run/IAP.

    Portal access and connector authorization are deliberately separate
    concerns.  This page can only check the established portal identity and
    continue to a safe local path; Google OAuth is rendered by the analytics
    connection step in the onboarding flow.
    """

    context = context or {}
    next_path = _safe_path(_value(context, "next_path", next_path), "/agency")
    auth_mode = _text(_value(context, "auth_mode", auth_mode), "cloud_run")
    error = _text(_value(context, "error", error))
    notice = _text(_value(context, "notice", notice))
    status = _text(_value(context, "status", status), "ready")
    error_html = f'<div class="notice error" role="alert"><strong>Access needs attention</strong>{_e(error)}</div>' if error else ""
    notice_html = f'<div class="notice" role="status"><strong>Next step</strong>{_e(notice)}</div>' if notice else ""
    mode_detail = {
        "cloud_run": "Cloud Run/IAP has already authenticated this request; the server will confirm the approved portal identity.",
        "api_token": "The configured portal access layer has already authenticated this request.",
    }.get(auth_mode, "Your organization controls access to this portal.")
    identity_label = user_email or "Provided by Cloud Run/IAP"
    status_state = stateFromValue(status)
    body = (
        _header(subtitle="Secure access")
        + '<main id="main-content" class="page page-narrow">'
        '<section class="card auth-card" aria-labelledby="login-heading">'
        '<p class="eyebrow">Secure workspace</p>'
        f'<h1 id="login-heading">{_e(title)}</h1>'
        '<p class="lead">Sign in to view only the companies, websites, and reports your role is authorized to access.</p>'
        f'{error_html}{notice_html}'
        '<form id="login-form" class="section-stack" novalidate>'
        f'<div class="card card-muted" aria-label="Established portal identity"><span class="metric-label">Portal identity</span><strong id="login-identity">{_e(identity_label)}</strong>'
        '<p class="helper">Identity comes from the approved Cloud Run/IAP session. This page does not collect credentials or start a provider connection.</p></div>'
        '<div class="auth-actions">'
        '<button id="login-submit" type="submit">Continue with approved portal identity</button>'
        '<button id="login-check" class="secondary" type="button">Check portal access</button>'
        '</div>'
        f'<div id="login-status" class="state state-{_e(status_state)}" role="status" aria-live="polite">'
        f'<strong>Access status</strong><span>{_e(mode_detail)}</span></div>'
        '</form>'
        '<p class="footer-note">No report is generated or sent from this access screen. Analytics-source authorization is available only in the connection step.</p>'
        '</section></main>'
    )
    script = r"""
const { UI, api, setStatus } = window.MeasurementUI;
const form = document.querySelector("#login-form");
const submit = document.querySelector("#login-submit");
const check = document.querySelector("#login-check");
const statusNode = document.querySelector("#login-status");
const setBusy = (busy) => { submit.disabled = busy; check.disabled = busy; submit.setAttribute("aria-busy", String(busy)); };
const trustedPortalPath = (value) => typeof value === "string" && value.startsWith("/") && !value.startsWith("//");
const checkPortalIdentity = async (continueAfterCheck) => {
  setBusy(true); setStatus(statusNode, "Checking the established portal identity.", "loading");
  try {
    const payload = await api(UI.apiRoutes.portal_identity);
    if (payload.approved !== true) throw new Error(String(payload.detail || "An approved portal identity was not confirmed."));
    const identity = payload.identity?.email || payload.email || "approved portal identity";
    setStatus(statusNode, `Approved portal identity confirmed for ${identity}.`, "success");
    if (continueAfterCheck) {
      const destination = UI.nextPath || "/agency";
      if (!trustedPortalPath(destination)) throw new Error("The requested portal path was not trusted.");
      window.location.assign(destination);
    }
  } catch (error) {
    setStatus(statusNode, error.message || "Portal access could not be confirmed.", "error");
  } finally { setBusy(false); }
};
form?.addEventListener("submit", (event) => { event.preventDefault(); checkPortalIdentity(true); });
check?.addEventListener("click", () => checkPortalIdentity(false));
"""
    return _page(
        "Sign in · Measurement & Reporting",
        body,
        config={"nextPath": next_path, "authMode": auth_mode},
        api_routes={"portal_identity": API_ROUTES["portal_identity"]},
        script=script,
    )


def agency_shell_html(
    context: Mapping[str, Any] | None = None,
    *,
    role: str = "agency_owner",
    user: str = "",
    organization_name: str = "Agency workspace",
    clients: Sequence[Mapping[str, Any]] | None = None,
    alerts: Sequence[Mapping[str, Any]] | None = None,
    period: str = "28d",
    active: str = "overview",
    portal_path: str = "/dashboard",
) -> str:
    """Render the agency operations shell with role-aware controls."""

    context = context or {}
    role = _normal_role(_value(context, "role", role))
    user = _text(_value(context, "user", user))
    organization_name = _text(_value(context, "organization_name", organization_name), "Agency workspace")
    period = _text(_value(context, "period", period), "28d")
    clients = _items(_value(context, "clients", clients))
    alerts = _items(_value(context, "alerts", alerts))
    portal_path = _safe_path(_value(context, "portal_path", portal_path), "/dashboard")
    agency_access = role in AGENCY_ROLES
    can_setup = role in {"agency_owner", "agency_admin"}
    can_connect = role in {"agency_owner", "agency_admin"}
    can_manage_access = role == "agency_owner"
    nav_links = [
        ("Portfolio overview", "/agency", active == "overview"),
        ("Reports", "/agency/reports", active == "reports"),
    ]
    if can_setup:
        nav_links.insert(1, ("Client onboarding", "/agency/onboarding", active == "onboarding"))
    if can_manage_access:
        nav_links.append(("Team access", "/agency/access", active == "access"))
    sidebar = _sidebar_links(nav_links)
    if not agency_access:
        body_content = (
            '<section class="card" aria-labelledby="agency-denied-heading">'
            '<p class="eyebrow">Role boundary</p><h1 id="agency-denied-heading">Agency workspace access is restricted</h1>'
            f'<p class="lead">Your current role is <strong>{_e(role.replace("_", " "))}</strong>. Open the approved client portal or ask an agency administrator to update access.</p>'
            f'<div class="card-actions"><a class="button" href="{_e(portal_path)}">Open client reporting</a><a class="button secondary" href="/docs">View access documentation</a></div>'
            '<div class="notice" role="status"><strong>Tenant boundary</strong>Other clients, agency operations, and setup controls are not rendered for this role.</div>'
            '</section>'
        )
    else:
        setup_action = f'<a class="button" href="/agency/onboarding">Set up a client</a>' if can_setup else ""
        access_action = '<a class="button secondary" href="/agency/access">Manage team access</a>' if can_manage_access else ""
        alerts_html = ""
        if alerts:
            alert_rows = "".join(
                f'<li><strong>{_e(_value(item, "title", "Attention required"))}</strong> '
                f'{_e(_value(item, "detail", "Review this client."))}</li>'
                for item in alerts
            )
            alerts_html = f'<div class="notice" role="status"><strong>Clients requiring attention</strong><ul>{alert_rows}</ul></div>'
        elif alerts is not None:
            alerts_html = _state_card("No active alerts", "No stored client action is currently reported.", "success")
        initial_rows = ""
        for item in clients:
            initial_rows += (
                f'<tr><td><strong>{_e(_value(item, "company", _value(item, "companyName", "Client")))}</strong>'
                f'<span class="meta">{_e(_value(item, "website", _value(item, "domain", "Website pending")))}</span></td>'
                f'<td>{_status_chip(_value(item, "health", "Pending"), _value(item, "healthState", "pending"))}</td>'
                f'<td>{_e(_value(item, "lastCompleteDate", "Not synced"))}</td>'
                f'<td>{_e(_value(item, "actionRequired", "No action recorded"))}</td></tr>'
            )
        initial_state = (
            f'<div class="table-wrap" id="portfolio-table-wrap"><table><caption class="sr-only">Client portfolio health</caption><thead><tr><th scope="col">Client</th><th scope="col">Health</th><th scope="col">Last complete</th><th scope="col">Action</th></tr></thead><tbody>{initial_rows}</tbody></table></div>'
            if clients
            else '<div id="portfolio-table-wrap" hidden class="table-wrap"></div>'
        )
        connection_section = (
            '<section class="card" aria-labelledby="connection-heading"><div class="card-heading"><div><h2 id="connection-heading">Analytics access</h2><p>Read-only access and intended-property assignment.</p></div>'
            + (_status_chip("Operator action required", "pending") if not can_connect else _status_chip("Read-only control", "info"))
            + '</div><div id="oauth-status" class="state state-loading" role="status" aria-live="polite"><strong>Loading</strong><span>Checking configured OAuth status.</span></div>'
            + ('<div class="card-actions"><button id="oauth-connect" type="button">Connect approved Analytics identity</button><a class="button secondary" href="/docs">Review connection contract</a></div>' if can_connect else '<div class="notice"><strong>Not available for this role</strong>Ask an agency administrator to complete a connection.</div>')
            + '<p id="oauth-message" class="helper" role="status"></p></section>'
        )
        access_section = (
            '<section class="card" aria-labelledby="access-heading"><div class="card-heading"><div><h2 id="access-heading">Team access</h2><p>Membership changes are tenant-scoped and audited.</p></div>'
            + _status_chip("Owner only", "info")
            + '</div><p class="helper">Use the documented membership API from an approved owner session. The UI never displays or stores credentials.</p><div class="card-actions"><a class="button secondary" href="/agency/access">Open access review</a></div></section>'
            if can_manage_access
            else ""
        )
        body_content = (
            f'<div class="page-heading"><div><p class="eyebrow">Agency operations</p><h1>Portfolio overview</h1><p class="lead">See which approved client websites are healthy, stale, incomplete, or waiting on an external gate.</p></div><div class="page-heading-actions">{setup_action}{access_action}</div></div>'
            f'{alerts_html}<section class="card" aria-labelledby="portfolio-heading"><div class="card-heading"><div><h2 id="portfolio-heading">Client portfolio</h2><p>Stored reporting snapshots remain the source for dashboard views.</p></div><label class="meta" for="agency-period">Period<select id="agency-period"><option value="7d"{" selected" if period == "7d" else ""}>Last 7 days</option><option value="28d"{" selected" if period == "28d" else ""}>Last 28 days</option><option value="this_month"{" selected" if period == "this_month" else ""}>This month</option><option value="last_month"{" selected" if period == "last_month" else ""}>Last month</option><option value="90d"{" selected" if period == "90d" else ""}>Last 90 days</option></select></label></div>'
            '<div id="portfolio-state" class="state state-loading" role="status" aria-live="polite"><strong>Loading portfolio</strong><span>Reading authorized client summaries.</span></div>'
            f'{initial_state}</section><div class="two-column">{connection_section}{access_section}</div>'
            '<section class="card" aria-labelledby="delivery-heading"><div class="card-heading"><div><h2 id="delivery-heading">Report delivery state</h2><p>Generation can be reviewed without contacting a client.</p></div>'
            + _status_chip("Dry-run / operator review", "pending")
            + '</div><div class="notice"><strong>No real client report is sent by this UI.</strong>Provider acceptance, recipient approval, and recurring dispatch remain separate production gates.</div></section>'
        )
    body = (
        _header(subtitle=organization_name, role=role, user=user, home_path="/agency")
        + '<main id="main-content" class="page">'
        + ('<div class="shell-grid"><aside class="sidebar" aria-label="Agency navigation"><nav>' + sidebar + f'</nav><p class="sidebar-note">{_e(_role_note(role))}</p></aside><div class="section-stack">' if agency_access else '')
        + body_content
        + ('</div></div>' if agency_access else '')
        + '</main>'
    )
    script = r"""
const { UI, api, route, esc, number, setStatus, stateFrom } = window.MeasurementUI;
const role = UI.role || "agency_analyst";
const portfolioState = document.querySelector("#portfolio-state");
const tableWrap = document.querySelector("#portfolio-table-wrap");
const tableBody = tableWrap?.querySelector("tbody");
const period = document.querySelector("#agency-period");
const renderRows = (payload) => {
  const rows = Array.isArray(payload) ? payload : (payload.clients || payload.rows || payload.websites || []);
  if (!rows.length) { tableWrap.hidden = true; setStatus(portfolioState, "No authorized client sites are configured yet. Start a client onboarding draft when your role permits it.", "pending"); return; }
  tableWrap.hidden = false;
  tableBody.innerHTML = rows.map((item) => {
    const company = item.company || item.companyName || item.name || "Client";
    const website = item.website || item.domain || "Website pending";
    const health = item.health || item.measurementHealth || "Pending";
    const healthState = stateFrom(item.healthState || item.state || "pending");
    return `<tr><td><strong>${esc(company)}</strong><span class="meta">${esc(website)}</span></td><td><span class="chip chip-${healthState}"><span class="chip-dot" aria-hidden="true"></span>${esc(health)}</span></td><td>${text(item.lastCompleteDate, "Not synced")}</td><td>${text(item.actionRequired, "No action recorded")}</td></tr>`;
  }).join("");
  setStatus(portfolioState, `${rows.length} authorized client site${rows.length === 1 ? "" : "s"} loaded.`, "success");
};
const loadPortfolio = async () => {
  if (!portfolioState || !UI.apiRoutes?.portfolio) return;
  setStatus(portfolioState, "Reading authorized client summaries.", "loading");
  try { renderRows(await api(`${UI.apiRoutes.portfolio}?period=${encodeURIComponent(period?.value || "28d")}`)); }
  catch (error) { tableWrap.hidden = true; setStatus(portfolioState, error.message || "Portfolio data is unavailable.", "error"); }
};
period?.addEventListener("change", loadPortfolio);
loadPortfolio();
const oauthStatus = document.querySelector("#oauth-status");
const oauthMessage = document.querySelector("#oauth-message");
const oauthButton = document.querySelector("#oauth-connect");
const loadOAuth = async () => {
  if (!oauthStatus || !UI.apiRoutes?.oauth_status) return;
  setStatus(oauthStatus, "Checking configured OAuth status.", "loading");
  try { const payload = await api(UI.apiRoutes.oauth_status); const state = payload.state || payload.status || "configured"; setStatus(oauthStatus, `Google authorization is ${state}. Verify the approved property before assigning it.`, stateFrom(state)); }
  catch (error) { setStatus(oauthStatus, error.message || "OAuth status is unavailable.", "error"); }
};
oauthButton?.addEventListener("click", async () => {
  oauthButton.disabled = true; oauthButton.setAttribute("aria-busy", "true");
  try { const payload = await api(UI.apiRoutes.oauth_authorize, { method: "POST" }); const destination = payload.authorizationUrl || payload.url; if (!destination || !(destination.startsWith("/") && !destination.startsWith("//") || destination.startsWith("https://accounts.google.com/"))) throw new Error("Authorization destination was not trusted."); window.location.assign(destination); }
  catch (error) { if (oauthMessage) oauthMessage.textContent = error.message || "Connection could not be started."; }
  finally { oauthButton.disabled = false; oauthButton.removeAttribute("aria-busy"); }
});
loadOAuth();
"""
    return _page(
        "Agency portfolio · Measurement & Reporting",
        body,
        config={"role": role, "organization": organization_name, "period": period, "portalPath": portal_path},
        script=script,
    )


def client_onboarding_html(
    context: Mapping[str, Any] | None = None,
    *,
    role: str = "agency_admin",
    user: str = "",
    company: Any = None,
    site: Any = None,
    step: int = 1,
    completed_steps: Sequence[int] | None = None,
    gates: Mapping[str, Any] | None = None,
    workflow_id: str = "",
    contract_slug: str = "local_service_v1",
    connection_id: str = "",
    replay_job_id: str = "",
    client_portal_path: str = "/dashboard",
) -> str:
    """Render the six-step, operator-led client onboarding wizard."""

    context = context or {}
    role = _normal_role(_value(context, "role", role))
    user = _text(_value(context, "user", user))
    company = _value(context, "company", company) or {}
    site = _value(context, "site", site) or {}
    step = int(_value(context, "step", step) or 1)
    step = max(1, min(6, step))
    completed = {int(value) for value in (_value(context, "completed_steps", completed_steps) or []) if str(value).isdigit()}
    gates = _value(context, "gates", gates) or {}
    workflow_id = _text(_value(context, "workflow_id", workflow_id))
    contract_slug = _text(_value(context, "contract_slug", contract_slug), "local_service_v1")
    connection_id = _text(_value(context, "connection_id", connection_id))
    replay_job_id = _text(_value(context, "replay_job_id", replay_job_id))
    client_portal_path = _safe_path(_value(context, "client_portal_path", client_portal_path), "/dashboard")
    company_name = _text(_value(company, "name", _value(company, "company", "New client")), "New client")
    company_id = _text(_value(company, "id", _value(company, "company_id", "")))
    site_id = _text(_value(site, "id", _value(site, "site_id", "")))
    domain = _text(_value(site, "domain", _value(site, "canonical_domain", "")))
    timezone = _text(_value(site, "timezone", _value(site, "property_timezone", "")))
    governance = _text(_value(gates, "governance", _value(site, "governance_status", "pending")), "pending")
    contract = _text(_value(gates, "contract", "pending"), "pending")
    analytics = _text(_value(gates, "analytics", "not_configured"), "not_configured")
    sync = _text(_value(gates, "sync", "pending"), "pending")
    access = _text(_value(gates, "access", "pending"), "pending")
    agency_access = role in AGENCY_ROLES
    if not agency_access:
        body_content = (
            '<section class="card" aria-labelledby="onboarding-denied-heading"><p class="eyebrow">Role boundary</p>'
            '<h1 id="onboarding-denied-heading">Client onboarding is agency-only</h1>'
            f'<p class="lead">Your current role is <strong>{_e(role.replace("_", " "))}</strong>. This wizard is not rendered with setup controls for client users.</p>'
            '<div class="notice" role="status"><strong>Nothing was changed</strong>Ask an agency administrator to complete the setup and invite client access.</div></section>'
        )
    else:
        step_buttons = ""
        for number, slug, label, description in ONBOARDING_STEPS:
            current = number == step
            complete = number in completed
            step_buttons += (
                f'<button class="step-button" type="button" data-step-button="{number}" aria-current="{"step" if current else "false"}" data-complete="{"true" if complete else "false"}">'
                f'<span class="step-number" aria-hidden="true">{number}</span><span class="step-copy"><strong>{_e(label)}</strong><small>{_e(description)}</small></span></button>'
            )
        panels = []
        panels.append(
            '<section class="card wizard-panel" data-step-panel="1" aria-labelledby="step-1-heading">'
            '<div class="card-heading"><div><p class="eyebrow">Step 1 of 6</p><h2 id="step-1-heading">Company &amp; site</h2><p>Establish the tenant and website reporting boundary before connecting data.</p></div>'
            + _status_chip("Draft", "pending")
            + '</div><form id="company-site-form" class="form-grid" novalidate>'
            + f'<div><label for="company-name">Company name</label><input id="company-name" name="company" required value="{_e(company_name if company_name != "New client" else "")}" autocomplete="organization"></div>'
            + f'<div><label for="company-id">Company ID <span class="meta">(if already provisioned)</span></label><input id="company-id" name="companyId" value="{_e(company_id)}" autocomplete="off"></div>'
            + f'<div><label for="site-domain">Canonical domain</label><input id="site-domain" name="domain" type="url" required placeholder="https://client.example" value="{_e(domain)}" autocomplete="url"></div>'
            + f'<div><label for="site-id">Website ID <span class="meta">(if already provisioned)</span></label><input id="site-id" name="siteId" value="{_e(site_id)}" autocomplete="off"></div>'
            + f'<div class="full"><label for="site-timezone">Property time zone</label><input id="site-timezone" name="timezone" placeholder="America/New_York" value="{_e(timezone)}"></div>'
            + '<div class="full form-actions"><span id="company-site-status" class="helper" role="status">The server creates or resumes a tenant-scoped workflow; this page does not save a local credential draft.</span><button type="submit">Create or resume workflow</button></div></form></section>'
        )
        panels.append(
            '<section class="card wizard-panel" data-step-panel="2" aria-labelledby="step-2-heading" hidden>'
            '<div class="card-heading"><div><p class="eyebrow">Step 2 of 6</p><h2 id="step-2-heading">Privacy &amp; contract</h2><p>Analytics is default-deny for unapproved routes and incomplete healthcare governance.</p></div>'
            + _status_chip("Review required", "pending")
            + '</div><div class="two-column"><div class="card card-muted"><h3>Governance gate</h3><p class="meta">Current state</p>'
            + _status_chip(governance.replace("_", " ").title(), governance)
            + '<p class="helper">Client/legal approval, route eligibility, consent behavior, and the authorized domain must be documented before production activation.</p></div><div class="card card-muted"><h3>Measurement contract</h3><p class="meta">Current state</p>'
            + _status_chip(contract.replace("_", " ").title(), contract)
            + '<p class="helper">Use the versioned contract and privacy fixtures. Do not invent a booked appointment or patient outcome from GA4 intent.</p></div></div>'
            + '<form id="governance-form" class="form-grid" novalidate><div class="checkbox-row full"><input id="privacy-reviewed" type="checkbox"><label for="privacy-reviewed">I have reviewed route eligibility and the client-approved privacy decision.</label></div><div class="checkbox-row full"><input id="contract-reviewed" type="checkbox"><label for="contract-reviewed">I have reviewed the active measurement-contract version and its data-quality caveats.</label></div><div><label for="governance-reference">Governance reference</label><input id="governance-reference" name="governanceReference" autocomplete="off" placeholder="approval-reference"></div><div><label for="consent-reference">Consent reference</label><input id="consent-reference" name="consentReference" autocomplete="off" placeholder="consent-reference"></div><div class="full form-actions"><span id="governance-status" class="helper" role="status">A review checkbox is not approval until the server records its response.</span><button type="submit">Save governance state</button></div></form><div class="form-actions"><a class="button secondary" href="/docs">Open contract documentation</a><button id="governance-continue" type="button" disabled>Continue to analytics connection</button></div></section>'
        )
        panels.append(
            '<section class="card wizard-panel" data-step-panel="3" aria-labelledby="step-3-heading" hidden>'
            '<div class="card-heading"><div><p class="eyebrow">Step 3 of 6</p><h2 id="step-3-heading">Analytics connection</h2><p>Connect read-only access, discover properties, and assign only the approved website boundary.</p></div>'
            + _status_chip(analytics.replace("_", " ").title(), analytics)
            + '</div><div id="analytics-connection-status" class="state state-pending" role="status" aria-live="polite"><strong>External gate</strong><span>OAuth production approval, authorized domain, legal pages, and intended-property access may still be incomplete.</span></div>'
            + f'<p class="helper">Connection reference: <code>{_e(connection_id or "not connected")}</code></p><div class="card-actions"><button id="analytics-connect" type="button">Start read-only Analytics connection</button><button id="analytics-defer" class="secondary" type="button">Defer until access is approved</button><a class="button secondary" href="/docs">Review OAuth requirements</a></div>'
            + '<form id="property-assignment-form" class="form-grid" novalidate><div><label for="property-id">Approved property ID</label><input id="property-id" name="propertyId" placeholder="properties/…" autocomplete="off"></div><div><label for="stream-id">Web stream ID <span class="meta">(if applicable)</span></label><input id="stream-id" name="streamId" autocomplete="off"></div><div class="full form-actions"><span id="property-status" class="helper" role="status">Assignment requires an authorized connection and server-side tenant validation.</span><button type="submit">Assign approved property</button></div></form></section>'
        )
        panels.append(
            '<section class="card wizard-panel" data-step-panel="4" aria-labelledby="step-4-heading" hidden>'
            '<div class="card-heading"><div><p class="eyebrow">Step 4 of 6</p><h2 id="step-4-heading">First sync</h2><p>Confirm that stored snapshots, health, freshness, and quality metadata are available.</p></div>'
            + _status_chip(sync.replace("_", " ").title(), sync)
            + '</div><div id="sync-status" class="state state-pending" role="status" aria-live="polite"><strong>Sync verification pending</strong><span>Run a bounded status check after the approved assignment exists.</span></div>'
            + f'<p class="helper">Website ID: <code>{_e(site_id or "not assigned")}</code>. The browser requests only the resumable first-sync state; internal scheduler and task endpoints are never called here.</p><div class="card-actions"><button id="sync-request" type="button"{(" disabled" if not site_id else "")}>Request first sync</button><button id="sync-check" class="secondary" type="button"{(" disabled" if not site_id else "")}>Check first-sync state</button></div></section>'
        )
        panels.append(
            '<section class="card wizard-panel" data-step-panel="5" aria-labelledby="step-5-heading" hidden>'
            '<div class="card-heading"><div><p class="eyebrow">Step 5 of 6</p><h2 id="step-5-heading">Client access</h2><p>Invite the smallest appropriate client role after tenant isolation and approved data are ready.</p></div>'
            + _status_chip(access.replace("_", " ").title(), access)
            + '</div><div class="notice"><strong>Access is not a report delivery.</strong>Inviting a user grants approved portal access only. No email report is sent by this wizard.</div>'
            + '<form id="client-access-form" class="form-grid" novalidate><div class="full"><label for="client-email">Client portal email</label><input id="client-email" name="email" type="email" required autocomplete="email" placeholder="client@example.com"></div><div><label for="client-role">Client role</label><select id="client-role" name="role"><option value="client_admin">Client administrator</option><option value="client_viewer">Client viewer</option></select></div><div><label for="authorization-reference">Authorization reference</label><input id="authorization-reference" name="authorizationReference" required autocomplete="off" placeholder="access-approval"></div><div class="full form-actions"><span id="client-access-status" class="helper" role="status">This grants portal access only. It is not a report recipient and does not send a report.</span><button type="submit">Save client portal access</button></div></form></section>'
        )
        panels.append(
            '<section class="card wizard-panel" data-step-panel="6" aria-labelledby="step-6-heading" hidden>'
            '<div class="card-heading"><div><p class="eyebrow">Step 6 of 6</p><h2 id="step-6-heading">Handoff</h2><p>Review readiness, state the remaining gates, and share the portal entry point.</p></div>'
            + _status_chip("Operator review", "pending")
            + '</div><div class="three-column"><div class="card card-muted"><h3>Data</h3>' + _status_chip(sync.replace("_", " ").title(), sync) + '<p class="helper">Stored sync and quality state.</p></div><div class="card card-muted"><h3>Access</h3>' + _status_chip(access.replace("_", " ").title(), access) + '<p class="helper">Tenant-scoped membership.</p></div><div class="card card-muted"><h3>Delivery</h3>' + _status_chip("Dry-run only", "pending") + '<p class="helper">No client recipient has been contacted.</p></div></div>'
            + f'<div class="notice"><strong>External gates stay explicit.</strong>OAuth production publishing, source credentials, legal/privacy approval, and inbox delivery need independent evidence. <a href="{_e(client_portal_path)}">Open the client portal shell</a> for review.</div>'
            + '<div class="checkbox-row"><input id="handoff-reviewed" type="checkbox"><label for="handoff-reviewed">I reviewed the readiness states and understand that a server response is required before handoff can be marked ready.</label></div><p id="handoff-status" class="helper" role="status">Handoff remains a review state until the server returns the current checklist.</p><div class="form-actions"><button type="button" data-prev-step="5" class="secondary">Back to client access</button><button id="handoff-complete" type="button" disabled>Check handoff readiness</button></div></section>'
        )
        panels_html = "".join(panels)
        body_content = (
            f'<div class="page-heading"><div><p class="eyebrow">Agency setup</p><h1>Client onboarding wizard</h1><p class="lead">A repeatable, auditable path from company/site setup to a reviewable client handoff.</p></div><div class="page-heading-actions"><a class="button secondary" href="/agency">Back to portfolio</a></div></div>'
            '<div class="progress-track" aria-label="Onboarding progress"><div id="wizard-progress" class="progress-fill"></div></div><p id="wizard-progress-label" class="helper" role="status">Step ' + str(step) + ' of 6</p>'
            + f'<div class="wizard-layout"><aside class="wizard-steps" aria-label="Onboarding steps"><nav>{step_buttons}</nav></aside><div class="section-stack">{panels_html}</div></div>'
    )
    body = _header(subtitle="Client onboarding", role=role, user=user, home_path="/agency") + '<main id="main-content" class="page">' + body_content + '</main>'
    script = r"""
const { UI, api, route, setStatus, stateFrom, esc } = window.MeasurementUI;
const panels = [...document.querySelectorAll("[data-step-panel]")];
const buttons = [...document.querySelectorAll("[data-step-button]")];
let currentStep = Number(UI.step || 1);
let workflowId = String(UI.workflowId || "");
const makeIdempotencyKey = (prefix) => {
  const suffix = typeof crypto?.randomUUID === "function" ? crypto.randomUUID().replaceAll("-", "") : `${Date.now()}${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${suffix}`.slice(0, 120);
};
const workflowKey = UI.idempotencyKey || makeIdempotencyKey("onboarding");
const progress = document.querySelector("#wizard-progress");
const progressLabel = document.querySelector("#wizard-progress-label");
const setStep = (value) => {
  currentStep = Math.max(1, Math.min(6, Number(value) || 1));
  panels.forEach((panel) => { panel.hidden = Number(panel.dataset.stepPanel) !== currentStep; });
  buttons.forEach((button) => { button.setAttribute("aria-current", Number(button.dataset.stepButton) === currentStep ? "step" : "false"); });
  if (progress) progress.style.width = `${(currentStep / 6) * 100}%`;
  if (progressLabel) progressLabel.textContent = `Step ${currentStep} of 6`;
  const heading = document.querySelector(`[data-step-panel="${currentStep}"]`)?.querySelector("h2");
  heading?.setAttribute("tabindex", "-1"); heading?.focus?.({ preventScroll: true });
};
buttons.forEach((button) => button.addEventListener("click", () => setStep(button.dataset.stepButton)));
document.querySelectorAll("[data-prev-step]").forEach((button) => button.addEventListener("click", () => setStep(button.dataset.prevStep)));
const workflowRoute = (key) => workflowId && UI.apiRoutes?.[key] ? route(UI.apiRoutes[key], { workflow_id: workflowId }) : "";
const requireWorkflow = (node) => {
  if (workflowId) return true;
  setStatus(node, "Create or resume the server workflow before using this step.", "blocked");
  return false;
};
const stateValue = (payload, fallback = "pending") => String(payload?.state || payload?.status || fallback).toLowerCase();
const applyChecklist = (payload) => {
  if (!payload || typeof payload !== "object") return;
  const checklist = Array.isArray(payload.checklist) ? payload.checklist : [];
  const byKey = Object.fromEntries(checklist.map((item) => [item.key, item]));
  const gate = (key, node, label) => {
    const item = byKey[key];
    if (!item || !node) return;
    const state = stateValue(item);
    setStatus(node, `${label}: ${state}.`, stateFrom(state));
  };
  gate("governance", document.querySelector("#governance-status"), "Server governance state");
  gate("ga4_connection", document.querySelector("#analytics-connection-status"), "Server Analytics connection state");
  gate("first_sync", document.querySelector("#sync-status"), "Server first-sync state");
  gate("client_access", document.querySelector("#client-access-status"), "Server client-access state");
  const governanceReady = Boolean(byKey.governance?.complete && byKey.consent?.complete);
  const continueButton = document.querySelector("#governance-continue");
  if (continueButton) continueButton.disabled = !governanceReady;
  const handoff = payload.handoff || {};
  const handoffNode = document.querySelector("#handoff-status");
  if (handoffNode && Object.keys(handoff).length) {
    const ready = handoff.ready === true;
    setStatus(handoffNode, ready ? "The server reports that handoff is ready." : "The server reports that handoff remains pending.", ready ? "success" : "pending");
  }
};
const refreshWorkflow = async () => {
  if (!requireWorkflow(document.querySelector("#company-site-status"))) return null;
  const workflow = await api(workflowRoute("onboarding_workflow"));
  const checklist = await api(workflowRoute("onboarding_checklist"));
  applyChecklist(checklist);
  return { workflow, checklist };
};
const setWorkflowFromResponse = (payload) => {
  const value = payload?.workflowId || payload?.workflow?.workflowId;
  if (!value) throw new Error("The server did not return a workflow identifier.");
  workflowId = String(value); UI.workflowId = workflowId;
};
const canonicalDomain = (value) => String(value || "").trim().replace(/^https?:\/\//i, "").replace(/\/+$/, "");
const publicId = (value, prefix) => {
  const normalized = String(value || "").trim().toLowerCase().replace(/[^a-z0-9_.:-]+/g, "-").replace(/^-+|-+$/g, "");
  return (normalized || `${prefix}-${Date.now()}`).slice(0, 120);
};
document.querySelector("#company-site-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const node = document.querySelector("#company-site-status");
  const data = Object.fromEntries(new FormData(event.currentTarget));
  const companyName = String(data.company || "").trim();
  const domain = canonicalDomain(data.domain);
  if (!companyName || !domain) { setStatus(node, "Company name and canonical domain are required.", "error"); return; }
  const payload = { idempotencyKey: workflowKey, companyId: String(data.companyId || "").trim() || publicId(companyName, "client"), companyName, websiteId: String(data.siteId || "").trim() || publicId(domain, "site"), canonicalDomain: domain, contractSlug: UI.contractSlug || "local_service_v1" };
  setStatus(node, "Creating or resuming the tenant-scoped workflow.", "loading");
  event.currentTarget.querySelector("button[type=submit]").disabled = true;
  try {
    const response = await api(UI.apiRoutes.onboarding_create, { method: "POST", body: JSON.stringify(payload) });
    setWorkflowFromResponse(response);
    await refreshWorkflow();
    setStatus(node, "Server workflow created or resumed.", "success");
    setStep(2);
  } catch (error) { setStatus(node, error.message || "The server could not create or resume the workflow.", "error"); }
  finally { event.currentTarget.querySelector("button[type=submit]").disabled = false; }
});
document.querySelector("#governance-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const node = document.querySelector("#governance-status");
  if (!requireWorkflow(node)) return;
  const data = Object.fromEntries(new FormData(event.currentTarget));
  const privacyReviewed = document.querySelector("#privacy-reviewed")?.checked === true;
  const contractReviewed = document.querySelector("#contract-reviewed")?.checked === true;
  const approved = privacyReviewed && contractReviewed;
  const governanceReference = String(data.governanceReference || "").trim();
  const consentReference = String(data.consentReference || "").trim();
  if (approved && (!governanceReference || !consentReference)) { setStatus(node, "Approval references are required before recording an approved state.", "error"); return; }
  setStatus(node, "Recording governance and consent state with the server.", "loading");
  try {
    await api(workflowRoute("onboarding_governance"), { method: "POST", body: JSON.stringify({ governanceStatus: approved ? "approved" : "requires_review", consentStatus: privacyReviewed ? "approved" : "pending_client_consent", governanceReference: governanceReference || null, consentReference: consentReference || null }) });
    await refreshWorkflow();
    setStatus(node, "Server governance state saved.", "success");
    setStep(3);
  } catch (error) { setStatus(node, error.message || "Governance state could not be saved.", "error"); }
});
document.querySelector("#governance-continue")?.addEventListener("click", () => setStep(3));
const connect = document.querySelector("#analytics-connect");
connect?.addEventListener("click", async () => {
  const node = document.querySelector("#analytics-connection-status");
  if (!requireWorkflow(node)) return;
  connect.disabled = true; setStatus(node, "Starting the read-only Analytics connection.", "loading");
  try {
    const payload = await api(UI.apiRoutes.oauth_authorize, { method: "POST" });
    const destination = payload.authorizationUrl || payload.url;
    if (!destination || !(destination.startsWith("/") && !destination.startsWith("//") || destination.startsWith("https://accounts.google.com/"))) throw new Error("Authorization destination was not trusted.");
    window.location.assign(destination);
  } catch (error) { setStatus(node, error.message || "Analytics connection could not be started.", "error"); }
  finally { connect.disabled = false; }
});
const recordConnection = async (connection) => {
  const node = document.querySelector("#analytics-connection-status");
  if (!requireWorkflow(node)) return false;
  setStatus(node, "Recording the source connection state with the server.", "loading");
  try {
    await api(workflowRoute("onboarding_connections"), { method: "POST", body: JSON.stringify(connection) });
    await refreshWorkflow();
    setStatus(node, "Server source connection state saved.", "success");
    return true;
  } catch (error) { setStatus(node, error.message || "Source connection state could not be saved.", "error"); return false; }
};
document.querySelector("#analytics-defer")?.addEventListener("click", () => recordConnection({ idempotencyKey: makeIdempotencyKey("ga4-deferred"), connectionKind: "ga4", mode: "deferred", deferReason: "Approved Analytics access has not been supplied yet." }));
document.querySelector("#property-assignment-form")?.addEventListener("submit", async (event) => {
  event.preventDefault(); const node = document.querySelector("#property-status");
  if (!requireWorkflow(node) || !UI.connectionId) { setStatus(node, "Connect and verify an approved OAuth connection before assigning a property.", "blocked"); return; }
  const data = Object.fromEntries(new FormData(event.currentTarget)); setStatus(node, "Assigning only after server-side authorization checks.", "loading");
  if (!/^\d+$/.test(String(data.propertyId || "")) || !/^\d+$/.test(String(data.streamId || ""))) { setStatus(node, "Property and stream IDs must be numeric provider identifiers.", "error"); return; }
  try {
    await api(route(UI.apiRoutes.oauth_assign, { connection_id: UI.connectionId }), { method: "POST", body: JSON.stringify({ websiteId: UI.siteId, propertyId: data.propertyId, streamId: data.streamId }) });
    const recorded = await recordConnection({ idempotencyKey: makeIdempotencyKey("ga4-registered"), connectionKind: "ga4", mode: "registered", credentialType: "oauth", credentialReference: `oauth_connection:${UI.connectionId}`, externalPropertyId: String(data.propertyId), externalStreamId: String(data.streamId) });
    if (recorded) { setStatus(node, "Approved property and server source connection state saved.", "success"); setStep(4); }
  }
  catch (error) { setStatus(node, error.message || "Property assignment could not be completed.", "error"); }
});
const syncNode = document.querySelector("#sync-status");
const firstSync = async (action) => {
  if (!requireWorkflow(syncNode)) return;
  setStatus(syncNode, action === "request" ? "Requesting first-sync state from the server." : "Checking first-sync state with the server.", "loading");
  try {
    const payload = action === "request"
      ? await api(workflowRoute("onboarding_first_sync"), { method: "POST", body: JSON.stringify({ action, idempotencyKey: makeIdempotencyKey("first-sync") }) })
      : await api(workflowRoute("onboarding_first_sync"));
    const firstSyncPayload = payload.firstSync || payload;
    const state = stateValue(firstSyncPayload);
    setStatus(syncNode, `Server first-sync state: ${state}.`, stateFrom(state));
    if (action === "request") applyChecklist((await api(workflowRoute("onboarding_checklist"))));
  } catch (error) { setStatus(syncNode, error.message || "First-sync state is unavailable.", "error"); }
};
document.querySelector("#sync-request")?.addEventListener("click", () => firstSync("request"));
document.querySelector("#sync-check")?.addEventListener("click", () => firstSync("check"));
document.querySelector("#client-access-form")?.addEventListener("submit", async (event) => {
  event.preventDefault(); const node = document.querySelector("#client-access-status");
  if (!requireWorkflow(node)) return;
  const data = Object.fromEntries(new FormData(event.currentTarget));
  setStatus(node, "Saving tenant-scoped client portal access with the server.", "loading");
  try {
    await api(workflowRoute("onboarding_memberships"), { method: "POST", body: JSON.stringify({ email: data.email, role: data.role, authorizationReference: data.authorizationReference }) });
    await refreshWorkflow();
    setStatus(node, "Server client portal access saved; no report recipient was created.", "success");
    setStep(6);
  } catch (error) { setStatus(node, error.message || "Client portal access could not be saved.", "error"); }
});
const handoff = document.querySelector("#handoff-reviewed"); const handoffButton = document.querySelector("#handoff-complete"); const handoffStatus = document.querySelector("#handoff-status"); handoff?.addEventListener("change", () => { handoffButton.disabled = !handoff.checked; }); handoffButton?.addEventListener("click", async () => {
  if (!requireWorkflow(handoffStatus)) return;
  handoffButton.disabled = true; setStatus(handoffStatus, "Checking the server handoff checklist.", "loading");
  try {
    const payload = await api(workflowRoute("onboarding_handoff"));
    const handoffState = payload.handoff || {};
    const ready = handoffState.ready === true;
    setStatus(handoffStatus, ready ? "The server reports that handoff is ready." : "The server reports that handoff remains pending; review the listed gates.", ready ? "success" : "pending");
  } catch (error) { setStatus(handoffStatus, error.message || "Handoff readiness is unavailable.", "error"); }
  finally { handoffButton.disabled = !handoff.checked; }
});
if (workflowId) refreshWorkflow().catch((error) => setStatus(document.querySelector("#company-site-status"), error.message || "The saved workflow could not be loaded.", "error"));
setStep(currentStep);
"""
    return _page(
        "Client onboarding · Measurement & Reporting",
        body,
        config={"role": role, "step": step, "siteId": site_id, "companyId": company_id, "connectionId": connection_id, "workflowId": workflow_id, "contractSlug": contract_slug, "clientPortalPath": client_portal_path},
        api_routes=ONBOARDING_API_ROUTES,
        script=script,
    )


def client_portal_html(
    context: Mapping[str, Any] | None = None,
    *,
    role: str = "client_viewer",
    user: str = "",
    company: str = "Client workspace",
    company_id: str = "",
    website_id: str = "",
    domain: str = "",
    period: str = "28d",
    health: Mapping[str, Any] | None = None,
    reports: Sequence[Mapping[str, Any]] | None = None,
    goals: Sequence[Mapping[str, Any]] | None = None,
    sources: Sequence[Mapping[str, Any]] | None = None,
    access_state: str = "approved",
    support_email: str = "",
) -> str:
    """Render the tenant-scoped client portal shell."""

    context = context or {}
    role = _normal_role(_value(context, "role", role))
    user = _text(_value(context, "user", user))
    company = _text(_value(context, "company", company), "Client workspace")
    company_id = _text(_value(context, "company_id", company_id))
    website_id = _text(_value(context, "website_id", website_id))
    domain = _text(_value(context, "domain", domain))
    period = _text(_value(context, "period", period), "28d")
    health = _value(context, "health", health) or {}
    reports = _items(_value(context, "reports", reports))
    goals = _items(_value(context, "goals", goals))
    sources = _items(_value(context, "sources", sources))
    access_state = _text(_value(context, "access_state", access_state), "approved")
    support_email = _text(_value(context, "support_email", support_email))
    can_edit = role == "client_admin"
    health_state = _text(_value(health, "state", "pending"), "pending")
    health_detail = _text(_value(health, "detail", "Health checks will appear after the first stored sync."), "Health checks will appear after the first stored sync.")
    nav = '<nav class="portal-nav" aria-label="Client navigation"><a href="#overview" aria-current="page">Overview</a><a href="#reports">Reports</a><a href="#goals">Goals</a><a href="#sources">Sources</a><a href="#support">Support &amp; access</a></nav>'
    health_chip = _status_chip(health_state.replace("_", " ").title(), health_state)
    report_rows = "".join(
        f'<li><strong>{_e(_value(item, "name", "Stored report"))}</strong><span class="meta">{_e(_value(item, "period", period))} · {_e(_value(item, "status", "available"))}</span></li>'
        for item in reports
    )
    report_list = f'<ul>{report_rows}</ul>' if report_rows else '<div id="reports-empty" class="state state-pending" role="status"><strong>No reports yet</strong><span>A stored report will appear after an approved sync. This portal does not send a report automatically.</span></div>'
    goal_rows = "".join(
        f'<tr><td>{_e(_value(item, "metric", "Goal"))}</td><td>{_e(_value(item, "target", "Not set"))}</td><td>{_e(_value(item, "effectiveFrom", "—"))}</td></tr>'
        for item in goals
    )
    goals_initial = f'<div class="table-wrap"><table><caption class="sr-only">Client goals</caption><thead><tr><th scope="col">Metric</th><th scope="col">Target</th><th scope="col">Effective from</th></tr></thead><tbody>{goal_rows}</tbody></table></div>' if goal_rows else '<div id="goals-empty" class="state state-pending" role="status"><strong>No goals configured</strong><span>Goals are shown only when their metric definition is trustworthy and approved.</span></div>'
    source_rows = "".join(
        f'<tr><td>{_e(_value(item, "source", _value(item, "sourceType", "Source")))}</td><td>{_status_chip(_value(item, "status", "not_configured"), _value(item, "state", "pending"))}</td><td>{_e(_value(item, "lastSyncAt", "Not available"))}</td></tr>'
        for item in sources
    )
    sources_initial = f'<div class="table-wrap"><table><caption class="sr-only">Connected reporting sources</caption><thead><tr><th scope="col">Source</th><th scope="col">Status</th><th scope="col">Last sync</th></tr></thead><tbody>{source_rows}</tbody></table></div>' if source_rows else '<div id="sources-empty" class="state state-pending" role="status"><strong>Sources are not configured</strong><span>Google Ads, call tracking, CRM/booking, and Search Console may require separate client approvals.</span></div>'
    goal_form = (
        '<details class="card card-muted" id="goal-editor"><summary><strong>Add an effective-dated goal</strong></summary><form id="goal-form" class="form-grid" novalidate><div class="full"><label for="goal-metric">Metric</label><input id="goal-metric" name="metric" required placeholder="qualified_leads"></div><div><label for="goal-target">Target</label><input id="goal-target" name="target" type="number" min="0" step="any" required></div><div><label for="goal-from">Effective from</label><input id="goal-from" name="effectiveFrom" type="date" required></div><div><label for="goal-to">Effective to <span class="meta">(optional)</span></label><input id="goal-to" name="effectiveTo" type="date"></div><div class="full form-actions"><span id="goal-status" class="helper" role="status">Saving a goal does not change the measurement contract.</span><button type="submit">Save goal</button></div></form></details>'
    ) if can_edit else '<p class="helper">Viewer access is read-only. Ask a client administrator or agency operator to update goals.</p>'
    support_link = f'<a href="mailto:{_e(support_email)}">Contact support</a>' if support_email and "@" in support_email else '<a href="/docs">Open support documentation</a>'
    body = (
        _header(subtitle=company, role=role, user=user, home_path="/dashboard", nav=nav)
        + '<main id="main-content" class="page">'
        + f'<div class="page-heading" id="overview"><div><p class="eyebrow">Client portal</p><h1>{_e(company)}</h1><p class="lead">Approved reporting for <strong>{_e(domain or "your website")}</strong>, with plain-language freshness and source caveats.</p></div><div class="page-heading-actions"><label class="meta" for="portal-period">Period<select id="portal-period"><option value="7d"{" selected" if period == "7d" else ""}>Last 7 days</option><option value="28d"{" selected" if period == "28d" else ""}>Last 28 days</option><option value="this_month"{" selected" if period == "this_month" else ""}>This month</option><option value="last_month"{" selected" if period == "last_month" else ""}>Last month</option><option value="90d"{" selected" if period == "90d" else ""}>Last 90 days</option></select></label></div></div>'
        + '<div class="notice"><strong>Reporting boundary</strong>Only this approved company and website are rendered. GA4 intent is not presented as a booked appointment, customer, or revenue unless an approved source supports it.</div>'
        + f'<section class="metrics-grid" aria-label="Business outcomes"><article class="card"><div class="metric-label">Qualified leads</div><div id="metric-qualified-leads" class="metric-value">—</div><div class="metric-note">Approved business outcome</div></article><article class="card"><div class="metric-label">Appointments</div><div id="metric-appointments" class="metric-value">—</div><div class="metric-note">Unavailable until source-backed</div></article><article class="card"><div class="metric-label">Users</div><div id="metric-users" class="metric-value">—</div><div class="metric-note">Stored GA4 snapshot</div></article><article class="card"><div class="metric-label">Health</div><div class="metric-value">{health_chip}</div><div class="metric-note">{_e(health_detail)}</div></article></section>'
        + '<section class="two-column" style="margin-top:1rem"><article class="card" aria-labelledby="health-heading"><div class="card-heading"><div><h2 id="health-heading">Measurement health</h2><p>Freshness, access, contract, and data-quality states.</p></div></div><div id="portal-health" class="state state-pending" role="status" aria-live="polite"><strong>Loading health</strong><span>' + _e(health_detail) + '</span></div></article><article class="card" aria-labelledby="freshness-heading"><div class="card-heading"><div><h2 id="freshness-heading">Freshness</h2><p>Stored snapshots do not synchronously query Google.</p></div></div><div id="portal-freshness" class="state state-pending" role="status" aria-live="polite"><strong>Loading sync status</strong><span>Last successful sync and complete date will appear here.</span></div></article></section>'
        + f'<section class="card" id="reports" aria-labelledby="reports-heading" style="margin-top:1rem"><div class="card-heading"><div><h2 id="reports-heading">Reports</h2><p>Download an approved stored report for review.</p></div><a id="portal-pdf" class="button secondary" href="{_e(_route(API_ROUTES["reports_pdf"], website_id=website_id))}?period={_e(period)}">Download PDF</a></div><div id="portal-reports">{report_list}</div><div class="notice"><strong>Delivery is not automatic here.</strong>Recurring report configuration and provider inbox acceptance are separate operator-controlled gates; this UI does not contact a client.</div></section>'
        + f'<section class="card" id="goals" aria-labelledby="goals-heading" style="margin-top:1rem"><div class="card-heading"><div><h2 id="goals-heading">Goals</h2><p>Effective-dated targets with definitions the platform can support.</p></div>{_status_chip("Client admin" if can_edit else "Read only", "info")}</div><div id="portal-goals">{goals_initial}</div>{goal_form}</section>'
        + f'<section class="card" id="sources" aria-labelledby="sources-heading" style="margin-top:1rem"><div class="card-heading"><div><h2 id="sources-heading">Connected sources</h2><p>Unavailable means unavailable, not zero.</p></div></div><div id="portal-sources">{sources_initial}</div></section>'
        + f'<section class="two-column" id="support" style="margin-top:1rem"><article class="card" aria-labelledby="support-heading"><h2 id="support-heading">Support</h2><p class="lead">Need a definition clarified, a source approved, or a stale sync reviewed?</p><p>{support_link}</p><p class="helper">Include the company, website, reporting period, and the visible status message. Do not send credentials or patient information.</p></article><article class="card" aria-labelledby="access-state-heading"><h2 id="access-state-heading">Access state</h2><div id="portal-access" class="state state-{_e(stateFromValue(access_state))}" role="status"><strong>{_e(access_state.replace("_", " ").title())}</strong><span>Your role is {_e(role.replace("_", " "))}. Access changes are approved and audited by the agency.</span></div></article></section>'
        + '<p class="footer-note">This portal is a reviewable product surface. A green health state is evidence about stored data, not a claim that an external provider or inbox accepted a report.</p></main>'
    )
    script = r"""
const { UI, api, route, esc, text, number, setStatus, stateFrom } = window.MeasurementUI;
const site = UI.websiteId || ""; const company = UI.companyId || "";
const period = document.querySelector("#portal-period");
const healthNode = document.querySelector("#portal-health"); const freshnessNode = document.querySelector("#portal-freshness");
const renderHealth = (payload) => {
  const checks = payload.checks || payload.health || [];
  const state = payload.state || payload.status || (Array.isArray(checks) && checks.some((item) => ["error", "blocked"].includes(item.state)) ? "error" : "ok");
  const detail = Array.isArray(checks) && checks.length ? checks.map((item) => `${item.key || "Check"}: ${item.detail || item.state || "reported"}`).join(" · ") : (payload.detail || "Stored measurement health is available.");
  setStatus(healthNode, detail, stateFrom(state));
};
const renderSync = (payload) => { const state = payload.status || payload.state || (payload.lastSuccessfulSync ? "ok" : "pending"); setStatus(freshnessNode, `Status: ${state}. Last successful sync: ${payload.lastSuccessfulSync || "not recorded"}. Last complete date: ${payload.lastCompleteDate || "not recorded"}.`, stateFrom(state)); };
const renderOverview = (payload) => { const metrics = payload.metrics || payload.outcomes || {}; const find = (names) => { for (const name of names) if (metrics[name] !== undefined) return metrics[name]; return null; }; document.querySelector("#metric-qualified-leads").textContent = number(find(["qualified_leads", "qualifiedLeads", "leads"])); document.querySelector("#metric-appointments").textContent = number(find(["booked_appointments", "bookedAppointments", "appointments"])); document.querySelector("#metric-users").textContent = number(find(["active_users", "activeUsers", "users"])); };
const renderGoals = (payload) => { const rows = payload.goals || payload.rows || (Array.isArray(payload) ? payload : []); const target = document.querySelector("#portal-goals"); if (!target || !rows.length) return; target.innerHTML = `<div class="table-wrap"><table><caption class="sr-only">Client goals</caption><thead><tr><th scope="col">Metric</th><th scope="col">Target</th><th scope="col">Effective from</th></tr></thead><tbody>${rows.map((item) => `<tr><td>${text(item.metric, "Goal")}</td><td>${text(item.target, "Not set")}</td><td>${text(item.effectiveFrom, "—")}</td></tr>`).join("")}</tbody></table></div>`; };
const renderSources = (payload) => { const rows = payload.sources || payload.connections || (Array.isArray(payload) ? payload : []); const target = document.querySelector("#portal-sources"); if (!target || !rows.length) return; target.innerHTML = `<div class="table-wrap"><table><caption class="sr-only">Connected reporting sources</caption><thead><tr><th scope="col">Source</th><th scope="col">Status</th><th scope="col">Last sync</th></tr></thead><tbody>${rows.map((item) => { const state = stateFrom(item.state || item.status || "pending"); return `<tr><td>${text(item.source || item.sourceType, "Source")}</td><td><span class="chip chip-${state}"><span class="chip-dot" aria-hidden="true"></span>${text(item.status, "not configured")}</span></td><td>${text(item.lastSyncAt, "Not available")}</td></tr>`; }).join("")}</tbody></table></div>`; };
const load = async () => {
  const p = period?.value || "28d"; const query = `?period=${encodeURIComponent(p)}`;
  document.querySelector("#portal-pdf").href = `${route(UI.apiRoutes.reports_pdf, { website_id: site })}?period=${encodeURIComponent(p)}`;
  setStatus(healthNode, "Loading stored measurement health.", "loading"); setStatus(freshnessNode, "Loading stored sync status.", "loading");
  const requests = await Promise.allSettled([
    api(`${route(UI.apiRoutes.overview, { company_id: company })}${query}`),
    api(`${route(UI.apiRoutes.measurement_health, { website_id: site })}${query}&refresh=false`),
    api(route(UI.apiRoutes.sync_status, { website_id: site })),
    api(`${route(UI.apiRoutes.goals, { website_id: site })}`),
    api(route(UI.apiRoutes.external_sources, { website_id: site }))
  ]);
  const [overview, health, sync, goals, sources] = requests;
  if (overview.status === "fulfilled") renderOverview(overview.value); else setStatus(document.querySelector("#metric-users")?.parentElement?.querySelector(".metric-note"), "Overview is unavailable.", "error");
  if (health.status === "fulfilled") renderHealth(health.value); else setStatus(healthNode, health.reason?.message || "Measurement health is unavailable.", "error");
  if (sync.status === "fulfilled") renderSync(sync.value); else setStatus(freshnessNode, sync.reason?.message || "Sync status is unavailable.", "error");
  if (goals.status === "fulfilled") renderGoals(goals.value);
  if (sources.status === "fulfilled") renderSources(sources.value);
};
period?.addEventListener("change", load); load();
document.querySelector("#goal-form")?.addEventListener("submit", async (event) => { event.preventDefault(); const node = document.querySelector("#goal-status"); const data = Object.fromEntries(new FormData(event.currentTarget)); setStatus(node, "Saving the effective-dated goal.", "loading"); try { await api(route(UI.apiRoutes.goals, { website_id: site }), { method: "POST", body: JSON.stringify({ metric: data.metric, target: Number(data.target), effectiveFrom: data.effectiveFrom, effectiveTo: data.effectiveTo || null }) }); setStatus(node, "Goal saved for this approved website.", "success"); await load(); } catch (error) { setStatus(node, error.message || "Goal could not be saved.", "error"); } });
"""
    return _page(
        "Client portal · Measurement & Reporting",
        body,
        config={"role": role, "company": company, "companyId": company_id, "websiteId": website_id, "period": period, "accessState": access_state},
        api_routes=PORTAL_API_ROUTES,
        script=script,
    )


def stateFromValue(value: Any) -> str:
    """Map access-state values to the small CSS state vocabulary."""

    normalized = _text(value, "pending").lower()
    return {"approved": "success", "active": "success", "ok": "success", "pending": "pending", "invited": "pending", "revoked": "error", "blocked": "blocked"}.get(normalized, "info")


# Short aliases keep integration call sites readable while the explicit names
# remain the documented public API.
agency_html = agency_shell_html
onboarding_html = client_onboarding_html
portal_html = client_portal_html


__all__ = [
    "AGENCY_ROLES",
    "API_ROUTES",
    "CLIENT_ROLES",
    "KNOWN_ROLES",
    "ONBOARDING_STEPS",
    "agency_html",
    "agency_shell_html",
    "client_onboarding_html",
    "client_portal_html",
    "login_html",
    "onboarding_html",
    "portal_html",
    "stateFromValue",
]
