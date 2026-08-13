from __future__ import annotations

import base64
import json
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

from .external_sources import (
    FirstPartyOutcomeConnector,
    GoogleAdsConnector,
    SearchConsoleConnector,
)


ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
PINNED_SECRET = re.compile(
    r"^projects/[A-Za-z0-9._:-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$"
)


class SecretJsonResolver:
    """Read a version-pinned JSON secret without exposing its payload to logs."""

    def __init__(self, requester: Callable | None = None):
        self.requester = requester or self._request

    @staticmethod
    def _request(reference: str) -> dict:
        credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        credentials.refresh(GoogleAuthRequest())
        request = urllib.request.Request(
            f"https://secretmanager.googleapis.com/v1/{reference}:access",
            headers={"Authorization": f"Bearer {credentials.token}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def resolve(self, reference: str) -> dict:
        if not PINNED_SECRET.fullmatch(reference):
            raise ValueError("version_pinned_secret_reference_required")
        response = self.requester(reference)
        encoded = response.get("payload", {}).get("data", "")
        if not encoded:
            raise RuntimeError("source_secret_payload_missing")
        try:
            value = json.loads(base64.b64decode(encoded, validate=True))
        except (ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("source_secret_payload_invalid") from error
        if not isinstance(value, dict):
            raise RuntimeError("source_secret_payload_invalid")
        return value


def oauth_access_token(secret: dict, scope: str) -> str:
    required = ("refreshToken", "clientId", "clientSecret")
    if any(not isinstance(secret.get(key), str) or not secret[key] for key in required):
        raise RuntimeError("oauth_refresh_configuration_required")
    credentials = Credentials(
        token=None,
        refresh_token=secret["refreshToken"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=secret["clientId"],
        client_secret=secret["clientSecret"],
        scopes=[scope],
    )
    credentials.refresh(GoogleAuthRequest())
    if not credentials.token:
        raise RuntimeError("oauth_access_token_unavailable")
    return credentials.token


def adc_access_token(scope: str) -> str:
    credentials, _ = google.auth.default(scopes=[scope])
    credentials.refresh(GoogleAuthRequest())
    if not credentials.token:
        raise RuntimeError("adc_access_token_unavailable")
    return credentials.token


class SourceConnectorFactory:
    def __init__(
        self,
        resolver: SecretJsonResolver | None = None,
        oauth_provider: Callable[[dict, str], str] = oauth_access_token,
        adc_provider: Callable[[str], str] = adc_access_token,
    ):
        self.resolver = resolver or SecretJsonResolver()
        self.oauth_provider = oauth_provider
        self.adc_provider = adc_provider

    def create(self, target: dict):
        source_type = target["source_type"]
        configuration = target.get("configuration_json") or {}
        secret = self.resolver.resolve(target["credential_secret_reference"])
        if source_type == "google_ads":
            developer_token = secret.get("developerToken")
            customer_id = target.get("external_account_id")
            if not isinstance(developer_token, str) or not developer_token or not customer_id:
                raise RuntimeError("google_ads_configuration_required")
            return GoogleAdsConnector(
                self.oauth_provider(secret, ADS_SCOPE),
                developer_token,
                customer_id,
                configuration.get("loginCustomerId", ""),
            )
        if source_type == "search_console":
            site_url = configuration.get("siteUrl")
            if not isinstance(site_url, str) or not site_url:
                raise RuntimeError("search_console_site_required")
            credential_mode = secret.get("credentialMode", "oauth")
            if credential_mode == "adc":
                access_token = self.adc_provider(SEARCH_CONSOLE_SCOPE)
            elif credential_mode == "oauth":
                access_token = self.oauth_provider(secret, SEARCH_CONSOLE_SCOPE)
            else:
                raise RuntimeError("unsupported_source_credential_mode")
            return SearchConsoleConnector(
                access_token,
                site_url,
                configuration.get("privacyApprovedQueries") is True,
            )
        if source_type in {"call_tracking", "crm_booking"}:
            hash_secret = secret.get("outcomeHashSecret")
            policy = configuration.get("identityPolicyReference")
            if not isinstance(hash_secret, str) or not isinstance(policy, str):
                raise RuntimeError("first_party_source_configuration_required")
            return FirstPartyOutcomeConnector(source_type, hash_secret, policy)
        raise ValueError("unsupported_external_source")


class BufferedOutcomeConnector:
    """Expose an approved push batch through the normal provenance engine."""

    def __init__(self, connector: FirstPartyOutcomeConnector, records: list[dict]):
        self.connector = connector
        self.source_type = connector.source_type
        self.rows = [connector.normalize(record) for record in records]

    def validate_access(self) -> dict:
        return self.connector.validate_access()

    def sync(self, start_date: date, end_date: date) -> list[dict]:
        if any(not start_date <= date.fromisoformat(row["outcomeDate"]) <= end_date for row in self.rows):
            raise ValueError("outcome_outside_batch_period")
        return self.rows

    def disable(self) -> None:
        self.connector.disable()


def external_sync_window(scheduled_for: str | None, target: dict) -> tuple[date, date]:
    configuration = target.get("configuration_json") or {}
    timezone_name = configuration.get("timezone", "America/New_York")
    zone = ZoneInfo(timezone_name)
    instant = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00")) if scheduled_for else datetime.now(timezone.utc)
    local_day = instant.astimezone(zone).date()
    lag = 3 if target["source_type"] == "search_console" else 1
    lag = int(configuration.get("finalizationLagDays", lag))
    lookback = int(configuration.get("lookbackDays", 14))
    if not 1 <= lag <= 14 or not 1 <= lookback <= 90:
        raise ValueError("invalid_external_sync_window")
    end_date = local_day - timedelta(days=lag)
    return end_date - timedelta(days=lookback - 1), end_date
