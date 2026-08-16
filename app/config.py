from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]

# The runtime always prefers an explicit site-config path. The single-file
# discovery fallback exists only to keep a checkout with one example site
# usable locally; it deliberately becomes an error as soon as the checkout
# contains more than one site config.
SITE_CONFIG_ENV = "MEASUREMENT_SITE_CONFIG"
DEFAULT_SITE_CONFIG_ENV = "MEASUREMENT_DEFAULT_SITE_CONFIG"
SITE_CONFIG_DIRECTORY = ROOT / "measurement" / "sites"

def load_dotenv(env: dict[str, str] | None = None) -> dict[str, str]:
    env = env if env is not None else os.environ
    path = ROOT / ".env"
    if not path.exists(): return env
    entries = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.replace("_", "").isalnum() and key[0].isalpha(): entries[key] = value.strip()
    for key, value in entries.items(): env.setdefault(key, value)
    return env

@dataclass(frozen=True)
class Site:
    site_id: str; company_id: str; company: str; canonical_domain: str; deployment_status: str
    business_timezone: str; property_id: str; stream_id: str; measurement_id: str
    property_timezone: str | None; collection_status: str; governance_status: str

def _resolve_site_config_path(env: Mapping[str, str]) -> Path:
    configured = env.get(SITE_CONFIG_ENV, "").strip()
    if not configured:
        configured = env.get(DEFAULT_SITE_CONFIG_ENV, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise RuntimeError(f"site_config_not_found:{configured}")
        return path

    candidates = sorted(SITE_CONFIG_DIRECTORY.glob("*.json"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError("site_config_required")
    raise RuntimeError("site_config_selection_required")


def load_site(env: Mapping[str, str] | None = None) -> Site:
    environment = os.environ if env is None else env
    value = json.loads(_resolve_site_config_path(environment).read_text()); ga4 = value["ga4"]
    return Site(value["siteId"], value["companyId"], value["company"], value["canonicalDomain"], value["deployment"]["status"], value["businessTimezone"], str(ga4["propertyId"]), str(ga4["webStreamId"]), ga4["measurementId"], ga4.get("propertyTimezone"), ga4["collectionStatus"], value["governance"]["status"])

@dataclass(frozen=True)
class Settings:
    mode: str
    data_api_enabled: bool
    live_approved: bool
    property_id: str
    stream_id: str
    api_token: str
    host: str
    port: int
    auth_mode: str = "token"
    database_url: str = ""
    db_name: str = "measurement"
    db_user: str = "measurement_app"
    db_password: str = ""
    db_socket: str = ""
    google_cloud_project: str = ""
    google_cloud_region: str = "us-central1"
    tasks_queue: str = ""
    service_url: str = ""
    task_service_account: str = ""
    internal_trigger_token: str = ""
    operator_email: str = ""
    report_email_api_key: str = ""
    report_email_from: str = ""
    report_recipients_json: str = "{}"
    report_email_endpoint: str = "https://api.resend.com/emails"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    google_oauth_state_secret: str = ""
    google_oauth_kms_key: str = ""
    google_oauth_enabled: bool = False
    google_oauth_production_approved: bool = False
    oauth_callback_only: bool = False
    trace_enabled: bool = False
    trace_sample_rate: float = 0.0
    # These settings belong to a separate browser-facing portal service. The
    # existing reporting service remains on its current token/IAM contract
    # unless this mode is explicitly enabled.
    portal_iap_mode: str | bool = "disabled"
    portal_iap_audience: str = ""
    portal_iap_expected_audience: str = ""

    @property
    def portal_iap_enabled(self) -> bool:
        if isinstance(self.portal_iap_mode, bool):
            return self.portal_iap_mode
        return str(self.portal_iap_mode).strip().casefold() in {"1", "true", "on", "yes", "direct", "iap", "enabled"}

    @property
    def portal_expected_audience(self) -> str:
        audience = self.portal_iap_audience.strip()
        expected = self.portal_iap_expected_audience.strip()
        if audience and expected and audience != expected:
            raise RuntimeError("portal_iap_audience_mismatch")
        return expected or audience

    @property
    def live_enabled(self): return self.mode == "live" and self.data_api_enabled and self.live_approved
    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        portal_iap_mode = env.get("PORTAL_IAP_MODE", env.get("PORTAL_IAP_ENABLED", "disabled"))
        portal_iap_mode = portal_iap_mode.strip().casefold()
        if portal_iap_mode in {"1", "true", "on", "yes"}:
            portal_iap_mode = "direct"
        elif portal_iap_mode in {"0", "false", "off", "no", ""}:
            portal_iap_mode = "disabled"
        audience = env.get("PORTAL_IAP_AUDIENCE", "")
        expected_audience = env.get("PORTAL_IAP_EXPECTED_AUDIENCE", "")
        return cls(
            env.get("PLATFORM_MODE", "demo"),
            env.get("GA4_DATA_API_ENABLED") == "true",
            env.get("GA4_LIVE_APPROVED") == "true",
            env.get("GA4_PROPERTY_ID", ""),
            env.get("GA4_STREAM_ID", ""),
            env.get("PLATFORM_API_TOKEN", ""),
            env.get("HOST", "127.0.0.1"),
            int(env.get("PORT", "3000")),
            env.get("PLATFORM_AUTH_MODE", "token"),
            env.get("DATABASE_URL", ""),
            env.get("DB_NAME", "measurement"),
            env.get("DB_USER", "measurement_app"),
            env.get("DB_PASSWORD", ""),
            env.get("INSTANCE_UNIX_SOCKET", ""),
            env.get("GOOGLE_CLOUD_PROJECT", ""),
            env.get("GOOGLE_CLOUD_REGION", "us-central1"),
            env.get("CLOUD_TASKS_QUEUE", ""),
            env.get("SERVICE_URL", ""),
            env.get("TASK_SERVICE_ACCOUNT", ""),
            env.get("INTERNAL_TRIGGER_TOKEN", ""),
            env.get("PLATFORM_OPERATOR_EMAIL", "").strip().lower(),
            env.get("REPORT_EMAIL_API_KEY", ""),
            env.get("REPORT_EMAIL_FROM", ""),
            env.get("REPORT_RECIPIENTS_JSON", "{}"),
            env.get("REPORT_EMAIL_ENDPOINT", "https://api.resend.com/emails"),
            env.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            env.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            env.get("GOOGLE_OAUTH_REDIRECT_URI", ""),
            env.get("GOOGLE_OAUTH_STATE_SECRET", ""),
            env.get("GOOGLE_OAUTH_KMS_KEY", ""),
            env.get("GOOGLE_OAUTH_ENABLED") == "true",
            env.get("GOOGLE_OAUTH_PRODUCTION_APPROVED") == "true",
            env.get("OAUTH_CALLBACK_ONLY") == "true",
            env.get("TRACE_ENABLED") == "true",
            float(env.get("TRACE_SAMPLE_RATE", "0")),
            portal_iap_mode,
            audience,
            expected_audience,
        )

    @property
    def database_enabled(self):
        return bool(self.database_url or (self.db_password and self.db_socket))

    @property
    def queue_enabled(self):
        return all((self.google_cloud_project, self.tasks_queue, self.service_url, self.task_service_account))
    @property
    def report_recipients(self) -> dict[str, str]:
        try:
            value = json.loads(self.report_recipients_json)
        except json.JSONDecodeError as error:
            raise RuntimeError("invalid_report_recipients_json") from error
        if not isinstance(value, dict) or not all(isinstance(key,str) and isinstance(item,str) for key,item in value.items()):
            raise RuntimeError("invalid_report_recipients_json")
        return value
    def validate(self, site: Site):
        if not 0 <= self.trace_sample_rate <= 1: raise RuntimeError("invalid_trace_sample_rate")
        if self.trace_enabled and not self.google_cloud_project: raise RuntimeError("trace_project_required")
        if self.google_oauth_production_approved and not self.google_oauth_enabled: raise RuntimeError("oauth_production_approval_requires_enablement")
        if not isinstance(self.portal_iap_mode, bool) and str(self.portal_iap_mode).strip().casefold() not in {"disabled", "off", "false", "0", "direct", "iap", "enabled", "true", "1", "on", "yes"}:
            raise RuntimeError("unsupported_portal_iap_mode")
        audience = self.portal_expected_audience
        if self.mode == "live" and self.portal_iap_enabled and not audience:
            raise RuntimeError("portal_iap_audience_required")
        if not self.live_enabled: return
        if self.auth_mode not in {"token", "cloud_run"}: raise RuntimeError("unsupported_auth_mode")
        if self.auth_mode == "token" and len(self.api_token) < 32: raise RuntimeError("platform_api_token_required")
        if not self.operator_email or "@" not in self.operator_email: raise RuntimeError("platform_operator_email_required")
        if self.property_id != site.property_id: raise RuntimeError("live_ga4_property_mismatch")
        if self.stream_id != site.stream_id: raise RuntimeError("live_ga4_stream_mismatch")
        if self.mode == "live" and not self.database_enabled: raise RuntimeError("production_database_required")
        if self.queue_enabled and len(self.internal_trigger_token) < 32: raise RuntimeError("internal_trigger_token_required")
