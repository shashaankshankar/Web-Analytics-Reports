from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]

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

def load_site() -> Site:
    value = json.loads((ROOT / "measurement/sites/house-of-dental.json").read_text()); ga4 = value["ga4"]
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
    @property
    def live_enabled(self): return self.mode == "live" and self.data_api_enabled and self.live_approved
    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
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
        if self.google_oauth_production_approved and not self.google_oauth_enabled: raise RuntimeError("oauth_production_approval_requires_enablement")
        if not self.live_enabled: return
        if self.auth_mode not in {"token", "cloud_run"}: raise RuntimeError("unsupported_auth_mode")
        if self.auth_mode == "token" and len(self.api_token) < 32: raise RuntimeError("platform_api_token_required")
        if not self.operator_email or "@" not in self.operator_email: raise RuntimeError("platform_operator_email_required")
        if self.property_id != site.property_id: raise RuntimeError("live_ga4_property_mismatch")
        if self.stream_id != site.stream_id: raise RuntimeError("live_ga4_stream_mismatch")
        if self.mode == "live" and not self.database_enabled: raise RuntimeError("production_database_required")
        if self.queue_enabled and len(self.internal_trigger_token) < 32: raise RuntimeError("internal_trigger_token_required")
