from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, Mapping, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class WebsiteInquiryMetricsConfig(BaseModel):
    """Optional, client-scoped website inquiry source configuration.

    This model intentionally stores only a reference to externally managed
    secret material. It never accepts an API key, token, recipient, or webhook
    payload in the client JSON file.
    """

    enabled: bool = False
    provider: Literal["website_aggregates", "secret_manager"] = "website_aggregates"
    secret_manager_ref: str = ""
    website_resend_metrics_secret_id: str = ""
    expected_client_id: str = ""
    expected_website_sending_domain: str = ""
    aggregate_source: str = "website_delivery_aggregate"
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def sync_secret_refs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            ref = data.get("secret_manager_ref", "")
            alias = data.get("website_resend_metrics_secret_id", "")
            if ref and not alias:
                data["website_resend_metrics_secret_id"] = ref
            elif alias and not ref:
                data["secret_manager_ref"] = alias
        return data

    @field_validator("secret_manager_ref", "website_resend_metrics_secret_id")
    @classmethod
    def validate_secret_manager_ref(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(r"^(?:projects/[a-z0-9][a-z0-9-]*/)?secrets/[a-zA-Z0-9][a-zA-Z0-9._-]*(?:/versions/[a-zA-Z0-9._-]+)?$", value):
            raise ValueError("secret_manager_ref must be a Secret Manager resource reference, not secret material.")
        return value

    @field_validator("expected_client_id")
    @classmethod
    def validate_expected_client_id(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not re.match(r"^[a-z0-9_-]+$", value):
            raise ValueError("expected_client_id must be a client slug.")
        return value

    @field_validator("expected_website_sending_domain")
    @classmethod
    def validate_expected_website_sending_domain(cls, value: str) -> str:
        value = value.strip().lower()
        if value.startswith("https://"):
            value = value[8:]
        elif value.startswith("http://"):
            value = value[7:]
        return value.rstrip("/")

class ClientConfig(BaseModel):
    client_id: str = Field(..., description="Unique slug identifier for client")
    company_name: str = Field(..., description="Business or organization name")
    domain: str = Field(..., description="Canonical website URL")
    industry: str = Field(default="general", description="Industry / business context")
    ga4_property_id: str = Field(default="", description="Google Analytics 4 Property ID")
    gsc_site_url: str = Field(default="", description="Google Search Console Site URL")
    gbp_location_id: str = Field(default="", description="Candidate private Google Business Profile location resource")
    gbp_candidate_location_ids: list[str] = Field(
        default_factory=list,
        description="Candidate private locations/<id> resources; OAuth access still verifies the match.",
    )
    gbp_public_place_id: str = Field(
        default="",
        description="Public Google Places ID kept separate from private locations/<id> resources.",
    )
    gbp_account_id: str = Field(default="", description="Optional Google Business Profile account ID for managed reviews")
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    recipients: dict[str, str] = Field(default_factory=dict, description="Recipient email map")
    timezone: str = Field(default="America/New_York", description="Local business timezone")
    site_launch_date: date | None = Field(
        default=None,
        description="Optional client-confirmed public website launch date; context only, not measurement eligibility.",
    )
    measurement_start_date: date | None = Field(
        default=None,
        description="First date the analytics property provides trustworthy reporting data.",
    )
    goals: list[str] = Field(default_factory=list, description="Agency goals and growth priorities")
    reporting: ReportingSettingsConfig = Field(default_factory=ReportingSettingsConfig)
    website_inquiry_metrics: WebsiteInquiryMetricsConfig = Field(default_factory=WebsiteInquiryMetricsConfig)

    @model_validator(mode="before")
    @classmethod
    def reject_removed_monthly_focus(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and "monthly_retainer_focus" in data:
            raise ValueError("monthly_retainer_focus is no longer supported; use goals instead")
        return data

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

    @field_validator("site_launch_date", "measurement_start_date", mode="before")
    @classmethod
    def validate_iso_date(cls, v: Any) -> Any:
        if v is None or isinstance(v, date):
            return v
        if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
            raise ValueError("Date fields must use ISO date format YYYY-MM-DD.")
        try:
            return date.fromisoformat(v.strip())
        except ValueError as exc:
            raise ValueError("Date fields must contain a valid calendar date in YYYY-MM-DD format.") from exc

    @model_validator(mode="after")
    def validate_scoped_source_references(self) -> "ClientConfig":
        candidates = list(self.gbp_candidate_location_ids)
        if self.gbp_location_id and self.gbp_location_id.startswith("locations/") and self.gbp_location_id not in candidates:
            candidates.insert(0, self.gbp_location_id)
        for value in candidates:
            if not re.fullmatch(r"locations/[A-Za-z0-9_-]+", value.strip()):
                raise ValueError("gbp_candidate_location_ids must contain locations/<id> resources.")
        self.gbp_candidate_location_ids = candidates
        if self.website_inquiry_metrics.expected_client_id and self.website_inquiry_metrics.expected_client_id != self.client_id:
            raise ValueError("website_inquiry_metrics.expected_client_id must match client_id.")
        return self

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
    # These are the Vector Studios report-delivery credentials. Website inquiry
    # credentials are client-scoped references and are never read here.
    resend_api_key: str = Field(default_factory=lambda: os.getenv("RESEND_API_KEY", ""))
    resend_from_email: str = Field(default_factory=lambda: os.getenv("RESEND_FROM_EMAIL", "reports@growthagency.com"))
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
    report_delivery_store_path: str = Field(
        default_factory=lambda: os.getenv("REPORT_DELIVERY_STORE_PATH", "")
    )
    report_delivery_cache_ttl_seconds: int = Field(
        default_factory=lambda: int(os.getenv("REPORT_DELIVERY_CACHE_TTL_SECONDS", "900")),
        ge=0,
    )
    report_delivery_retention_days: int = Field(
        default_factory=lambda: int(os.getenv("REPORT_DELIVERY_RETENTION_DAYS", "90")),
        ge=0,
    )

    @classmethod
    def from_env(cls) -> Settings:
        return cls()

def is_production_dispatch_allowed(client_slug: str) -> bool:
    """Require an explicit deployment allowlist before any real email dispatch."""
    settings = Settings.from_env()
    return settings.report_delivery_enabled and client_slug.strip().lower() in settings.report_allowed_clients
