from __future__ import annotations

import hmac
import hashlib
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from .config import ROOT, Settings, Site, load_dotenv, load_site
from .auth import (
    AGENCY_ROLES,
    PortalIdentityError,
    TenantContext,
    cloud_identity_email,
    resolve_portal_identity_email,
)
from .credentials import AdcCredential, GA4Admin, OAuthCredential
from .dashboard import agency_dashboard_html, dashboard_html
from .ga4 import GA4AccessVerifier, GA4Reporter, access_error_health, assignment_health, unverified_access_health
from .reports import build_client_pdf
from .report_delivery import ReportEmailSender, delivery_html
from .oauth import ANALYTICS_READONLY_SCOPE, KmsCipher, OAuthManager
from .product_ui import agency_shell_html, client_onboarding_html, client_portal_html, login_html
from .storage import Database, normalize_canonical_domain
from .sync import SyncEngine
from .tasks import PERIODS, TaskQueue
from .external_sync import ExternalSyncEngine
from .source_runtime import BufferedOutcomeConnector, PINNED_SECRET, SourceConnectorFactory, external_sync_window
from .telemetry import PrivacySafeTracingMiddleware, configure_tracing

Period = Literal["7d", "28d", "90d", "this_month", "last_month"]
bearer = HTTPBearer(auto_error=False, description="Dashboard API token when token authentication is enabled")


class SyncRequest(BaseModel):
    period: Period
    scheduledFor: str | None = None
    assignmentId: str | None = None


class AnnotationRequest(BaseModel):
    date: date
    type: Literal["site_launch", "campaign_launch", "tracking_change", "website_redesign", "major_outage", "measurement_change"]
    note: str


class GoalRequest(BaseModel):
    metric: str
    target: float
    effectiveFrom: date
    effectiveTo: date | None = None


class RecurringReportRequest(BaseModel):
    name: str
    period: Period
    cadence: Literal["weekly", "monthly"]
    timezone: str
    recipientReference: str
    nextRunAt: datetime


class OAuthRevokeRequest(BaseModel):
    deleteToken: bool = False


class OffboardingRequest(BaseModel):
    confirmationWebsiteId: str


class MembershipRequest(BaseModel):
    email: str
    role: Literal["agency_owner","agency_admin","agency_analyst","client_admin","client_viewer"]


class SourceConnectionRequest(BaseModel):
    sourceType: Literal["google_ads","search_console","call_tracking","crm_booking"]
    credentialSecretReference: str
    externalAccountId: str | None = None
    configuration: dict = Field(default_factory=dict)


class SourceApprovalRequest(BaseModel):
    confirmationSourceType: Literal["google_ads","search_console","call_tracking","crm_booking"]
    approvalReference: str = Field(min_length=3,max_length=160,pattern=r"^[A-Za-z0-9_.:-]+$")


class ExternalSyncRequest(BaseModel):
    connectionId: str
    sourceType: Literal["google_ads","search_console"]
    startDate: date
    endDate: date


class OutcomeBatchRequest(BaseModel):
    requestId: str = Field(min_length=8,max_length=160,pattern=r"^[A-Za-z0-9_.:-]+$")
    records: list[dict] = Field(min_length=1,max_length=1000)


class OAuthAssignmentRequest(BaseModel):
    websiteId: str
    propertyId: str
    streamId: str


class OnboardingWorkflowRequest(BaseModel):
    idempotencyKey: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$")
    companyId: str = Field(min_length=1, max_length=120)
    companyName: str = Field(min_length=1, max_length=200)
    websiteId: str = Field(min_length=1, max_length=120)
    canonicalDomain: str = Field(min_length=1, max_length=253)
    contractSlug: Literal["local_service_v1"] = "local_service_v1"


class OnboardingGovernanceRequest(BaseModel):
    governanceStatus: Literal["pending_review", "requires_review", "approved", "prohibited"]
    consentStatus: Literal["pending_client_consent", "approved", "rejected"]
    governanceReference: str | None = Field(default=None, max_length=160)
    consentReference: str | None = Field(default=None, max_length=160)


class OnboardingConnectionRequest(BaseModel):
    idempotencyKey: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$")
    connectionKind: Literal["ga4", "google_ads", "search_console", "call_tracking", "crm_booking"] | None = None
    sourceType: Literal["ga4", "google_ads", "search_console", "call_tracking", "crm_booking"] | None = None
    mode: Literal["registered", "deferred"]
    credentialType: Literal["service_account", "oauth"] | None = None
    credentialReference: str | None = Field(default=None, max_length=300)
    externalPropertyId: str | None = None
    externalStreamId: str | None = None
    externalAccountId: str | None = Field(default=None, max_length=200)
    configuration: dict = Field(default_factory=dict)
    deferReason: str | None = Field(default=None, max_length=500)


class OnboardingFirstSyncRequest(BaseModel):
    action: Literal["request", "check"] = "request"
    idempotencyKey: str | None = Field(default=None, min_length=8, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$")


class OnboardingClientMembershipRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["client_admin", "client_viewer"]
    authorizationReference: str = Field(min_length=3, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,159}$")


def create_app(settings=None, reporter=None, database=None, task_queue=None, source_connector_factory=None):
    load_dotenv(); site = load_site(); settings = settings or Settings.from_environment(); settings.validate(site)
    tracing_runtime = configure_tracing(settings)
    database = database or Database(settings)
    report_sender = ReportEmailSender(settings.report_email_api_key,settings.report_email_from,settings.report_recipients,settings.report_email_endpoint)
    oauth_manager = OAuthManager(settings.google_oauth_client_id,settings.google_oauth_client_secret,settings.google_oauth_redirect_uri,settings.google_oauth_state_secret,KmsCipher(settings.google_oauth_kms_key),settings.google_oauth_enabled)
    source_connector_factory = source_connector_factory or SourceConnectorFactory()

    @asynccontextmanager
    async def lifespan(app):
        app.state.reporter = reporter
        try:
            yield
        finally:
            database.close()
            if tracing_runtime: tracing_runtime.shutdown()

    app = FastAPI(title="Measurement & Reporting Platform",version="1.0.0",description="Stored, privacy-aware measurement reporting for configured tenants.",lifespan=lifespan)
    static_root = ROOT / "app" / "static"
    static_index = static_root / "index.html"
    static_assets = static_root / "assets"
    if static_root.exists() and static_assets.exists():
        app.mount("/assets", StaticFiles(directory=static_assets), name="assets")
    app.add_middleware(PrivacySafeTracingMiddleware)
    app.state.settings, app.state.site, app.state.database = settings, site, database

    @app.middleware("http")
    async def headers(request: Request, call_next):
        callback_paths=frozenset({"/oauth/google/callback","/oauth/google/ready","/health","/healthz"})
        response = JSONResponse(status_code=404,content={"detail":"not_found"}) if settings.oauth_callback_only and request.url.path not in callback_paths else await call_next(request)
        response.headers.update({"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer","X-Frame-Options":"DENY","Permissions-Policy":"camera=(), microphone=(), geolocation=()","Content-Security-Policy":"default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    def require_context(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_organization_id: str = Header(default=""),
        x_serverless_authorization: str | None = Header(default=None, alias="X-Serverless-Authorization"),
        authorization: str | None = Header(default=None, alias="Authorization"),
        x_goog_iap_jwt_assertion: str | None = Header(default=None, alias="X-Goog-IAP-JWT-Assertion"),
        x_goog_authenticated_user_email: str | None = Header(default=None, alias="X-Goog-Authenticated-User-Email"),
    ) -> TenantContext:
        # The browser portal has a separate, explicit mode. Its identity is
        # established from a verified IAP/Google assertion and then resolved
        # through the tenant membership database. No user-supplied tenant
        # header is accepted on this path.
        if settings.portal_iap_enabled:
            try:
                email = resolve_portal_identity_email(
                    # These headers authenticate transport to Cloud Run. The
                    # resolver intentionally ignores them for portal identity;
                    # only the signed IAP assertion may identify a user.
                    authorization=authorization,
                    x_serverless_authorization=x_serverless_authorization,
                    x_goog_iap_jwt_assertion=x_goog_iap_jwt_assertion,
                    x_goog_authenticated_user_email=x_goog_authenticated_user_email,
                    expected_audience=settings.portal_expected_audience,
                    portal_iap_mode=settings.portal_iap_mode,
                    live=settings.mode == "live",
                )
            except PortalIdentityError as error:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=str(error),
                    headers={"WWW-Authenticate": "Bearer"},
                ) from error
            try:
                return database.authorize_context(email, None)
            except PermissionError as error:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="portal_authorization_unavailable") from error

        if settings.auth_mode != "cloud_run" and settings.live_enabled:
            supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
            if not hmac.compare_digest(supplied, settings.api_token):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        try:
            if settings.auth_mode=="cloud_run" and x_serverless_authorization: raise PermissionError("ambiguous_cloud_identity_headers")
            email=cloud_identity_email(credentials.credentials if credentials else "",settings.operator_email,allow_cloud_run_signature_removed=settings.auth_mode=="cloud_run") if settings.auth_mode=="cloud_run" else settings.operator_email
            return database.authorize_context(email, x_organization_id or None)
        except PermissionError as error:
            raise HTTPException(status_code=403,detail=str(error)) from error

    def require_portal_mode() -> None:
        if not settings.portal_iap_enabled:
            raise HTTPException(status_code=404, detail="portal_not_enabled")

    def require_portal_context(context: TenantContext = Depends(require_context)) -> TenantContext:
        require_portal_mode()
        return context

    def require_portal_onboarding_role(context: TenantContext) -> TenantContext:
        try:
            context.require_role(frozenset({"agency_owner", "agency_admin"}))
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        return context

    def portal_login_markup(next_path: str = "/portal") -> str:
        # The current product UI login contract already publishes the
        # dedicated /api/portal/identity route. It deliberately does not use
        # Analytics connector OAuth for portal identity.
        return login_html(
            next_path=next_path,
            auth_mode="portal_iap",
            notice="Use the Google identity approved for this portal. Portal access is separate from Analytics connector authorization.",
        )

    def _portal_resource_query(context: TenantContext) -> list[dict]:
        """Resolve portal resources through the tenant-scoped DB session.

        ``Database`` implementations may expose a richer resolver in the
        future. The SQL fallback keeps this integration usable with the
        current storage class without making the configured boot site an
        authorization source. RLS is established by ``tenant_connection``.
        """

        resolver = getattr(database, "portal_resources", None)
        if callable(resolver):
            return resolver(context)
        with database.tenant_connection(context) as connection:
            rows = connection.execute(
                """
                SELECT cp.public_id company_id,c.name company,
                       wp.public_id website_id,w.canonical_domain
                  FROM app.websites w
                  JOIN app.companies c ON c.id=w.company_id
                  JOIN app.resource_identifiers cp
                    ON cp.resource_type='company' AND cp.resource_id=c.id
                  JOIN app.resource_identifiers wp
                    ON wp.resource_type='website' AND wp.resource_id=w.id
                 ORDER BY c.name,w.canonical_domain
                """
            ).fetchall()
        return [dict(row) for row in rows]

    async def portal_authorized_resources(context: TenantContext) -> list[dict]:
        resources = await call(_portal_resource_query, context)
        authorized = []
        for item in resources or []:
            website_id = str(item.get("website_id", item.get("websiteId", ""))).strip()
            company_id = str(item.get("company_id", item.get("companyId", ""))).strip()
            if not website_id or not company_id:
                continue
            if not await call(database.website_authorized, context, website_id):
                continue
            if not await call(database.company_authorized, context, company_id):
                continue
            authorized.append(
                {
                    "companyId": company_id,
                    "company": item.get("company") or "Client workspace",
                    "websiteId": website_id,
                    "canonicalDomain": item.get("canonical_domain", item.get("canonicalDomain", "")) or "",
                }
            )
        return authorized

    async def portal_site_selection(
        context: TenantContext,
        website_id: str = "",
        company_id: str = "",
    ) -> dict:
        resources = await portal_authorized_resources(context)
        requested_website = website_id.strip()
        requested_company = company_id.strip()
        matches = [
            item
            for item in resources
            if (not requested_website or item["websiteId"] == requested_website)
            and (not requested_company or item["companyId"] == requested_company)
        ]
        if not matches:
            raise HTTPException(status_code=403, detail="portal_resource_not_authorized")
        if len(matches) > 1:
            raise HTTPException(status_code=422, detail="portal_resource_selection_required")
        selected = matches[0]
        # Keep the explicit resource checks at the request boundary even when
        # the resource list came from a tenant-scoped resolver.
        if not await call(database.website_authorized, context, selected["websiteId"]):
            raise HTTPException(status_code=403, detail="forbidden_website")
        if not await call(database.company_authorized, context, selected["companyId"]):
            raise HTTPException(status_code=403, detail="forbidden_company")
        return selected

    async def portal_client_page(
        context: TenantContext,
        website_id: str = "",
        company_id: str = "",
        period: Period = "28d",
    ) -> HTMLResponse:
        resource = await portal_site_selection(context, website_id, company_id)
        return HTMLResponse(
            client_portal_html(
                role=context.role,
                user=context.email,
                company=resource["company"],
                company_id=resource["companyId"],
                website_id=resource["websiteId"],
                domain=resource["canonicalDomain"],
                period=period,
                access_state="approved",
            )
        )

    async def portal_agency_page(context: TenantContext, active: str = "overview") -> HTMLResponse:
        if context.role not in AGENCY_ROLES:
            return HTMLResponse(
                agency_shell_html(
                    role=context.role,
                    user=context.email,
                    organization_name="Agency workspace",
                    portal_path="/dashboard",
                )
            )
        summary = await call(database.portfolio_summary, context, "28d")
        clients = []
        alerts = []
        for item in summary.get("websites", []):
            sync_health = item.get("syncHealth") or "pending"
            state = "ok" if sync_health in {"ok", "ready", "succeeded", "success"} else "warning" if sync_health else "pending"
            clients.append(
                {
                    "company": item.get("company") or "Client workspace",
                    "website": item.get("canonicalDomain") or "Website pending",
                    "health": sync_health,
                    "healthState": state,
                    "lastCompleteDate": item.get("lastCompleteDate"),
                    "actionRequired": "Review stored alerts" if item.get("actionRequired") else "No action recorded",
                }
            )
            for alert in item.get("alerts", []):
                alerts.append(
                    {
                        "title": alert.get("key", "Client action required"),
                        "detail": alert.get("detail", "Review the stored client health state."),
                    }
                )
        return HTMLResponse(
            agency_shell_html(
                role=context.role,
                user=context.email,
                organization_name="Agency workspace",
                clients=clients,
                alerts=alerts,
                active=active,
                portal_path="/dashboard",
            )
        )

    async def portal_onboarding_page(context: TenantContext, workflow_id: str = "") -> HTMLResponse:
        require_portal_onboarding_role(context)
        company = {}
        website = {}
        gates = {}
        connection_id = ""
        if workflow_id.strip():
            try:
                workflow = await onboarding_call(database.onboarding_workflow, context, workflow_id.strip())
            except PermissionError as error:
                raise HTTPException(status_code=403, detail=str(error)) from error
            except ValueError as error:
                raise onboarding_error(error) from error
            company = workflow.get("company") or {}
            website = workflow.get("website") or {}
            connections = workflow.get("connections") or []
            ga4_connection = next((item for item in connections if item.get("kind") == "ga4"), None)
            connection_id = (ga4_connection or {}).get("connectionId", "")
            contract = workflow.get("contract") or {}
            governance = workflow.get("governance") or {}
            gates = {
                "governance": governance.get("status", "pending"),
                "contract": contract.get("approvalStatus", "pending"),
                "analytics": (ga4_connection or {}).get("status", "not_configured"),
                "sync": (workflow.get("firstSync") or {}).get("status", "pending"),
                "access": "approved" if workflow.get("clientMembers") else "pending",
            }
        return HTMLResponse(
            client_onboarding_html(
                role=context.role,
                user=context.email,
                company=company,
                site=website,
                gates=gates,
                connection_id=connection_id,
                client_portal_path="/dashboard",
            )
        )

    def require_agency(context: TenantContext = Depends(require_context)) -> TenantContext:
        try: context.require_role(AGENCY_ROLES)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        return context

    def require_onboarding_admin(context: TenantContext = Depends(require_context)) -> TenantContext:
        try: context.require_role(frozenset({"agency_owner", "agency_admin"}))
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        return context

    def require_internal(x_internal_trigger_token: str = Header(default="")):
        if len(settings.internal_trigger_token) < 32 or not hmac.compare_digest(x_internal_trigger_token,settings.internal_trigger_token):
            raise HTTPException(status_code=401,detail="unauthorized_internal_trigger")

    def ga4_access_health(refresh: bool = True, website_site: Site | None = None):
        """Return assignment and bounded access evidence for a given website.

        The verifier cache is keyed by the website's property so one tenant's
        live probe cannot be reused for another and a stale boot singleton is
        never consulted.
        """
        assignment = assignment_health(website_site, str(website_site.property_id), str(website_site.stream_id))
        if assignment["state"] != "configured":
            return assignment, unverified_access_health("ga4_assignment_not_configured")
        if not settings.live_enabled:
            return assignment, unverified_access_health("live_reporting_disabled")
        try:
            cache = getattr(app.state, "ga4_access_verifiers", None)
            if cache is None:
                cache = {}
                app.state.ga4_access_verifiers = cache
            property_key = str(website_site.property_id)
            verifier = cache.get(property_key)
            if verifier is None:
                # The injected reporter seam is a test double that provides
                # verify_access(); production builds a per-website reporter.
                reporter_instance = getattr(app.state, "reporter", None)
                if reporter_instance is None:
                    reporter_instance = GA4Reporter(website_site, AdcCredential().get_authorized_client())
                verifier = GA4AccessVerifier(reporter_instance)
                cache[property_key] = verifier
            return assignment, verifier.check(refresh=refresh)
        except Exception as error:
            return assignment, access_error_health(error)

    async def call(method,*args):
        try: return await run_in_threadpool(method,*args)
        except HTTPException: raise
        except PermissionError: raise
        except Exception as error: raise HTTPException(status_code=502,detail=f"upstream_operation_failed:{type(error).__name__}") from error

    async def onboarding_call(method, *args):
        try: return await run_in_threadpool(method, *args)
        except (HTTPException, PermissionError, ValueError): raise
        except Exception as error: raise HTTPException(status_code=502, detail=f"onboarding_operation_failed:{type(error).__name__}") from error

    def onboarding_error(error: Exception) -> HTTPException:
        code = str(error)
        if code.endswith("_not_found"):
            return HTTPException(status_code=404, detail=code)
        if "conflict" in code:
            return HTTPException(status_code=409, detail=code)
        return HTTPException(status_code=422, detail=code)

    def snapshot(context: TenantContext, website_id: str, view: str, period: str):
        if not database.configured: raise HTTPException(status_code=503,detail="production_database_not_configured")
        value = database.latest_snapshot(context,website_id,view,period)
        if value is None: raise HTTPException(status_code=503,detail="report_not_synced")
        return value

    def website_site(context: TenantContext, website_id: str) -> Site:
        """Build a per-website Site from tenant-scoped storage metadata.

        Endpoints that previously used the boot singleton now resolve the
        requested website's company, domain, timezone, assignment, governance,
        and stream measurement ID from Postgres under RLS. No value is borrowed
        from the boot ``site`` singleton.
        """
        row = database.website_site_context(context, website_id)
        property_timezone = row.get("property_timezone") or "UTC"
        return Site(
            row["site_id"],
            row["company_id"],
            row["company"],
            row["canonical_domain"],
            "live",  # deployment status is not a persisted runtime field
            property_timezone,
            str(row.get("property_id") or ""),
            str(row.get("stream_id") or ""),
            str(row.get("measurement_id") or ""),
            property_timezone,
            "unavailable",  # collection status is not a persisted runtime field
            row.get("governance_status") or "requires_review",
        )

    @app.get("/",response_class=HTMLResponse,include_in_schema=False)
    def home():
        if settings.portal_iap_enabled:
            return HTMLResponse(portal_login_markup("/portal"))
        return f"<!doctype html><title>Measurement Platform</title><style>body{{font:16px system-ui;margin:3rem;max-width:55rem}}</style><h1>Measurement &amp; Reporting Platform</h1><p>Stored reporting for the configured tenant.</p><p><a href='/agency'>Open agency console</a> · <a href='/dashboard'>Open client reporting view</a> · <a href='/docs'>Open API documentation</a></p>"

    @app.get("/favicon.ico",include_in_schema=False)
    def favicon(): return Response(status_code=204)

    @app.get("/dashboard",response_class=HTMLResponse,include_in_schema=False)
    async def dashboard(
        website_id: str = "",
        company_id: str = "",
        period: Period = "28d",
        context: TenantContext = Depends(require_context),
    ):
        if settings.portal_iap_enabled:
            if context.role in AGENCY_ROLES:
                return RedirectResponse("/agency", status_code=303)
            return await portal_client_page(context, website_id, company_id, period)
        return dashboard_html(site)

    @app.get("/agency",response_class=HTMLResponse,include_in_schema=False)
    async def agency(context: TenantContext = Depends(require_context)):
        if settings.portal_iap_enabled:
            return await portal_agency_page(context)
        try:
            context.require_role(AGENCY_ROLES)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        return agency_dashboard_html(site)

    @app.get("/portal/login", response_class=HTMLResponse, include_in_schema=False, dependencies=[Depends(require_portal_mode)])
    @app.get("/login", response_class=HTMLResponse, include_in_schema=False, dependencies=[Depends(require_portal_mode)])
    def portal_login(next_path: str = "/portal"):
        return HTMLResponse(portal_login_markup(next_path))

    @app.get("/portal/access/status", tags=["Portal Access"], include_in_schema=False)
    async def portal_access_status(context: TenantContext = Depends(require_portal_context)):
        destination = "/agency" if context.role in AGENCY_ROLES else "/dashboard"
        return {
            "status": "authenticated",
            "identityMode": "iap",
            "role": context.role,
            "redirect": destination,
        }

    @app.get("/api/portal/identity", tags=["Portal Access"], include_in_schema=False)
    async def portal_identity(context: TenantContext = Depends(require_portal_context)):
        return {
            "approved": True,
            "identityMode": "iap",
            "identity": {"email": context.email, "role": context.role},
        }

    @app.post("/portal/access/continue", tags=["Portal Access"], include_in_schema=False)
    async def portal_access_continue(
        website_id: str = "",
        company_id: str = "",
        context: TenantContext = Depends(require_portal_context),
    ):
        if context.role in AGENCY_ROLES:
            destination = "/agency"
        else:
            resource = await portal_site_selection(context, website_id, company_id)
            destination = f"/dashboard?website_id={quote(resource['websiteId'], safe='')}&company_id={quote(resource['companyId'], safe='')}"
        return {"status": "authenticated", "identityMode": "iap", "role": context.role, "redirect": destination}

    @app.get("/portal/access/resources", tags=["Portal Access"], include_in_schema=False)
    async def portal_access_resources(context: TenantContext = Depends(require_portal_context)):
        return {"resources": await portal_authorized_resources(context)}

    if static_index.exists():
        @app.get("/portal", include_in_schema=False)
        def portal_home():
            return FileResponse(static_index)
    else:
        @app.get("/portal", include_in_schema=False)
        async def portal_home(context: TenantContext = Depends(require_portal_context)):
            if context.role in AGENCY_ROLES:
                return RedirectResponse("/agency", status_code=303)
            resource = await portal_site_selection(context)
            return RedirectResponse(
                f"/dashboard?website_id={quote(resource['websiteId'], safe='')}&company_id={quote(resource['companyId'], safe='')}",
                status_code=303,
            )

    @app.get("/portal/agency", response_class=HTMLResponse, include_in_schema=False)
    async def portal_agency(context: TenantContext = Depends(require_portal_context)):
        return await portal_agency_page(context)

    @app.get("/portal/client/{website_id}", response_class=HTMLResponse, include_in_schema=False)
    async def portal_client(
        website_id: str,
        company_id: str = "",
        period: Period = "28d",
        context: TenantContext = Depends(require_portal_context),
    ):
        return await portal_client_page(context, website_id, company_id, period)

    @app.get("/portal/onboarding", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/agency/onboarding", response_class=HTMLResponse, include_in_schema=False)
    async def portal_onboarding(workflow_id: str = "", context: TenantContext = Depends(require_portal_context)):
        return await portal_onboarding_page(context, workflow_id)

    @app.get("/agency/reports", response_class=HTMLResponse, include_in_schema=False)
    async def portal_reports(context: TenantContext = Depends(require_portal_context)):
        return await portal_agency_page(context, active="reports")

    @app.get("/agency/access", response_class=HTMLResponse, include_in_schema=False)
    async def portal_access(context: TenantContext = Depends(require_portal_context)):
        require_portal_onboarding_role(context)
        return await portal_agency_page(context, active="access")

    @app.get("/health",tags=["Operations"])
    def health(): return {"status":"ok","runtime":"fastapi","liveReporting":settings.live_enabled}

    async def readiness():
        db = await call(database.health)
        ready_state = settings.live_enabled and db.get("status") == "ok"
        payload = {"status":"ready" if ready_state else "not_ready","liveReporting":settings.live_enabled,"database":db}
        return JSONResponse(status_code=200 if ready_state else 503,content=jsonable_encoder(payload))

    @app.get("/healthz",tags=["Operations"],include_in_schema=False)
    async def healthz(): return await readiness()

    @app.get("/oauth/google/ready",tags=["Operations"],include_in_schema=False)
    async def oauth_ready():
        db = await call(database.health)
        ready_state = settings.live_enabled and db.get("status") == "ok"
        return JSONResponse(status_code=200 if ready_state else 503,content={"status":"ready" if ready_state else "not_ready"})

    @app.get("/ready",tags=["Operations"])
    async def ready(): return await readiness()

    @app.get("/api/portfolio/summary",tags=["Reporting"])
    async def portfolio(period:Period="28d",context:TenantContext=Depends(require_agency)):
        return await call(database.portfolio_summary,context,period)

    @app.get("/api/companies/{company_id}/overview",tags=["Reporting"])
    async def overview(company_id:str,period:Period="28d",context:TenantContext=Depends(require_context)):
        if not await call(database.company_authorized,context,company_id): raise HTTPException(status_code=403,detail="forbidden_company")
        if settings.portal_iap_enabled:
            resource = await portal_site_selection(context, company_id=company_id)
            return await call(snapshot, context, resource["websiteId"], "overview", period)
        return await call(snapshot,context,site.site_id,"overview",period)

    @app.get("/api/websites/{website_id}/acquisition",tags=["Reporting"])
    async def acquisition(website_id:str,period:Period="28d",context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        return await call(snapshot,context,website_id,"acquisition",period)

    @app.get("/api/websites/{website_id}/conversion",tags=["Reporting"])
    async def conversion(website_id:str,period:Period="28d",context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        return await call(snapshot,context,website_id,"conversion",period)

    @app.get("/api/websites/{website_id}/landing-pages",tags=["Reporting"])
    async def landing_pages(website_id:str,period:Period="28d",context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        return await call(snapshot,context,website_id,"landing-pages",period)

    @app.get("/api/websites/{website_id}/events",tags=["Reporting"])
    async def events(website_id:str,period:Period="28d",context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        return await call(snapshot,context,website_id,"events",period)

    @app.get("/api/websites/{website_id}/measurement-health",tags=["Operations"])
    async def measurement_health(website_id:str,period:Period="28d",refresh:bool=True,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        requested_site = await call(website_site, context, website_id)
        sync = await call(database.sync_status,context,website_id) if database.configured else {"status":"disabled","quality":{}}
        admin_health = await call(database.latest_measurement_health,context,website_id) if database.configured and hasattr(database,"latest_measurement_health") else None
        event_health = await call(snapshot,context,website_id,"events",period) if database.configured else {"expectedEvents":[],"otherObservedEvents":[],"prohibitedEvents":[]}
        assignment, access_health = await call(ga4_access_health, refresh, requested_site)
        access_check_state = {"verified":"ok","stale":"warning","unverified":"blocked","error":"error"}.get(access_health["state"],"error")
        governance_status = requested_site.governance_status
        quality = sync.get("quality", {}) or {}
        checks=[
          {"key":"ga4_data_api","state":access_check_state,"accessState":access_health["state"],"detail":access_health["reason"]},
          {"key":"assignment","state":"ok" if assignment["state"] == "configured" else "error","assignmentState":assignment["state"],"detail":assignment["detail"]},
          {"key":"persistence","state":"ok" if sync.get("lastSuccessfulSync") else "blocked","detail":"stored report execution available" if sync.get("lastSuccessfulSync") else "first sync has not succeeded"},
          {"key":"governance","state":"ok" if governance_status == "approved" else "warning","detail":governance_status},
          {"key":"collection","state":"warning" if quality.get("empty") else "ok","detail":"successful GA4 reports returned no rows" if quality.get("empty") else "stored GA4 reporting collection is present"},
        ]
        if admin_health: checks.extend(admin_health["details"].get("checks",[]))
        state="ready" if all(c["state"]=="ok" for c in checks) else "attention_required"
        return {"websiteId":requested_site.site_id,"governanceStatus":governance_status,"state":state,"contract":{"slug":"local_service_v1","version":1,"approvalStatus":"approved" if governance_status == "approved" else "pending_approval"},"lastValidation":admin_health["checkedAt"] if admin_health else None,"lastAccessVerification":access_health["lastVerifiedAt"],"assignment":assignment,"ga4AccessHealth":access_health,"requiredEventHealth":event_health.get("expectedEvents",[]),"unexpectedEvents":event_health.get("otherObservedEvents",[]),"prohibitedEventDetection":event_health.get("prohibitedEvents",[]),"leadEventActivity":next((item for item in event_health.get("expectedEvents",[]) if item["event"]=="generate_lead"),None),"consentConfiguration":{"required":True,"approvalStatus":"approved" if governance_status == "approved" else "pending_authorized_review"},"adminHealth":admin_health,"checks":checks,"sync":sync}

    @app.get("/api/websites/{website_id}/sync-status",tags=["Operations"])
    async def status_(website_id:str,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        return {"websiteId":website_id,**(await call(database.sync_status,context,website_id) if database.configured else {"status":"disabled","reason":"database_not_configured"})}

    @app.get("/api/websites/{website_id}/annotations",tags=["Reporting"])
    async def annotations(website_id:str,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        return {"websiteId":website_id,"annotations":await call(database.list_annotations,context,website_id)}

    @app.post("/api/websites/{website_id}/annotations",status_code=201,tags=["Reporting"])
    async def add_annotation(website_id:str,request:AnnotationRequest,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        if not request.note.strip() or len(request.note.strip()) > 1000: raise HTTPException(status_code=422,detail="invalid_annotation_note")
        try: value=await call(database.create_annotation,context,website_id,request.date,request.type,request.note.strip())
        except HTTPException: raise
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        return {"websiteId":website_id,"annotation":value}

    @app.get("/api/websites/{website_id}/goals",tags=["Reporting"])
    async def goals(website_id:str,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        return {"websiteId":website_id,"goals":await call(database.list_goals,context,website_id),"approvedMetrics":await call(database.list_goal_metrics,context,website_id)}

    @app.post("/api/websites/{website_id}/goals",status_code=201,tags=["Reporting"])
    async def add_goal(website_id:str,request:GoalRequest,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        if request.target < 0 or request.effectiveTo and request.effectiveTo < request.effectiveFrom: raise HTTPException(status_code=422,detail="invalid_goal")
        try: value=await call(database.create_goal,context,website_id,request.metric,request.target,request.effectiveFrom,request.effectiveTo)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        return {"websiteId":website_id,"goal":value}

    @app.get("/api/websites/{website_id}/reports/pdf",tags=["Reporting"])
    async def report_pdf(website_id:str,period:Period="28d",context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        requested_site = await call(website_site, context, website_id)
        overview_data=await call(snapshot,context,website_id,"overview",period)
        acquisition_data=await call(snapshot,context,website_id,"acquisition",period)
        annotation_data=await call(database.list_annotations,context,website_id)
        content=await call(build_client_pdf,requested_site,period,overview_data,acquisition_data,annotation_data)
        return Response(content=content,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{website_id}-{period}-report.pdf"'})

    @app.get("/api/websites/{website_id}/recurring-reports",tags=["Reporting"])
    async def recurring_reports(website_id:str,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        return {"websiteId":website_id,"emailDeliveryConfigured":report_sender.configured,"reports":await call(database.list_recurring_reports,context,website_id)}

    @app.post("/api/websites/{website_id}/recurring-reports",status_code=201,tags=["Reporting"])
    async def add_recurring_report(website_id:str,request:RecurringReportRequest,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        if not report_sender.configured: raise HTTPException(status_code=503,detail="report_email_not_configured")
        try:
            ZoneInfo(request.timezone)
            report_sender.resolve_recipient(request.recipientReference)
        except (ZoneInfoNotFoundError,RuntimeError) as error: raise HTTPException(status_code=422,detail=str(error)) from error
        if not request.name.strip() or len(request.name.strip())>120 or request.nextRunAt.tzinfo is None: raise HTTPException(status_code=422,detail="invalid_recurring_report")
        try: value=await call(database.create_recurring_report,context,website_id,request.name.strip(),request.period,request.cadence,request.timezone,request.recipientReference,request.nextRunAt)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        return {"websiteId":website_id,"report":value}

    @app.delete("/api/websites/{website_id}/recurring-reports/{report_id}",status_code=204,tags=["Reporting"])
    async def remove_recurring_report(website_id:str,report_id:str,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        try: removed=await call(database.disable_recurring_report,context,website_id,report_id)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        if not removed: raise HTTPException(status_code=404,detail="recurring_report_not_found")
        return Response(status_code=204)

    @app.post("/internal/reports/dispatch",dependencies=[Depends(require_internal)],include_in_schema=False)
    async def dispatch_reports():
        results=[]
        for report in await call(database.due_recurring_reports,20):
            delivery_id=await call(database.begin_report_delivery,report)
            if not delivery_id:
                results.append({"reportId":report["id"],"status":"already_sent"}); continue
            report_hash=""
            try:
                context=TenantContext(report["organization_id"],report["created_by"],"", "agency_owner")
                overview_data=await call(snapshot,context,report["website_id"],"overview",report["period_key"])
                acquisition_data=await call(snapshot,context,report["website_id"],"acquisition",report["period_key"])
                annotation_data=await call(database.list_annotations,context,report["website_id"])
                report_site = await call(website_site, context, report["website_id"])
                content=await call(build_client_pdf,report_site,report["period_key"],overview_data,acquisition_data,annotation_data)
                report_hash=hashlib.sha256(content).hexdigest()
                message_id=await call(report_sender.send_pdf,report["recipient_secret_reference"],f"{report['company']} analytics report",delivery_html(report["company"],report["period_key"]),f"{report['website_id']}-{report['period_key']}-report.pdf",content,f"report-delivery/{delivery_id}")
                await call(database.finish_report_delivery,report,delivery_id,report_hash,message_id,None)
                results.append({"reportId":report["id"],"status":"sent","deliveryId":delivery_id})
            except Exception as error:
                code=str(error) if isinstance(error,RuntimeError) else type(error).__name__
                bounded_code=code[:120]
                await call(database.finish_report_delivery,report,delivery_id,report_hash,None,bounded_code)
                results.append({"reportId":report["id"],"status":"failed","errorCode":bounded_code})
        summary={"processed":len(results),"deliveries":results}
        if any(result["status"] == "failed" for result in results):
            raise HTTPException(status_code=502,detail={"code":"report_dispatch_failed",**summary})
        return {"status":"ok",**summary}

    @app.get("/api/oauth/google/status",tags=["Connections"])
    async def oauth_status(context:TenantContext=Depends(require_context)):
        return {"provider":"google_analytics","configured":oauth_manager.configured,"enabled":settings.google_oauth_enabled,"productionApproved":settings.google_oauth_production_approved,"requiredScopes":[ANALYTICS_READONLY_SCOPE],"connections":await call(database.list_oauth_connections,context)}

    @app.post("/api/oauth/google/authorize",tags=["Connections"])
    async def oauth_authorize(context:TenantContext=Depends(require_context)):
        try:
            context.require_role(frozenset({"agency_owner","agency_admin","client_admin"}))
            authorization=oauth_manager.create_authorization(context.organization_id,context.user_id)
            verifier_ciphertext=await call(oauth_manager.cipher.encrypt,authorization["verifier"],f"oauth-state:{authorization['stateHash']}")
            await call(database.create_oauth_state,context,authorization["stateHash"],verifier_ciphertext,[ANALYTICS_READONLY_SCOPE],settings.google_oauth_redirect_uri,authorization["expiresAt"])
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        except RuntimeError as error: raise HTTPException(status_code=503,detail=str(error)) from error
        return {"authorizationUrl":authorization["url"],"expiresAt":authorization["expiresAt"].isoformat(),"scope":ANALYTICS_READONLY_SCOPE}

    @app.get("/oauth/google/callback",response_class=HTMLResponse,include_in_schema=False)
    async def oauth_callback(state:str="",code:str="",error:str=""):
        if error: raise HTTPException(status_code=400,detail="google_oauth_authorization_denied")
        if not state or not code: raise HTTPException(status_code=400,detail="google_oauth_callback_missing_parameters")
        try:
            signed=oauth_manager.verify_state(state)
            context=await call(database.context_for_oauth_callback,signed["organizationId"],signed["userId"])
            state_hash=hashlib.sha256(state.encode()).hexdigest()
            stored=await call(database.consume_oauth_state,context,state_hash)
            verifier=await call(oauth_manager.cipher.decrypt,stored["pkce_verifier_ciphertext"],f"oauth-state:{state_hash}")
            token=await call(oauth_manager.exchange_code,code,verifier)
            refresh_ciphertext=await call(oauth_manager.cipher.encrypt,token["refresh_token"],f"oauth-refresh:{context.organization_id}")
            connection=await call(database.save_oauth_connection,context,refresh_ciphertext,token.get("scope","").split(),None)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        except Exception as error: raise HTTPException(status_code=502,detail=f"google_oauth_connection_failed:{type(error).__name__}") from error
        agency_url=f"{settings.service_url.rstrip('/')}/agency" if settings.service_url.startswith("https://") else "/"
        return f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>Google Analytics connected</title><main><h1>Connection received</h1><p>Connection {connection['connectionId']} is pending assignment review.</p><p><a href='{agency_url}'>Return to the private agency console</a></p></main>"

    @app.post("/api/oauth/google/connections/{connection_id}/revoke",tags=["Connections"])
    async def oauth_revoke(connection_id:str,request:OAuthRevokeRequest,context:TenantContext=Depends(require_context)):
        try:
            ciphertext=await call(database.oauth_refresh_ciphertext,context,connection_id)
            refresh_token=await call(oauth_manager.cipher.decrypt,ciphertext,f"oauth-refresh:{context.organization_id}")
            await call(oauth_manager.revoke,refresh_token)
            removed=await call(database.revoke_oauth_connection,context,connection_id,request.deleteToken)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        except Exception as error: raise HTTPException(status_code=502,detail=f"google_oauth_revocation_failed:{type(error).__name__}") from error
        if not removed: raise HTTPException(status_code=404,detail="oauth_connection_not_found")
        return {"connectionId":connection_id,"status":"offboarded" if request.deleteToken else "revoked","tokenDeleted":request.deleteToken}

    async def oauth_credential(context:TenantContext,connection_id:str):
        ciphertext=await call(database.oauth_refresh_ciphertext,context,connection_id)
        refresh_token=await call(oauth_manager.cipher.decrypt,ciphertext,f"oauth-refresh:{context.organization_id}")
        return OAuthCredential(refresh_token,settings.google_oauth_client_id,settings.google_oauth_client_secret)

    @app.get("/api/oauth/google/connections/{connection_id}/properties",tags=["Connections"])
    async def oauth_properties(connection_id:str,context:TenantContext=Depends(require_context)):
        try:
            context.require_role(frozenset({"agency_owner","agency_admin","client_admin"}))
            credential=await oauth_credential(context,connection_id)
            properties=await call(credential.list_accessible_properties)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        except Exception as error: raise HTTPException(status_code=502,detail=f"google_property_discovery_failed:{type(error).__name__}") from error
        return {"connectionId":connection_id,"properties":properties,"readOnly":True}

    @app.post("/api/oauth/google/connections/{connection_id}/assign",status_code=201,tags=["Connections"])
    async def oauth_assign(connection_id:str,request:OAuthAssignmentRequest,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,request.websiteId): raise HTTPException(status_code=403,detail="forbidden_website")
        if not request.propertyId.isdigit() or not request.streamId.isdigit(): raise HTTPException(status_code=422,detail="invalid_google_resource_identifier")
        try:
            context.require_role(frozenset({"agency_owner","agency_admin","client_admin"}))
            credential=await oauth_credential(context,connection_id)
            admin=GA4Admin(credential); inspected=await call(admin.inspect,request.propertyId)
            stream_name=f"properties/{request.propertyId}/dataStreams/{request.streamId}"
            stream=next((item for item in inspected["streams"] if item.get("name")==stream_name and item.get("type")=="WEB_DATA_STREAM"),None)
            if not stream: raise HTTPException(status_code=422,detail="approved_web_stream_not_accessible")
            prop=inspected["property"]
            value=await call(database.assign_oauth_property,context,connection_id,request.websiteId,request.propertyId,request.streamId,prop.get("display_name"),prop.get("time_zone"),prop.get("currency_code"),stream.get("display_name"),stream.get("web_stream_data",{}).get("measurement_id"))
        except HTTPException: raise
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        except Exception as error: raise HTTPException(status_code=502,detail=f"google_assignment_validation_failed:{type(error).__name__}") from error
        return {"assignment":value,"validation":{"property":f"properties/{request.propertyId}","stream":stream_name,"scope":ANALYTICS_READONLY_SCOPE}}

    @app.get("/api/websites/{website_id}/offboarding-preview",tags=["Governance"])
    async def offboarding_preview(website_id:str,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        try: preview=await call(database.deletion_preview,context,website_id)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        return {"preview":preview,"retention":await call(database.retention_policy,context)}

    @app.post("/api/websites/{website_id}/offboarding",status_code=202,tags=["Governance"])
    async def request_offboarding(website_id:str,request:OffboardingRequest,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        try: return await call(database.request_offboarding,context,website_id,request.confirmationWebsiteId)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error

    @app.delete("/api/offboarding/{request_id}",status_code=204,tags=["Governance"])
    async def cancel_offboarding(request_id:str,context:TenantContext=Depends(require_context)):
        try: cancelled=await call(database.cancel_offboarding,context,request_id)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        if not cancelled: raise HTTPException(status_code=404,detail="offboarding_request_not_found")
        return Response(status_code=204)

    @app.post("/internal/retention",dependencies=[Depends(require_internal)],include_in_schema=False)
    async def retention():
        expired=await call(database.purge_retention)
        deletions=await call(database.execute_due_deletions,5)
        return {"status":"ok","retention":expired,"deletions":deletions}

    @app.get("/api/websites/{website_id}/external-sources",tags=["Connections"])
    async def external_sources(website_id:str,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        return {"websiteId":website_id,"sources":await call(database.external_source_status,context,website_id),"priorityOrder":["google_ads","search_console","call_tracking","crm_booking"]}

    @app.post("/api/websites/{website_id}/external-sources",status_code=201,tags=["Connections"])
    async def register_external_source(website_id:str,request:SourceConnectionRequest,context:TenantContext=Depends(require_agency)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        if not PINNED_SECRET.fullmatch(request.credentialSecretReference):
            raise HTTPException(status_code=422,detail="version_pinned_secret_reference_required")
        if request.externalAccountId and len(request.externalAccountId)>200: raise HTTPException(status_code=422,detail="invalid_external_account_id")
        if request.sourceType=="google_ads" and not request.externalAccountId: raise HTTPException(status_code=422,detail="google_ads_customer_id_required")
        if request.sourceType=="search_console" and not isinstance(request.configuration.get("siteUrl"),str): raise HTTPException(status_code=422,detail="search_console_site_required")
        if request.sourceType in {"call_tracking","crm_booking"} and not isinstance(request.configuration.get("identityPolicyReference"),str): raise HTTPException(status_code=422,detail="identity_policy_reference_required")
        if "timezone" in request.configuration:
            try: ZoneInfo(request.configuration["timezone"])
            except (TypeError,ZoneInfoNotFoundError): raise HTTPException(status_code=422,detail="invalid_source_timezone")
        for key,minimum,maximum in (("lookbackDays",1,90),("finalizationLagDays",1,14)):
            if key in request.configuration and (isinstance(request.configuration[key],bool) or not isinstance(request.configuration[key],int) or not minimum<=request.configuration[key]<=maximum):
                raise HTTPException(status_code=422,detail=f"invalid_{key}")
        try: value=await call(database.register_source_connection,context,website_id,request.sourceType,request.credentialSecretReference,request.externalAccountId,request.configuration)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        return {"connection":value,"nextStep":"validate_source_access_before_approval"}

    @app.post("/api/websites/{website_id}/external-sources/{source_type}/approve",tags=["Connections"])
    async def approve_external_source(website_id:str,source_type:Literal["google_ads","search_console","call_tracking","crm_booking"],request:SourceApprovalRequest,context:TenantContext=Depends(require_agency)):
        if request.confirmationSourceType != source_type: raise HTTPException(status_code=422,detail="source_approval_confirmation_mismatch")
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        try:
            target=await call(database.source_connection_for_website,context,website_id,source_type)
            connector=await call(source_connector_factory.create,target)
            validation=await call(connector.validate_access)
            if validation.get("status") not in {"ok","configuration_valid"}: raise RuntimeError("source_access_validation_failed")
            return {"connection":await call(database.approve_source_connection,context,website_id,source_type,request.approvalReference,validation)}
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error

    @app.post("/api/websites/{website_id}/external-sources/{source_type}/outcomes",tags=["Connections"])
    async def ingest_outcomes(website_id:str,source_type:Literal["call_tracking","crm_booking"],request:OutcomeBatchRequest,context:TenantContext=Depends(require_agency)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        try:
            target=await call(database.source_connection_for_website,context,website_id,source_type)
            if target["approval_status"]!="approved" or target["disabled_at"]: raise PermissionError("external_source_not_approved")
            normalizer=await call(source_connector_factory.create,target)
            connector=await call(BufferedOutcomeConnector,normalizer,request.records)
            dates=[date.fromisoformat(row["outcomeDate"]) for row in connector.rows]
            result=await call(ExternalSyncEngine(database,str(target["id"]),connector).run,min(dates),max(dates),request.requestId)
            return {"websiteId":website_id,**result}
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error

    @app.delete("/api/websites/{website_id}/external-sources/{source_type}",status_code=204,tags=["Connections"])
    async def disable_external_source(website_id:str,source_type:Literal["google_ads","search_console","call_tracking","crm_booking"],context:TenantContext=Depends(require_agency)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        try: disabled=await call(database.disable_source_connection,context,website_id,source_type)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        if not disabled: raise HTTPException(status_code=404,detail="source_connection_not_found")
        return Response(status_code=204)

    @app.get("/api/websites/{website_id}/business-outcomes",tags=["Reporting"])
    async def business_outcomes(website_id:str,startDate:date,endDate:date,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        if endDate<startDate or (endDate-startDate).days>366: raise HTTPException(status_code=422,detail="invalid_business_outcome_period")
        return await call(database.business_outcomes,context,website_id,startDate,endDate)

    @app.get("/api/websites/{website_id}/paid-performance",tags=["Reporting"])
    async def paid_performance(website_id:str,startDate:date,endDate:date,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        if endDate<startDate or (endDate-startDate).days>366: raise HTTPException(status_code=422,detail="invalid_paid_performance_period")
        return await call(database.google_ads_performance,context,website_id,startDate,endDate)

    @app.get("/api/websites/{website_id}/search-performance",tags=["Reporting"])
    async def search_performance(website_id:str,startDate:date,endDate:date,context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        if endDate<startDate or (endDate-startDate).days>366: raise HTTPException(status_code=422,detail="invalid_search_performance_period")
        return await call(database.search_console_performance,context,website_id,startDate,endDate)

    @app.get("/api/memberships",tags=["Access"])
    async def memberships(context:TenantContext=Depends(require_agency)):
        return {"organizationId":context.organization_id,"memberships":await call(database.list_memberships,context)}

    @app.post("/api/memberships",status_code=201,tags=["Access"])
    async def add_membership(request:MembershipRequest,context:TenantContext=Depends(require_agency)):
        email=request.email.strip().lower()
        if "@" not in email or len(email)>254: raise HTTPException(status_code=422,detail="invalid_member_email")
        try: value=await call(database.upsert_membership,context,email,request.role)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        return {"membership":value,"cloudRunIamRequired":True}

    @app.delete("/api/memberships/{user_id}",status_code=204,tags=["Access"])
    async def remove_membership(user_id:str,context:TenantContext=Depends(require_agency)):
        try: removed=await call(database.remove_membership,context,user_id)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        if not removed: raise HTTPException(status_code=404,detail="membership_not_found")
        return Response(status_code=204)

    @app.post("/api/onboarding/workflows", tags=["Onboarding"])
    async def create_onboarding_workflow(request: OnboardingWorkflowRequest, context: TenantContext = Depends(require_onboarding_admin)):
        try:
            domain = normalize_canonical_domain(request.canonicalDomain)
            value = await onboarding_call(database.create_onboarding_workflow, context, request.idempotencyKey, request.companyId, request.companyName, request.websiteId, domain, request.contractSlug)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error:
            raise onboarding_error(error) from error
        return JSONResponse(status_code=200 if value.get("idempotentReplay") else 201, content=jsonable_encoder(value))

    @app.get("/api/onboarding/workflows/{workflow_id}", tags=["Onboarding"])
    async def get_onboarding_workflow(workflow_id: str, context: TenantContext = Depends(require_onboarding_admin)):
        try: return await onboarding_call(database.onboarding_workflow, context, workflow_id)
        except PermissionError as error: raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error: raise onboarding_error(error) from error

    @app.post("/api/onboarding/workflows/{workflow_id}/governance", tags=["Onboarding"])
    async def record_onboarding_governance(workflow_id: str, request: OnboardingGovernanceRequest, context: TenantContext = Depends(require_onboarding_admin)):
        try:
            value = await onboarding_call(database.record_onboarding_governance, context, workflow_id, request.governanceStatus, request.consentStatus, request.governanceReference, request.consentReference)
        except PermissionError as error: raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error: raise onboarding_error(error) from error
        return value

    @app.post("/api/onboarding/workflows/{workflow_id}/connections", tags=["Onboarding"])
    async def record_onboarding_connection(workflow_id: str, request: OnboardingConnectionRequest, context: TenantContext = Depends(require_onboarding_admin)):
        connection_kind = request.connectionKind or request.sourceType
        if not connection_kind:
            raise HTTPException(status_code=422, detail="connection_kind_required")
        try:
            value = await onboarding_call(database.register_onboarding_connection, context, workflow_id, connection_kind, request.mode, request.idempotencyKey, request.credentialType, request.credentialReference, request.externalPropertyId, request.externalStreamId, request.externalAccountId, request.configuration, request.deferReason)
        except PermissionError as error: raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error: raise onboarding_error(error) from error
        return value

    @app.post("/api/onboarding/workflows/{workflow_id}/first-sync", tags=["Onboarding"])
    async def onboarding_first_sync(workflow_id: str, request: OnboardingFirstSyncRequest, context: TenantContext = Depends(require_onboarding_admin)):
        try: return await onboarding_call(database.onboarding_first_sync, context, workflow_id, request.action, request.idempotencyKey)
        except PermissionError as error: raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error: raise onboarding_error(error) from error

    @app.get("/api/onboarding/workflows/{workflow_id}/first-sync", tags=["Onboarding"])
    async def get_onboarding_first_sync(workflow_id: str, context: TenantContext = Depends(require_onboarding_admin)):
        try:
            value = await onboarding_call(database.onboarding_first_sync, context, workflow_id, "check", None)
            return {"workflowId": value["workflowId"], "firstSync": value["firstSync"]}
        except PermissionError as error: raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error: raise onboarding_error(error) from error

    @app.post("/api/onboarding/workflows/{workflow_id}/memberships", status_code=201, tags=["Onboarding"])
    async def add_onboarding_client_membership(workflow_id: str, request: OnboardingClientMembershipRequest, context: TenantContext = Depends(require_onboarding_admin)):
        try: return await onboarding_call(database.add_onboarding_client_membership, context, workflow_id, request.email, request.role, request.authorizationReference)
        except PermissionError as error: raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error: raise onboarding_error(error) from error

    @app.get("/api/onboarding/workflows/{workflow_id}/checklist", tags=["Onboarding"])
    async def onboarding_checklist(workflow_id: str, context: TenantContext = Depends(require_onboarding_admin)):
        try: return await onboarding_call(database.onboarding_checklist, context, workflow_id)
        except PermissionError as error: raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error: raise onboarding_error(error) from error

    @app.get("/api/onboarding/workflows/{workflow_id}/handoff", tags=["Onboarding"])
    async def onboarding_handoff(workflow_id: str, context: TenantContext = Depends(require_onboarding_admin)):
        try:
            value = await onboarding_call(database.onboarding_checklist, context, workflow_id)
            return {"workflowId": value["workflowId"], "status": value["status"], "handoff": value["handoff"], "externalGates": value["externalGates"]}
        except PermissionError as error: raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error: raise onboarding_error(error) from error

    @app.post("/internal/schedule",dependencies=[Depends(require_internal)],include_in_schema=False)
    async def schedule(x_cloudscheduler_scheduletime: str | None = Header(default=None)):
        queue = task_queue or TaskQueue(settings)
        scheduled_for=x_cloudscheduler_scheduletime or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        targets=await call(database.active_sync_targets)
        return {"status":"accepted","scheduledFor":scheduled_for,"assignments":len(targets),"tasks":await call(queue.enqueue_periods,scheduled_for,targets)}

    @app.post("/internal/external-sources/schedule",dependencies=[Depends(require_internal)],include_in_schema=False)
    async def schedule_external_sources(x_cloudscheduler_scheduletime: str | None = Header(default=None)):
        queue = task_queue or TaskQueue(settings)
        scheduled_for=x_cloudscheduler_scheduletime or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        targets=await call(database.active_external_sync_targets)
        tasks=[]
        for target in targets:
            start_date,end_date=external_sync_window(scheduled_for,target)
            tasks.append({"connectionId":target["connection_id"],"sourceType":target["source_type"],"startDate":start_date.isoformat(),"endDate":end_date.isoformat()})
        return {"status":"accepted","scheduledFor":scheduled_for,"connections":len(tasks),"tasks":await call(queue.enqueue_external_sources,scheduled_for,tasks)}

    @app.post("/internal/external-sources/sync",dependencies=[Depends(require_internal)],include_in_schema=False)
    async def sync_external_source(request:ExternalSyncRequest):
        if request.endDate<request.startDate or (request.endDate-request.startDate).days>366: raise HTTPException(status_code=422,detail="invalid_external_sync_period")
        try:
            target=await call(database.source_sync_target,request.connectionId)
            if target["source_type"] != request.sourceType: raise PermissionError("external_source_type_mismatch")
            connector=await call(source_connector_factory.create,target)
            return await call(ExternalSyncEngine(database,request.connectionId,connector).run,request.startDate,request.endDate)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error

    @app.post("/internal/sync",dependencies=[Depends(require_internal)],include_in_schema=False)
    async def sync(request: SyncRequest, x_cloudtasks_taskretrycount: int = Header(default=0)):
        target=await call(database.sync_target,request.assignmentId) if request.assignmentId else None
        credential=AdcCredential()
        run_site=site; assignment_id=None
        if target:
            assignment_id=target["assignment_id"]
            run_site=Site(target["website_id"],target["company_id"],target["company"],target["canonical_domain"],"live",target["property_timezone"] or site.business_timezone,target["property_id"],target["stream_id"] or "",target["measurement_id"] or (site.measurement_id if target["website_id"]==site.site_id else ""),target["property_timezone"],"active","approved")
            if target["credential_type"]=="oauth":
                secret=await call(database.internal_oauth_credential,target["analytics_connection_id"])
                refresh_token=await call(oauth_manager.cipher.decrypt,secret["encrypted_refresh_token"],f"oauth-refresh:{secret['organization_id']}")
                credential=OAuthCredential(refresh_token,settings.google_oauth_client_id,settings.google_oauth_client_secret)
        engine=SyncEngine(database,GA4Reporter(run_site,credential.get_authorized_client()),GA4Admin(credential),run_site)
        return await call(engine.run,request.period,request.scheduledFor,x_cloudtasks_taskretrycount >= 4,assignment_id)

    @app.post("/api/operations/sync-jobs/{job_id}/replay",tags=["Operations"])
    async def replay(job_id: str,context:TenantContext=Depends(require_agency)):
        try: payload=await call(database.replay_payload,job_id)
        except HTTPException: raise
        except ValueError as error: raise HTTPException(status_code=404,detail=str(error)) from error
        replay_request=SyncRequest(period=payload["period"],scheduledFor=payload["scheduledFor"],assignmentId=payload.get("assignmentId"))
        return await sync(replay_request,0)

    return app


load_dotenv(); app = create_app()
def migrate():
    """Apply pending migrations through the application's database connection.

    This entrypoint is intended for a Cloud Run Job or deployment preamble so
    migrations are always applied before portal or reporting processes start.
    """
    load_dotenv(); site = load_site(); settings = Settings.from_environment(); settings.validate(site)
    database = Database(settings)
    result = database.migrate()
    database.close()
    return result


def run(): uvicorn.run("app.main:app",host=app.state.settings.host,port=app.state.settings.port)
