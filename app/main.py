from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

from app.cli import generate_report
from app.config import ClientConfig, list_available_clients, load_client_config

# Optional lightweight web service / webhook trigger support
try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks
    from fastapi.responses import JSONResponse
    
    app = FastAPI(
        title="Client Growth Reports API",
        version="1.0.0",
        description="On-demand analytics report generation",
    )

    class ReportTriggerRequest(BaseModel):
        client_slug: str
        days: int = 28
        send_email: bool = False
        mock_data: bool = False

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "client-growth-reports"}

    @app.get("/clients")
    def get_clients():
        return {"clients": list_available_clients()}

    @app.post("/reports/generate")
    def trigger_report(req: ReportTriggerRequest):
        try:
            briefing = generate_report(
                client_slug=req.client_slug,
                days=req.days,
                send_email=req.send_email,
                mock_data=req.mock_data,
            )
            return {
                "status": "success",
                "client_id": briefing.client_id,
                "company_name": briefing.company_name,
                "generated_at": briefing.generated_at,
                "period": briefing.period_label,
                "executive_summary": briefing.insights.executive_summary,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

except ImportError:
    app = None
