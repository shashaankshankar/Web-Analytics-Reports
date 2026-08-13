from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .config import Settings, load_dotenv, load_site
from .credentials import AdcCredential, GA4Admin
from .dashboard import dashboard_html
from .ga4 import GA4Reporter
from .storage import Database
from .sync import SyncEngine
from .tasks import PERIODS, TaskQueue

Period = Literal["7d", "28d", "90d", "this_month", "last_month"]
bearer = HTTPBearer(auto_error=False, description="Dashboard API token when token authentication is enabled")


class SyncRequest(BaseModel):
    period: Period
    scheduledFor: str | None = None


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

    def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if settings.auth_mode == "cloud_run": return
        if not settings.live_enabled: return
        supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
        if not hmac.compare_digest(supplied, settings.api_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

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
        except Exception as error: raise HTTPException(status_code=502,detail=f"upstream_operation_failed:{type(error).__name__}") from error

    def snapshot(view: str, period: str):
        if not database.configured: raise HTTPException(status_code=503,detail="production_database_not_configured")
        value = database.latest_snapshot(view,period)
        if value is None: raise HTTPException(status_code=503,detail="report_not_synced")
        return value

    @app.get("/",response_class=HTMLResponse,include_in_schema=False)
    def home(): return f"<!doctype html><title>Measurement Platform</title><style>body{{font:16px system-ui;margin:3rem;max-width:55rem}}</style><h1>Measurement &amp; Reporting Platform</h1><p>{site.company} stored GA4 reporting service.</p><p><a href='/dashboard'>Open dashboard</a> · <a href='/docs'>Open API documentation</a></p>"

    @app.get("/dashboard",response_class=HTMLResponse,include_in_schema=False,dependencies=[Depends(require_token)])
    def dashboard(): return dashboard_html(site)

    @app.get("/health",tags=["Operations"])
    @app.get("/healthz",tags=["Operations"],include_in_schema=False)
    def healthz(): return {"status":"ok","runtime":"fastapi","liveReporting":settings.live_enabled}

    @app.get("/ready",tags=["Operations"])
    async def ready():
        db = await call(database.health)
        ready_state = settings.live_enabled and db.get("status") == "ok"
        return {"status":"ready" if ready_state else "not_ready","liveReporting":settings.live_enabled,"database":db}

    @app.get("/api/portfolio/summary",dependencies=[Depends(require_token)],tags=["Reporting"])
    async def portfolio():
        sync = await call(database.sync_status) if database.configured else {"status":"disabled"}
        return {"organizationId":"org_agency","websites":[{"websiteId":site.site_id,"company":site.company,"canonicalDomain":site.canonical_domain,"deploymentState":site.deployment_status,"collectionState":site.collection_status,"governanceState":site.governance_status,"sync":sync,"measurementState":"stored_reporting" if sync.get("lastSuccessfulSync") else "awaiting_first_sync"}]}

    @app.get("/api/companies/{company_id}/overview",dependencies=[Depends(require_token)],tags=["Reporting"])
    async def overview(company_id:str,period:Period="28d"):
        if company_id != site.company_id: raise HTTPException(status_code=403,detail="forbidden_company")
        return await call(snapshot,"overview",period)

    @app.get("/api/websites/{website_id}/acquisition",dependencies=[Depends(require_token)],tags=["Reporting"])
    async def acquisition(website_id:str,period:Period="28d"):
        if website_id != site.site_id: raise HTTPException(status_code=403,detail="forbidden_website")
        return await call(snapshot,"acquisition",period)

    @app.get("/api/websites/{website_id}/conversion",dependencies=[Depends(require_token)],tags=["Reporting"])
    async def conversion(website_id:str,period:Period="28d"):
        if website_id != site.site_id: raise HTTPException(status_code=403,detail="forbidden_website")
        return await call(snapshot,"conversion",period)

    @app.get("/api/websites/{website_id}/landing-pages",dependencies=[Depends(require_token)],tags=["Reporting"])
    async def landing_pages(website_id:str,period:Period="28d"):
        if website_id != site.site_id: raise HTTPException(status_code=403,detail="forbidden_website")
        return await call(snapshot,"landing-pages",period)

    @app.get("/api/websites/{website_id}/events",dependencies=[Depends(require_token)],tags=["Reporting"])
    async def events(website_id:str,period:Period="28d"):
        if website_id != site.site_id: raise HTTPException(status_code=403,detail="forbidden_website")
        return await call(snapshot,"events",period)

    @app.get("/api/websites/{website_id}/measurement-health",dependencies=[Depends(require_token)],tags=["Operations"])
    async def measurement_health(website_id:str,period:Period="28d"):
        if website_id != site.site_id: raise HTTPException(status_code=403,detail="forbidden_website")
        sync = await call(database.sync_status) if database.configured else {"status":"disabled","quality":{}}
        admin_health = await call(database.latest_measurement_health) if database.configured and hasattr(database,"latest_measurement_health") else None
        event_health = await call(snapshot,"events",period) if database.configured else {"expectedEvents":[],"otherObservedEvents":[],"prohibitedEvents":[]}
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

    @app.get("/api/websites/{website_id}/sync-status",dependencies=[Depends(require_token)],tags=["Operations"])
    async def status_(website_id:str):
        if website_id != site.site_id: raise HTTPException(status_code=403,detail="forbidden_website")
        return {"websiteId":site.site_id,**(await call(database.sync_status) if database.configured else {"status":"disabled","reason":"database_not_configured"})}

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

    @app.post("/api/operations/sync-jobs/{job_id}/replay",dependencies=[Depends(require_token)],tags=["Operations"])
    async def replay(job_id: str):
        try: payload=await call(database.replay_payload,job_id)
        except HTTPException: raise
        except ValueError as error: raise HTTPException(status_code=404,detail=str(error)) from error
        credential=AdcCredential()
        engine=SyncEngine(database,ga4(),GA4Admin(credential),site)
        return await call(engine.run,payload["period"],payload["scheduledFor"],False)

    return app


load_dotenv(); app = create_app()
def run(): uvicorn.run("app.main:app",host=app.state.settings.host,port=app.state.settings.port)
