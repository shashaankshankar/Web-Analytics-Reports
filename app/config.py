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
    mode: str; data_api_enabled: bool; live_approved: bool; property_id: str; stream_id: str; api_token: str; host: str; port: int
    @property
    def live_enabled(self): return self.mode == "live" and self.data_api_enabled and self.live_approved
    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        return cls(env.get("PLATFORM_MODE", "demo"), env.get("GA4_DATA_API_ENABLED") == "true", env.get("GA4_LIVE_APPROVED") == "true", env.get("GA4_PROPERTY_ID", ""), env.get("GA4_STREAM_ID", ""), env.get("PLATFORM_API_TOKEN", ""), env.get("HOST", "127.0.0.1"), int(env.get("PORT", "3000")))
    def validate(self, site: Site):
        if not self.live_enabled: return
        if len(self.api_token) < 32: raise RuntimeError("platform_api_token_required")
        if self.property_id != site.property_id: raise RuntimeError("live_ga4_property_mismatch")
        if self.stream_id != site.stream_id: raise RuntimeError("live_ga4_stream_mismatch")
