from __future__ import annotations
import hmac
from contextlib import asynccontextmanager
from typing import Literal
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool
from .config import Settings, load_dotenv, load_site
from .ga4 import GA4Reporter

Period = Literal["7d", "28d", "90d", "this_month", "last_month"]
bearer = HTTPBearer(auto_error=False, description="Local dashboard API token")
def sync_status(settings): return {"status":"direct_query_ready" if settings.live_enabled else "disabled","lastSuccessfulSync":None,"lastCompleteDate":None,"queuedJobs":0,"failedJobs":0,"reason":"live_ga4_data_api_queries_refresh_on_request;_persistence_not_configured" if settings.live_enabled else "live_reporting_not_enabled"}

def create_app(settings=None, reporter=None):
    load_dotenv(); site = load_site(); settings = settings or Settings.from_environment(); settings.validate(site)
    @asynccontextmanager
    async def lifespan(app): app.state.reporter = reporter; yield
    app = FastAPI(title="Measurement & Reporting Platform",version="0.2.0",description="Read-only, privacy-aware GA4 reporting for House of Dental.",lifespan=lifespan)
    app.state.settings, app.state.site = settings, site
    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request); response.headers.update({"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer"}); return response
    def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not settings.live_enabled: return
        supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
        if not hmac.compare_digest(supplied, settings.api_token): raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    def ga4():
        if not settings.live_enabled: raise HTTPException(status_code=503,detail="live_reporting_not_enabled")
        if app.state.reporter is None: app.state.reporter = GA4Reporter(site)
        return app.state.reporter
    async def call(method,*args):
        try: return await run_in_threadpool(method,*args)
        except Exception as error: raise HTTPException(status_code=502,detail="ga4_request_failed") from error
    @app.get("/",response_class=HTMLResponse,include_in_schema=False)
    def home(): return "<!doctype html><title>Measurement Platform</title><style>body{font:16px system-ui;margin:3rem;max-width:55rem}</style><h1>Measurement &amp; Reporting Platform</h1><p>House of Dental live GA4 reporting API.</p><p><a href='/docs'>Open interactive API docs</a></p><p>Use <strong>Authorize</strong> in the docs to enter the local bearer token before testing data endpoints.</p>"
    @app.get("/healthz",tags=["Operations"])
    def healthz(): return {"status":"ok","runtime":"fastapi","liveReporting":settings.live_enabled}
    @app.get("/api/portfolio/summary",dependencies=[Depends(require_token)],tags=["Reporting"])
    def portfolio(): return {"organizationId":"org_agency","websites":[{"websiteId":site.site_id,"company":site.company,"canonicalDomain":site.canonical_domain,"deploymentState":site.deployment_status,"collectionState":site.collection_status,"governanceState":site.governance_status,"sync":sync_status(settings),"measurementState":"live_collection_reporting" if settings.live_enabled else "setup"}]}
    @app.get("/api/companies/{company_id}/overview",dependencies=[Depends(require_token)],tags=["Reporting"])
    async def overview(company_id:str,period:Period="28d"):
        if company_id != site.company_id: raise HTTPException(status_code=403,detail="forbidden_company")
        return await call(ga4().overview,period)
    @app.get("/api/websites/{website_id}/acquisition",dependencies=[Depends(require_token)],tags=["Reporting"])
    async def acquisition(website_id:str,period:Period="28d"):
        if website_id != site.site_id: raise HTTPException(status_code=403,detail="forbidden_website")
        return await call(ga4().acquisition,period)
    @app.get("/api/websites/{website_id}/measurement-health",dependencies=[Depends(require_token)],tags=["Operations"])
    async def measurement_health(website_id:str):
        if website_id != site.site_id: raise HTTPException(status_code=403,detail="forbidden_website")
        if not settings.live_enabled: return {"websiteId":site.site_id,"state":"blocked","checks":[{"key":"live_reporting","state":"blocked","detail":"live_reporting_not_enabled"}],"sync":sync_status(settings)}
        await call(ga4().report,"7d",[],["activeUsers"])
        return {"websiteId":site.site_id,"deploymentStatus":site.deployment_status,"publicCollectionStatus":site.collection_status,"governanceStatus":site.governance_status,"state":"ready","checks":[{"key":"ga4_data_api","state":"ok","detail":"analytics.readonly via ADC"},{"key":"assignment","state":"ok","detail":f"properties/{site.property_id}/dataStreams/{site.stream_id}"},{"key":"persistence","state":"warning","detail":"direct GA4 queries only; persistent sync is not configured"}],"sync":sync_status(settings)}
    @app.get("/api/websites/{website_id}/sync-status",dependencies=[Depends(require_token)],tags=["Operations"])
    def status_(website_id:str):
        if website_id != site.site_id: raise HTTPException(status_code=403,detail="forbidden_website")
        return {"websiteId":site.site_id,**sync_status(settings)}
    return app

load_dotenv(); app = create_app()
def run(): uvicorn.run("app.main:app",host=app.state.settings.host,port=app.state.settings.port)
