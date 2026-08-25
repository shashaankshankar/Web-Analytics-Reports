from __future__ import annotations

from fastapi import FastAPI

from app.gbp_oauth import create_oauth_router

app = FastAPI(
    title="Google Business Profile OAuth Callback",
    version="1.0.0",
    description="Callback-only service for the private GBP reporting connection.",
)
app.include_router(create_oauth_router())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "measurement-oauth-callback"}
