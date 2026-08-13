from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .config import Settings, load_dotenv, load_site
from .auth import AGENCY_ROLES, TenantContext
from .credentials import AdcCredential, GA4Admin
from .dashboard import agency_dashboard_html, dashboard_html
from .ga4 import GA4Reporter
from .reports import build_client_pdf
from .storage import Database
from .sync import SyncEngine
from .tasks import PERIODS, TaskQueue

Period = Literal["7d", "28d", "90d", "this_month", "last_month"]
bearer = HTTPBearer(auto_error=False, description="Dashboard API token when token authentication is enabled")


class SyncRequest(BaseModel):
    period: Period
    scheduledFor: str | None = None


class AnnotationRequest(BaseModel):
    date: date
    type: Literal["site_launch", "campaign_launch", "tracking_change", "website_redesign", "major_outage", "measurement_change"]
    note: str


class GoalRequest(BaseModel):
    metric: str
    target: float
    effectiveFrom: date
    effectiveTo: date | None = None


def create_app(settings=None, reporter=None, database=None, task_queue=None):
    load_dotenv(); site = load_site(); settings = settings or Settings.from_environment(); settings.validate(site)
    database = database or Database(settings)

    @asynccontextmanager
    async def lifespan(app):
        app.state.reporter = reporter
        yield
        database.close()

    app = FastAPI(title="Measurement & Reporting Platform",version="1.0.0",description="Stored, privacy-aware GA4 reporting for House of Dental.",lifespan=lifespan)
    app.state.settings, app.state.site, app.state.database = settings, site, database

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update({"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer","X-Frame-Options":"DENY","Permissions-Policy":"camera=(), microphone=(), geolocation=()","Content-Security-Policy":"default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    def require_context(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), x_organization_id: str = Header(default="")) -> TenantContext:
        if settings.auth_mode != "cloud_run" and settings.live_enabled:
            supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
            if not hmac.compare_digest(supplied, settings.api_token):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        try:
            return database.authorize_context(settings.operator_email, x_organization_id or None)
        except PermissionError as error:
            raise HTTPException(status_code=403,detail=str(error)) from error

    def require_agency(context: TenantContext = Depends(require_context)) -> TenantContext:
        try: context.require_role(AGENCY_ROLES)
        except PermissionError as error: raise HTTPException(status_code=403,detail=str(error)) from error
        return context

    def require_internal(x_internal_trigger_token: str = Header(default="")):
        if len(settings.internal_trigger_token) < 32 or not hmac.compare_digest(x_internal_trigger_token,settings.internal_trigger_token):
            raise HTTPException(status_code=401,detail="unauthorized_internal_trigger")

    def ga4():
        if not settings.live_enabled: raise HTTPException(status_code=503,detail="live_reporting_not_enabled")
        if app.state.reporter is None: app.state.reporter = GA4Reporter(site)
        return app.state.reporter

    async def call(method,*args):
        try: return await run_in_threadpool(method,*args)
        except HTTPException: raise
        except PermissionError: raise
        except Exception as error: raise HTTPException(status_code=502,detail=f"upstream_operation_failed:{type(error).__name__}") from error

    def snapshot(context: TenantContext, website_id: str, view: str, period: str):
        if not database.configured: raise HTTPException(status_code=503,detail="production_database_not_configured")
        value = database.latest_snapshot(context,website_id,view,period)
        if value is None: raise HTTPException(status_code=503,detail="report_not_synced")
        return value

    @app.get("/",response_class=HTMLResponse,include_in_schema=False)
    def home(): return f"<!doctype html><title>Measurement Platform</title><style>body{{font:16px system-ui;margin:3rem;max-width:55rem}}</style><h1>Measurement &amp; Reporting Platform</h1><p>{site.company} stored GA4 reporting service.</p><p><a href='/agency'>Open agency console</a> · <a href='/dashboard'>Open client reporting view</a> · <a href='/docs'>Open API documentation</a></p>"

    @app.get("/dashboard",response_class=HTMLResponse,include_in_schema=False,dependencies=[Depends(require_context)])
    def dashboard(): return dashboard_html(site)

    @app.get("/agency",response_class=HTMLResponse,include_in_schema=False)
    def agency(context:TenantContext=Depends(require_agency)): return agency_dashboard_html(site)

    @app.get("/health",tags=["Operations"])
    @app.get("/healthz",tags=["Operations"],include_in_schema=False)
    def healthz(): return {"status":"ok","runtime":"fastapi","liveReporting":settings.live_enabled}

    @app.get("/ready",tags=["Operations"])
    async def ready():
        db = await call(database.health)
        ready_state = settings.live_enabled and db.get("status") == "ok"
        return {"status":"ready" if ready_state else "not_ready","liveReporting":settings.live_enabled,"database":db}

    @app.get("/api/portfolio/summary",tags=["Reporting"])
    async def portfolio(period:Period="28d",context:TenantContext=Depends(require_agency)):
        return await call(database.portfolio_summary,context,period)

    @app.get("/api/companies/{company_id}/overview",tags=["Reporting"])
    async def overview(company_id:str,period:Period="28d",context:TenantContext=Depends(require_context)):
        if not await call(database.company_authorized,context,company_id): raise HTTPException(status_code=403,detail="forbidden_company")
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
    async def measurement_health(website_id:str,period:Period="28d",context:TenantContext=Depends(require_context)):
        if not await call(database.website_authorized,context,website_id): raise HTTPException(status_code=403,detail="forbidden_website")
        sync = await call(database.sync_status,context,website_id) if database.configured else {"status":"disabled","quality":{}}
        admin_health = await call(database.latest_measurement_health,context,website_id) if database.configured and hasattr(database,"latest_measurement_health") else None
        event_health = await call(snapshot,context,website_id,"events",period) if database.configured else {"expectedEvents":[],"otherObservedEvents":[],"prohibitedEvents":[]}
        checks=[
          {"key":"ga4_data_api","state":"ok" if settings.live_enabled else "blocked","detail":"read-only ADC connection enabled" if settings.live_enabled else "live reporting disabled"},
          {"key":"assignment","state":"ok","detail":f"properties/{site.property_id}/dataStreams/{site.stream_id}"},
          {"key":"persistence","state":"ok" if sync.get("lastSuccessfulSync") else "blocked","detail":"stored report execution available" if sync.get("lastSuccessfulSync") else "first sync has not succeeded"},
          {"key":"governance","state":"ok" if site.governance_status == "approved" else "warning","detail":site.governance_status},
          {"key":"collection","state":"warning" if sync.get("quality",{}).get("empty") else "ok","detail":"successful GA4 reports returned no rows" if sync.get("quality",{}).get("empty") else site.collection_status},
        ]
        if admin_health: checks.extend(admin_health["details"].get("checks",[]))
        state="ready" if all(c["state"]=="ok" for c in checks) else "attention_required"
        return {"websiteId":site.site_id,"deploymentStatus":site.deployment_status,"publicCollectionStatus":site.collection_status,"governanceStatus":site.governance_status,"state":state,"contract":{"slug":"local_service_v1","version":1,"approvalStatus":"approved" if site.governance_status == "approved" else "pending_approval"},"lastValidation":admin_health["checkedAt"] if admin_health else None,"requiredEventHealth":event_health.get("expectedEvents",[]),"unexpectedEvents":event_health.get("otherObservedEvents",[]),"prohibitedEventDetection":event_health.get("prohibitedEvents",[]),"leadEventActivity":next((item for item in event_health.get("expectedEvents",[]) if item["event"]=="generate_lead"),None),"consentConfiguration":{"required":True,"approvalStatus":"approved" if site.governance_status == "approved" else "pending_authorized_review"},"adminHealth":admin_health,"checks":checks,"sync":sync}

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
        overview_data=await call(snapshot,context,website_id,"overview",period)
        acquisition_data=await call(snapshot,context,website_id,"acquisition",period)
        annotation_data=await call(database.list_annotations,context,website_id)
        content=await call(build_client_pdf,site,period,overview_data,acquisition_data,annotation_data)
        return Response(content=content,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{website_id}-{period}-report.pdf"'})

    @app.post("/internal/schedule",dependencies=[Depends(require_internal)],include_in_schema=False)
    async def schedule(x_cloudscheduler_scheduletime: str | None = Header(default=None)):
        queue = task_queue or TaskQueue(settings)
        scheduled_for=x_cloudscheduler_scheduletime or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        return {"status":"accepted","scheduledFor":scheduled_for,"tasks":await call(queue.enqueue_periods,scheduled_for)}

    @app.post("/internal/sync",dependencies=[Depends(require_internal)],include_in_schema=False)
    async def sync(request: SyncRequest, x_cloudtasks_taskretrycount: int = Header(default=0)):
        credential=AdcCredential()
        engine=SyncEngine(database,ga4(),GA4Admin(credential),site)
        return await call(engine.run,request.period,request.scheduledFor,x_cloudtasks_taskretrycount >= 4)

    @app.post("/api/operations/sync-jobs/{job_id}/replay",tags=["Operations"])
    async def replay(job_id: str,context:TenantContext=Depends(require_agency)):
        try: payload=await call(database.replay_payload,job_id)
        except HTTPException: raise
        except ValueError as error: raise HTTPException(status_code=404,detail=str(error)) from error
        credential=AdcCredential()
        engine=SyncEngine(database,ga4(),GA4Admin(credential),site)
        return await call(engine.run,payload["period"],payload["scheduledFor"],False)

    return app


load_dotenv(); app = create_app()
def run(): uvicorn.run("app.main:app",host=app.state.settings.host,port=app.state.settings.port)
