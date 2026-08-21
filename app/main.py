from __future__ import annotations

import logging
from typing import Optional
from pydantic import BaseModel, Field

from app.cli import generate_report
from app.analytics.contracts import ReportType
from app.config import load_client_config_by_slug, is_production_dispatch_allowed

logger = logging.getLogger(__name__)

# Optional lightweight web service / webhook trigger support
try:
    from fastapi import FastAPI, HTTPException
    
    app = FastAPI(
        title="Client Growth Reports API",
        version="1.0.0",
        description="On-demand analytics report generation",
    )

    class ReportTriggerRequest(BaseModel):
        client_slug: str
        report_type: ReportType = ReportType.PERFORMANCE_28D
        days: int = Field(default=28, ge=1, le=90)
        send_email: bool = False
        mock_data: bool = False
        dry_run: bool = False

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "client-growth-reports"}

    @app.post("/reports/generate")
    def trigger_report(req: ReportTriggerRequest):
        try:
            load_client_config_by_slug(req.client_slug)
            if req.send_email and not is_production_dispatch_allowed(req.client_slug):
                raise HTTPException(status_code=403, detail="Client is not enabled for production delivery.")
            if req.send_email and req.mock_data:
                raise HTTPException(status_code=400, detail="Mock data cannot be emailed.")
            briefing = generate_report(
                client_slug=req.client_slug,
                report_type=req.report_type,
                days=req.days,
                send_email=req.send_email,
                mock_data=req.mock_data,
                dry_run=req.dry_run,
            )
            return {
                "status": "success",
                "client_id": briefing.client_id,
                "company_name": briefing.company_name,
                "generated_at": briefing.generated_at,
                "period": briefing.period_label,
                "executive_summary": briefing.insights.executive_summary,
            }
        except HTTPException:
            raise
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            logger.exception(
                "REPORT_EVENT report_generation_failed client_slug=%s report_type=%s",
                req.client_slug,
                req.report_type.value,
            )
            raise HTTPException(status_code=500, detail="Report generation failed; see protected service logs.")

except ImportError:
    app = None
