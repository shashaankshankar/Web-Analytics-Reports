from __future__ import annotations

from typing import Protocol, runtime_checkable

import google.auth
from google.oauth2.credentials import Credentials
from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.protobuf.json_format import MessageToDict

ANALYTICS_READONLY = "https://www.googleapis.com/auth/analytics.readonly"


def message_dict(value) -> dict:
    raw = getattr(value, "_pb", value)
    return MessageToDict(raw, preserving_proto_field_name=True)


@runtime_checkable
class AnalyticsCredential(Protocol):
    def get_authorized_client(self) -> BetaAnalyticsDataClient: ...
    def get_admin_client(self) -> AnalyticsAdminServiceClient: ...
    def validate_access(self, property_id: str) -> bool: ...
    def list_accessible_properties(self) -> list[dict]: ...
    def disable(self) -> None: ...


class AdcCredential:
    """Read-only ADC provider. It owns authentication, not website assignment."""

    def __init__(self, credentials=None, project_id: str | None = None):
        if credentials is None:
            credentials, discovered_project = google.auth.default(scopes=[ANALYTICS_READONLY])
            project_id = project_id or discovered_project
        self.credentials = credentials
        self.project_id = project_id
        self._disabled = False
        self._data_client = None
        self._admin_client = None

    def _require_enabled(self):
        if self._disabled:
            raise RuntimeError("analytics_connection_disabled")

    def get_authorized_client(self) -> BetaAnalyticsDataClient:
        self._require_enabled()
        if self._data_client is None:
            self._data_client = BetaAnalyticsDataClient(credentials=self.credentials)
        return self._data_client

    def get_admin_client(self) -> AnalyticsAdminServiceClient:
        self._require_enabled()
        if self._admin_client is None:
            self._admin_client = AnalyticsAdminServiceClient(credentials=self.credentials)
        return self._admin_client

    def validate_access(self, property_id: str) -> bool:
        self.get_admin_client().get_property(name=f"properties/{property_id}")
        return True

    def list_accessible_properties(self) -> list[dict]:
        client = self.get_admin_client()
        properties = []
        for summary in client.list_account_summaries():
            for item in summary.property_summaries:
                properties.append({
                    "account": summary.account,
                    "accountDisplayName": summary.display_name,
                    "property": item.property,
                    "propertyDisplayName": item.display_name,
                    "propertyType": item.property_type.name,
                    "parent": item.parent,
                })
        return properties

    def disable(self) -> None:
        self._disabled = True
        self._data_client = None
        self._admin_client = None


class OAuthCredential:
    """Read-only OAuth provider for an already authorized, KMS-decrypted refresh token."""

    def __init__(self, refresh_token: str, client_id: str, client_secret: str,
                 token_uri: str = "https://oauth2.googleapis.com/token", credentials=None):
        if not refresh_token or not client_id or not client_secret:
            raise RuntimeError("oauth_credential_configuration_required")
        self.credentials=credentials or Credentials(
            token=None,refresh_token=refresh_token,token_uri=token_uri,
            client_id=client_id,client_secret=client_secret,scopes=[ANALYTICS_READONLY],
        )
        if set(self.credentials.scopes or []) != {ANALYTICS_READONLY}:
            raise RuntimeError("oauth_credential_scope_violation")
        self._disabled=False; self._data_client=None; self._admin_client=None

    def _require_enabled(self):
        if self._disabled: raise RuntimeError("analytics_connection_disabled")

    def get_authorized_client(self) -> BetaAnalyticsDataClient:
        self._require_enabled()
        if self._data_client is None: self._data_client=BetaAnalyticsDataClient(credentials=self.credentials)
        return self._data_client

    def get_admin_client(self) -> AnalyticsAdminServiceClient:
        self._require_enabled()
        if self._admin_client is None: self._admin_client=AnalyticsAdminServiceClient(credentials=self.credentials)
        return self._admin_client

    def validate_access(self, property_id: str) -> bool:
        self.get_admin_client().get_property(name=f"properties/{property_id}")
        return True

    def list_accessible_properties(self) -> list[dict]:
        properties=[]
        for summary in self.get_admin_client().list_account_summaries():
            for item in summary.property_summaries:
                properties.append({"account":summary.account,"accountDisplayName":summary.display_name,
                                   "property":item.property,"propertyDisplayName":item.display_name,
                                   "propertyType":item.property_type.name,"parent":item.parent})
        return properties

    def disable(self) -> None:
        self._disabled=True; self._data_client=None; self._admin_client=None; self.credentials=None


class GA4Admin:
    def __init__(self, credential: AnalyticsCredential):
        self.credential = credential

    def inspect(self, property_id: str) -> dict:
        client = self.credential.get_admin_client()
        parent = f"properties/{property_id}"
        prop = client.get_property(name=parent)
        streams = [message_dict(value) for value in client.list_data_streams(parent=parent)]
        key_events = [message_dict(value) for value in client.list_key_events(parent=parent)]
        custom_dimensions = [message_dict(value) for value in client.list_custom_dimensions(parent=parent)]
        custom_metrics = [message_dict(value) for value in client.list_custom_metrics(parent=parent)]
        return {
            "property": message_dict(prop),
            "streams": streams,
            "keyEvents": key_events,
            "customDimensions": custom_dimensions,
            "customMetrics": custom_metrics,
        }
    def configuration_health(self, site) -> dict:
        configuration = self.inspect(site.property_id)
        expected_stream_name = f"properties/{site.property_id}/dataStreams/{site.stream_id}"
        stream = next((item for item in configuration["streams"] if item.get("name") == expected_stream_name), None)
        property_timezone = configuration["property"].get("time_zone")
        checks = [
            {"key":"property_access","state":"ok","detail":f"properties/{site.property_id}"},
            {"key":"stream_exists","state":"ok" if stream else "error","detail":expected_stream_name},
            {"key":"measurement_id","state":"ok" if stream and stream.get("web_stream_data",{}).get("measurement_id") == site.measurement_id else "error","detail":stream.get("web_stream_data",{}).get("measurement_id") if stream else None},
            {"key":"timezone_alignment","state":"ok" if property_timezone == site.business_timezone else "warning","detail":f"GA4={property_timezone}; business={site.business_timezone}"},
        ]
        return {
            "status":"ok" if all(item["state"] == "ok" for item in checks) else "warning",
            "checks":checks,
            "keyEvents":[item.get("event_name") for item in configuration["keyEvents"]],
            "customDimensionCount":len(configuration["customDimensions"]),
            "customMetricCount":len(configuration["customMetrics"]),
        }
