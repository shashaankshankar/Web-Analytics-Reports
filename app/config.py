from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config" / "clients"

class BrandingConfig(BaseModel):
    primary_color: str = "#1E3A8A"
    secondary_color: str = "#3B82F6"
    accent_color: str = "#F59E0B"
    logo_url: str | None = None

    @field_validator("primary_color", "secondary_color", "accent_color")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", v):
            return "#1E3A8A"
        return v

class WeeklyDigestConfig(BaseModel):
    enabled: bool = True
    day: str = "monday"
    attach_pdf: bool = False

class PerformanceReportConfig(BaseModel):
    enabled: bool = True
    cadence: str = "28d"
    attach_pdf: bool = True
    deep_insights: bool = False

class ReportingSettingsConfig(BaseModel):
    weekly_digest: WeeklyDigestConfig = Field(default_factory=WeeklyDigestConfig)
    performance_report: PerformanceReportConfig = Field(default_factory=PerformanceReportConfig)

class ClientConfig(BaseModel):
    client_id: str = Field(..., description="Unique slug identifier for client")
    company_name: str = Field(..., description="Business or organization name")
    domain: str = Field(..., description="Canonical website URL")
    industry: str = Field(default="general", description="Industry / business context")
    ga4_property_id: str = Field(default="", description="Google Analytics 4 Property ID")
    gsc_site_url: str = Field(default="", description="Google Search Console Site URL")
    gbp_location_id: str = Field(default="", description="Google Business Profile Location ID")
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    recipients: dict[str, str] = Field(default_factory=dict, description="Recipient email map")
    timezone: str = Field(default="America/New_York", description="Local business timezone")
    monthly_retainer_focus: str = Field(default="", description="Agency goals & monthly priority focus")
    reporting: ReportingSettingsConfig = Field(default_factory=ReportingSettingsConfig)

    @field_validator("client_id")
    @classmethod
    def validate_client_id(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r"^[a-z0-9_-]+$", v):
            raise ValueError(f"Invalid client_id slug: {v}. Must contain only alphanumeric, dash, or underscore.")
        return v

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            v = f"https://{v}"
        return v

def load_client_config(client_slug_or_path: str | Path, config_dir: Path | None = None) -> ClientConfig:
    """Load a client configuration by slug from config/clients/<slug>.json or from a direct file path."""
    path = Path(client_slug_or_path)
    if not path.is_file():
        base_dir = config_dir or CONFIG_DIR
        slug = str(client_slug_or_path).replace(".json", "")
        path = base_dir / f"{slug}.json"

    if not path.is_file():
        raise FileNotFoundError(f"Client configuration not found: {client_slug_or_path} (resolved to {path})")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return ClientConfig(**data)

def load_client_config_by_slug(client_slug: str, config_dir: Path | None = None) -> ClientConfig:
    """Load only a configured client slug; never interpret caller input as a path."""
    if not re.match(r"^[a-z0-9_-]+$", client_slug.strip().lower()):
        raise ValueError("Client slug must contain only lowercase letters, numbers, dashes, or underscores.")
    slug = client_slug.strip().lower()
    base_dir = config_dir or CONFIG_DIR
    path = (base_dir / f"{slug}.json").resolve()
    if path.parent != base_dir.resolve():
        raise ValueError("Client configuration must be inside the configured client directory.")
    return load_client_config(path, config_dir=base_dir)

def list_available_clients(config_dir: Path | None = None) -> list[str]:
    """List all available client configuration slugs in the clients directory."""
    base_dir = config_dir or CONFIG_DIR
    if not base_dir.exists():
        return []
    return sorted([p.stem for p in base_dir.glob("*.json")])

class Settings(BaseModel):
    openrouter_api_key: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_base_url: str = Field(
        default_factory=lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    )
    resend_api_key: str = Field(default_factory=lambda: os.getenv("RESEND_API_KEY", os.getenv("REPORT_EMAIL_API_KEY", "")))
    resend_from_email: str = Field(default_factory=lambda: os.getenv("RESEND_FROM_EMAIL", os.getenv("REPORT_EMAIL_FROM", "reports@growthagency.com")))
    google_application_credentials: str = Field(default_factory=lambda: os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""))
    llm_model: str = Field(default_factory=lambda: os.getenv("LLM_MODEL", "openai/gpt-5.6-luna"))
    llm_reasoning_mode: str = Field(default_factory=lambda: os.getenv("LLM_REASONING_MODE", "standard"))
    llm_reasoning_effort: str = Field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT", "medium"))
    report_delivery_enabled: bool = Field(default_factory=lambda: os.getenv("REPORT_DELIVERY_ENABLED", "false").lower() == "true")
    report_allowed_clients: tuple[str, ...] = Field(
        default_factory=lambda: tuple(
            slug.strip().lower()
            for slug in os.getenv("REPORT_ALLOWED_CLIENTS", "").split(",")
            if slug.strip()
        )
    )

    @classmethod
    def from_env(cls) -> Settings:
        return cls()

def is_production_dispatch_allowed(client_slug: str) -> bool:
    """Require an explicit deployment allowlist before any real email dispatch."""
    settings = Settings.from_env()
    return settings.report_delivery_enabled and client_slug.strip().lower() in settings.report_allowed_clients
