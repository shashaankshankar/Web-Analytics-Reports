"""Optional client-owned website inquiry aggregate source.

This adapter intentionally has no fallback credential.  Website credentials
are resolved only from the client-scoped reference supplied by the caller; the
internal Resend credential is not imported or consulted here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from app.analytics.contracts import SourceAvailability, WebsiteInquiryMetrics
from app.config import ClientConfig, WebsiteInquiryMetricsConfig
from app.sources.resend_email_metrics import (
    MODE_WEBSITE_INQUIRY_DELIVERY,
    ResendEmailMetricsSource,
    default_resend_metrics_requester,
)


@dataclass(frozen=True)
class WebsiteMetricsCredential:
    """Validated, client-bound website credential passed to an aggregate reader."""

    client_id: str
    token: str


def _validate_date(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO date or datetime.")
    raw = value.strip()
    try:
        if "T" in raw or " " in raw:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
        else:
            datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date or datetime.") from exc
    return raw


def _date_part(value: str) -> date:
    if "T" in value or " " in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    return date.fromisoformat(value)


def _validated_timezone(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timezone must be a valid IANA timezone.")
    raw = value.strip()
    try:
        ZoneInfo(raw)
    except Exception as exc:
        raise ValueError("timezone must be a valid IANA timezone.") from exc
    return raw


def _safe_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or isinstance(value, float) and value != number:
        return None
    return number


class WebsiteInquiryMetricsSource:
    """Read client-scoped website inquiry aggregates when explicitly wired.

    ``aggregate_reader`` is deliberately injected because this repository does
    not own a website provider contract in this pass.  It must accept keyword
    arguments ``client_id``, ``start_date``, ``end_date``, ``timezone``, and
    ``credential`` and return a mapping of aggregate fields.
    """

    source_name = "website_inquiries"

    def __init__(
        self,
        client: ClientConfig | None = None,
        *,
        config: WebsiteInquiryMetricsConfig | None = None,
        credential_resolver: Callable[[str], Any] | None = None,
        aggregate_reader: Callable[..., Mapping[str, Any]] | Any | None = None,
        reader: Callable[..., Mapping[str, Any]] | Any | None = None,
        aggregate_fetcher: Callable[..., Mapping[str, Any]] | Any | None = None,
        requester: Callable[[str, str], Mapping[str, Any]] | None = None,
    ):
        if client is None and config is None:
            raise ValueError("client or website inquiry metrics config is required.")
        if client is not None and config is not None and config != client.website_inquiry_metrics:
            raise ValueError("client and website inquiry metrics config do not match.")
        self.client = client
        self.config = config or client.website_inquiry_metrics
        self.credential_resolver = credential_resolver
        self.requester = requester
        readers = [item for item in (aggregate_reader, reader, aggregate_fetcher) if item is not None]
        if len(readers) > 1:
            raise ValueError("Provide only one website aggregate reader.")
        self.aggregate_reader = readers[0] if readers else None

    def is_configured(self) -> bool:
        if not self.config.enabled:
            return False
        if not self.config.aggregate_source.strip():
            return False
        if self.aggregate_reader is not None:
            return True
        if self.config.provider == "secret_manager" and self.config.secret_manager_ref.strip():
            return True
        return False

    def _base_result(self, status: SourceAvailability, reason: str | None = None) -> dict[str, Any]:
        model = WebsiteInquiryMetrics(
            status=status,
            source=self.config.aggregate_source or self.source_name,
            credential_reference_configured=bool(self.config.secret_manager_ref.strip()),
            reason=reason,
        )
        return model.model_dump(mode="json")

    @staticmethod
    def _safe_provider_reason(status: SourceAvailability) -> str | None:
        """Keep provider-supplied diagnostics out of the client contract."""
        reasons = {
            SourceAvailability.EMPTY: "Website inquiry aggregate returned no data for the requested window.",
            SourceAvailability.PARTIAL: "Website inquiry aggregate is only partially available for the requested window.",
            SourceAvailability.NOT_CONFIGURED: "Website inquiry aggregate is not configured.",
            SourceAvailability.UNAVAILABLE: "Website inquiry aggregate is unavailable for the requested window.",
            SourceAvailability.ERROR: "Website inquiry aggregate returned an unusable response.",
        }
        return reasons.get(status)

    @staticmethod
    def _credential_bundle(value: Any, expected_client_id: str) -> WebsiteMetricsCredential | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError from exc
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError) as exc:
                raise ValueError from exc
        if not isinstance(value, Mapping):
            raise ValueError
        bundle_client_id = str(value.get("client_id", "")).strip().lower()
        token = value.get("token", value.get("api_key"))
        if bundle_client_id != expected_client_id or not isinstance(token, str) or not token.strip():
            raise ValueError
        if value.get("authorized", True) is not True:
            raise PermissionError
        return WebsiteMetricsCredential(client_id=bundle_client_id, token=token.strip())

    def _resolve_credential(self, client_id: str) -> tuple[WebsiteMetricsCredential | None, str | None, SourceAvailability]:
        if self.config.provider != "secret_manager":
            return None, None, SourceAvailability.AVAILABLE
        reference = self.config.secret_manager_ref.strip()
        resolver = self.credential_resolver
        if resolver is None:
            secret_id = self.config.website_resend_metrics_secret_id.strip()
            env_candidates = [
                secret_id,
                secret_id.upper().replace("-", "_") if secret_id else "",
                "WEBSITE_RESEND_API_KEY",
                f"{client_id.upper().replace('-', '_')}_WEBSITE_RESEND_API_KEY",
            ]
            env_token = ""
            for env_name in env_candidates:
                if env_name and os.getenv(env_name):
                    env_token = os.getenv(env_name, "").strip()
                    break
            if env_token:
                resolver = lambda ref: {"client_id": client_id, "token": env_token}

        if not reference or resolver is None:
            return None, "Website metrics credential reference is not resolvable.", SourceAvailability.NOT_CONFIGURED
        try:
            raw = resolver(reference)
            credential = self._credential_bundle(raw, client_id)
        except PermissionError:
            return None, "Website metrics credential is not authorized for this client.", SourceAvailability.UNAVAILABLE
        except Exception:
            return None, "Website metrics credential is missing or malformed.", SourceAvailability.UNAVAILABLE
        if credential is None:
            return None, "Website metrics credential is missing.", SourceAvailability.NOT_CONFIGURED
        return credential, None, SourceAvailability.AVAILABLE

    def _read_aggregate(
        self,
        *,
        client_id: str,
        start_date: str,
        end_date: str,
        timezone_name: str,
        credential: WebsiteMetricsCredential | None,
    ) -> Mapping[str, Any]:
        reader = self.aggregate_reader
        if reader is not None:
            method = getattr(reader, "fetch_metrics", None)
            if callable(method):
                response = method(
                    client_id=client_id,
                    start_date=start_date,
                    end_date=end_date,
                    timezone=timezone_name,
                    credential=credential,
                )
            else:
                response = reader(
                    client_id=client_id,
                    start_date=start_date,
                    end_date=end_date,
                    timezone=timezone_name,
                    credential=credential,
                )
            if not isinstance(response, Mapping):
                raise ValueError
            return response

        token = credential.token if credential else ""
        if not token:
            raise LookupError("No website Resend API token available.")

        resend_source = ResendEmailMetricsSource(
            api_key=token,
            mode=MODE_WEBSITE_INQUIRY_DELIVERY,
            expected_sending_domain=self.config.expected_website_sending_domain,
            requester=self.requester or default_resend_metrics_requester,
        )
        return resend_source.fetch_metrics(
            client_id=client_id,
            start_date=start_date,
            end_date=end_date,
            timezone=timezone_name,
        )

    def fetch_metrics(
        self,
        client_id: str,
        start_date: str,
        end_date: str,
        timezone: str,
    ) -> dict[str, Any]:
        """Fetch only a validated client/window aggregate; never use Resend auth."""
        selected_client = str(client_id).strip().lower()
        try:
            start = _validate_date(start_date, "start_date")
            end = _validate_date(end_date, "end_date")
            if _date_part(start) > _date_part(end):
                raise ValueError
            timezone_name = _validated_timezone(timezone)
        except ValueError:
            return self._base_result(SourceAvailability.ERROR, "The website inquiry metrics selection is invalid.")

        if not self.config.enabled:
            return self._base_result(SourceAvailability.NOT_CONFIGURED, "Website inquiry metrics are not enabled for this client.")
        if self.client is not None and self.client.client_id != selected_client:
            return self._base_result(SourceAvailability.UNAVAILABLE, "Website inquiry metrics are scoped to another client.")
        if self.config.expected_client_id and self.config.expected_client_id != selected_client:
            return self._base_result(SourceAvailability.UNAVAILABLE, "Website metrics credential is scoped to another client.")
        if not self.config.aggregate_source.strip() or (self.aggregate_reader is None and not self.is_configured()):
            return self._base_result(SourceAvailability.NOT_CONFIGURED, "No website inquiry aggregate source is configured.")
        if self.config.provider == "secret_manager" and not self.config.secret_manager_ref.strip():
            return self._base_result(SourceAvailability.NOT_CONFIGURED, "Website metrics credential reference is not configured.")

        credential, credential_reason, credential_status = self._resolve_credential(selected_client)
        if credential_reason:
            return self._base_result(credential_status, credential_reason)

        try:
            response = self._read_aggregate(
                client_id=selected_client,
                start_date=start,
                end_date=end,
                timezone_name=timezone_name,
                credential=credential,
            )
        except PermissionError:
            return self._base_result(SourceAvailability.UNAVAILABLE, "Website inquiry aggregate access was not authorized.")
        except LookupError:
            return self._base_result(SourceAvailability.NOT_CONFIGURED, "No website inquiry aggregate source is configured.")
        except Exception:
            return self._base_result(SourceAvailability.UNAVAILABLE, "Website inquiry aggregate data is unavailable.")

        response_client = response.get("client_id")
        if response_client is not None and str(response_client).strip().lower() != selected_client:
            return self._base_result(SourceAvailability.UNAVAILABLE, "Website inquiry aggregate was returned for another client.")
        for response_field, expected in (("start_date", start), ("end_date", end), ("timezone", timezone_name)):
            if response.get(response_field) is not None and str(response[response_field]).strip() != expected:
                return self._base_result(SourceAvailability.UNAVAILABLE, "Website inquiry aggregate window did not match the requested window.")

        delivery_metrics = response.get("delivery_metrics") or response.get("metrics") or {}
        if not isinstance(delivery_metrics, dict):
            delivery_metrics = {}

        current = _safe_count(response.get("current_inquiries"))
        if current is None and delivery_metrics:
            current = _safe_count(delivery_metrics.get("delivered", delivery_metrics.get("sent")))

        prior = _safe_count(response.get("prior_inquiries"))
        event_values = response.get("inquiry_events", {}) or {}
        prior_event_values = response.get("prior_inquiry_events", {}) or {}
        if not isinstance(event_values, Mapping) or not isinstance(prior_event_values, Mapping):
            return self._base_result(SourceAvailability.UNAVAILABLE, "Website inquiry aggregate data was malformed.")
        normalized_events = {str(name): _safe_count(value) for name, value in event_values.items()}
        normalized_prior_events = {str(name): _safe_count(value) for name, value in prior_event_values.items()}

        raw_status = response.get("status")
        if raw_status is None:
            status = SourceAvailability.AVAILABLE if any(value is not None for value in (current, prior)) or normalized_events or delivery_metrics else SourceAvailability.EMPTY
        else:
            try:
                status = SourceAvailability(str(getattr(raw_status, "value", raw_status)))
            except ValueError:
                return self._base_result(SourceAvailability.UNAVAILABLE, "Website inquiry aggregate returned an unknown status.")
        if status == SourceAvailability.AVAILABLE and current is None and prior is None and not normalized_events and not delivery_metrics:
            status = SourceAvailability.PARTIAL

        model = WebsiteInquiryMetrics(
            status=status,
            source=self.config.aggregate_source or self.source_name,
            current_inquiries=current,
            prior_inquiries=prior,
            inquiry_events=normalized_events,
            prior_inquiry_events=normalized_prior_events,
            delivery_metrics=delivery_metrics,
            credential_reference_configured=bool(self.config.secret_manager_ref.strip()),
            reason=self._safe_provider_reason(status),
        )
        return model.model_dump(mode="json")


WebsiteInquiryMetricsExtractor = WebsiteInquiryMetricsSource
