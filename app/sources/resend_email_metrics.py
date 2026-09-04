"""Read-only Resend Email Metrics API adapter for internal report delivery."""

from __future__ import annotations

import copy
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from app.analytics.contracts import ReportDeliveryMetrics, SourceAvailability, WebsiteInquiryMetrics
from app.config import Settings
from app.delivery.report_store import SentReportStore


RESEND_API_KEY_ENV = "RESEND_API_KEY"
RESEND_EMAIL_METRICS_ENDPOINT = "https://api.resend.com/emails/metrics"
MAX_EMAIL_IDS_PER_REQUEST = 100

MODE_INTERNAL_REPORT_DELIVERY = "internal_report_delivery"
MODE_WEBSITE_INQUIRY_DELIVERY = "website_inquiry_delivery"
VALID_MODES = (MODE_INTERNAL_REPORT_DELIVERY, MODE_WEBSITE_INQUIRY_DELIVERY)

# Request count metrics only. Rates cannot be added across 100-ID batches, so
# this source derives rates only when the required aggregate denominators are
# known.
RESEND_COUNT_METRICS = (
    "received",
    "delivered",
    "complained",
    "suppressed",
    "bounced",
    "bounced_transient",
    "bounced_permanent",
    "bounced_undetermined",
    "opened",
    "clicked",
    "unsubscribed",
    "delivery_delayed",
    "failed",
    "sent",
    "unique_opened",
    "unique_clicked",
)


class ResendMetricsRequestError(RuntimeError):
    """Provider request failure with only a safe HTTP status classification."""

    def __init__(self, status_code: int | None = None):
        super().__init__("Resend Email Metrics request failed")
        self.status_code = status_code


def default_resend_metrics_requester(url: str, api_key: str) -> Mapping[str, Any]:
    """Fetch one metrics batch without retaining or exposing the response body."""
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "ClientGrowthReports/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise ResendMetricsRequestError(exc.code) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ResendMetricsRequestError() from exc


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().lower()


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


def _validate_timezone(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timezone must be a valid IANA timezone.")
    raw = value.strip()
    try:
        ZoneInfo(raw)
    except Exception as exc:
        raise ValueError("timezone must be a valid IANA timezone.") from exc
    return raw


def _numeric_metric(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or value < 0:
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _derive_rate(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round((float(numerator) / float(denominator)) * 100.0, 2)


class ResendEmailMetricsSource:
    """Query aggregate metrics for internal report delivery or client website inquiries.

    In mode ``internal_report_delivery`` (the default), the credential is the
    internal ``RESEND_API_KEY`` and queries are scoped exclusively to report IDs
    recorded in ``SentReportStore``.

    In mode ``website_inquiry_delivery``, the credential must be the client's
    configured website Resend API key (never falling back to ``RESEND_API_KEY``).
    If email IDs are provided, it queries them in batches of <= 100. If not
    provided, it executes an aggregate query for the client's reporting window
    with America/New_York timezone and validates the sending domain.
    """

    source_name = "resend_email_metrics"

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        mode: str = MODE_INTERNAL_REPORT_DELIVERY,
        expected_sending_domain: Optional[str] = None,
        store: SentReportStore | None = None,
        store_path: str | os.PathLike[str] | None = None,
        endpoint: str = RESEND_EMAIL_METRICS_ENDPOINT,
        requester: Callable[[str, str], Mapping[str, Any]] = default_resend_metrics_requester,
        cache_ttl_seconds: int = 900,
        retention_days: int | None = 90,
        clock: Callable[[], datetime] | None = None,
    ):
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}.")
        if store is not None and store_path is not None:
            raise ValueError("Provide store or store_path, not both.")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be non-negative.")
        if retention_days is not None and retention_days < 0:
            raise ValueError("retention_days must be non-negative.")

        self.mode = mode
        self.expected_sending_domain = (
            expected_sending_domain.strip().lower()
            if expected_sending_domain and expected_sending_domain.strip()
            else None
        )
        if self.mode == MODE_WEBSITE_INQUIRY_DELIVERY:
            # Website inquiry delivery MUST authenticate using the client's website Resend API key,
            # and MUST NEVER fall back to the internal RESEND_API_KEY.
            self.api_key = "" if api_key is None else str(api_key).strip()
        else:
            self.api_key = os.getenv(RESEND_API_KEY_ENV, "") if api_key is None else str(api_key).strip()

        self.store = store or SentReportStore(store_path)
        self.endpoint = endpoint
        self.requester = requester
        self.cache_ttl_seconds = cache_ttl_seconds
        self.retention_days = retention_days
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._cache: dict[tuple[Any, ...], tuple[datetime, dict[str, Any]]] = {}

    @classmethod
    def from_settings(
        cls,
        *,
        requester: Callable[[str, str], Mapping[str, Any]] = default_resend_metrics_requester,
        endpoint: str = RESEND_EMAIL_METRICS_ENDPOINT,
        api_key: str | None = None,
        store: SentReportStore | None = None,
    ) -> "ResendEmailMetricsSource":
        """Construct the source from the internal report-delivery settings."""
        settings = Settings.from_env()
        return cls(
            api_key=settings.resend_api_key if api_key is None else api_key,
            mode=MODE_INTERNAL_REPORT_DELIVERY,
            store=store or SentReportStore(settings.report_delivery_store_path),
            endpoint=endpoint,
            requester=requester,
            cache_ttl_seconds=settings.report_delivery_cache_ttl_seconds,
            retention_days=settings.report_delivery_retention_days,
        )

    def is_configured(self) -> bool:
        if self.mode == MODE_WEBSITE_INQUIRY_DELIVERY:
            return bool(self.api_key.strip())
        return bool(self.api_key.strip()) and self.store.is_configured

    def clear_cache(self) -> None:
        self._cache.clear()

    def _now(self) -> datetime:
        current = self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    @staticmethod
    def _cache_key(
        mode: str,
        client_id: str,
        report_type: str,
        start_date: str,
        end_date: str,
        timezone_name: str,
        email_ids: tuple[str, ...] = (),
        domain: str | None = None,
    ) -> tuple[Any, ...]:
        return (mode, client_id, report_type, start_date, end_date, timezone_name, email_ids, domain)

    def _base_result(
        self,
        *,
        client_id: str,
        report_type: str,
        start_date: str,
        end_date: str,
        timezone_name: str,
    ) -> dict[str, Any]:
        return {
            "source": self.source_name,
            "mode": self.mode,
            "status": SourceAvailability.NOT_CONFIGURED.value,
            "client_id": client_id,
            "report_type": report_type,
            "start_date": start_date,
            "end_date": end_date,
            "timezone": timezone_name,
            "metrics": {},
            "tracked_report_count": 0,
            "queried_report_count": 0,
            "successful_report_count": 0,
            "failed_report_count": 0,
            "retention_excluded_count": 0,
            "provider_retention_clamped": False,
            "successful_batches": 0,
            "failed_batches": 0,
            "cache_hit": False,
            "retention_clamped": False,
            "reason": None,
        }

    @staticmethod
    def _safe_reason(status: str, *, unavailable: bool = False) -> str:
        if unavailable:
            return "Resend Email Metrics is currently unavailable for the requested report window."
        if status == SourceAvailability.ERROR.value:
            return "Resend Email Metrics returned an unusable provider response."
        return "No stored report email IDs match the selected client and reporting window."

    def _cached(self, key: tuple[Any, ...], now: datetime) -> dict[str, Any] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, result = entry
        if now >= expires_at:
            self._cache.pop(key, None)
            return None
        cached = copy.deepcopy(result)
        cached["cache_hit"] = True
        return cached

    def _cache_result(self, key: tuple[Any, ...], result: dict[str, Any], now: datetime) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        if result.get("status") not in {
            SourceAvailability.AVAILABLE.value,
            SourceAvailability.EMPTY.value,
        }:
            return
        stored = copy.deepcopy(result)
        stored["cache_hit"] = False
        self._cache[key] = (now + timedelta(seconds=self.cache_ttl_seconds), stored)

    def _request_batch(
        self,
        ids: list[str],
        start_date: str,
        end_date: str,
        timezone_name: str,
        domain: str | None = None,
    ) -> Mapping[str, Any]:
        # Resend requires the email dimension when filtering by email IDs. Use
        # one repeated array parameter per ID; comma-joining would be a
        # different value and can silently defeat the provider-side filter.
        # The caller creates batches of at most 100 IDs before reaching this
        # method.
        params: list[tuple[str, str]] = [
            ("start_date", start_date),
            ("end_date", end_date),
            ("timezone", timezone_name),
        ]
        if ids:
            params.append(("dimensions", "email"))
            params.append(("metrics", ",".join(RESEND_COUNT_METRICS)))
            for email_id in ids:
                params.append(("email_id[]", email_id))
        else:
            params.append(("metrics", ",".join(RESEND_COUNT_METRICS)))
            effective_domain = domain or self.expected_sending_domain
            if effective_domain:
                params.append(("domain", effective_domain))

        separator = "&" if "?" in self.endpoint else "?"
        url = f"{self.endpoint}{separator}{urllib.parse.urlencode(params)}"
        response = self.requester(url, self.api_key)
        if not isinstance(response, Mapping):
            raise ValueError("Provider response is not an object.")
        totals = response.get("totals")
        if not isinstance(totals, Mapping):
            raise ValueError("Provider response has no totals object.")

        # Validate domain if expected
        effective_domain = domain or self.expected_sending_domain
        if effective_domain:
            returned_domain = response.get("domain") or response.get("sending_domain")
            if returned_domain and isinstance(returned_domain, str):
                returned_clean = returned_domain.strip().lower()
                if returned_clean and returned_clean != effective_domain.lower():
                    raise ValueError(
                        f"Returned sending domain '{returned_clean}' does not match expected domain '{effective_domain}'."
                    )
            data_rows = response.get("data")
            if isinstance(data_rows, list):
                for row in data_rows:
                    if isinstance(row, Mapping) and "domain" in row:
                        row_domain = str(row["domain"]).strip().lower()
                        if row_domain and row_domain != effective_domain.lower():
                            raise ValueError(
                                f"Returned sending domain '{row_domain}' does not match expected domain '{effective_domain}'."
                            )

        return response

    @staticmethod
    def _extract_totals(response: Mapping[str, Any]) -> tuple[dict[str, int | float], bool]:
        totals: dict[str, int | float] = {}
        for name, raw_value in response.get("totals", {}).items():
            if name not in RESEND_COUNT_METRICS:
                continue
            value = _numeric_metric(raw_value)
            if value is not None:
                totals[name] = value
        missing_requested_metric = any(name not in totals for name in RESEND_COUNT_METRICS)
        if not totals:
            raise ValueError("Provider response has no usable requested metrics.")
        return totals, missing_requested_metric

    @staticmethod
    def _merge_totals(target: dict[str, int | float], batch: Mapping[str, int | float]) -> None:
        for name, value in batch.items():
            target[name] = target.get(name, 0) + value

    @staticmethod
    def _add_derived_rates(metrics: dict[str, int | float]) -> None:
        candidates = {
            "delivery_rate": _derive_rate(metrics.get("delivered"), metrics.get("sent")),
            "open_rate": _derive_rate(metrics.get("unique_opened"), metrics.get("delivered")),
            "click_rate": _derive_rate(metrics.get("unique_clicked"), metrics.get("delivered")),
            "bounce_rate": _derive_rate(metrics.get("bounced"), metrics.get("sent")),
            "complaint_rate": _derive_rate(metrics.get("complained"), metrics.get("delivered")),
            "unsubscribe_rate": _derive_rate(metrics.get("unsubscribed"), metrics.get("delivered")),
        }
        for name, value in candidates.items():
            if value is not None:
                metrics[name] = value

    def fetch_metrics(
        self,
        client_id: str,
        start_date: str,
        end_date: str,
        timezone: str = "America/New_York",
        report_type: Any = "performance",
        *,
        timezone_name: str | None = None,
        email_ids: list[str] | tuple[str, ...] | None = None,
        expected_sending_domain: str | None = None,
    ) -> dict[str, Any]:
        """Return honest aggregate metrics for one exact report selection."""
        report_slug = _enum_value(report_type)
        effective_timezone = timezone_name or timezone or "America/New_York"
        try:
            client_slug = _enum_value(client_id)
            if not client_slug or not all(character.isalnum() or character in "_-" for character in client_slug):
                raise ValueError
            start = _validate_date(start_date, "start_date")
            end = _validate_date(end_date, "end_date")
            if _date_part(start) > _date_part(end):
                raise ValueError
            timezone_value = _validate_timezone(effective_timezone)
            if not report_slug or not all(character.isalnum() or character in "_-" for character in report_slug):
                raise ValueError
        except ValueError:
            result = self._base_result(
                client_id=str(client_id).strip().lower(),
                report_type=report_slug,
                start_date=str(start_date).strip(),
                end_date=str(end_date).strip(),
                timezone_name=str(effective_timezone).strip(),
            )
            result["status"] = SourceAvailability.ERROR.value
            result["reason"] = "The report delivery metrics selection is invalid."
            return result

        result = self._base_result(
            client_id=client_slug,
            report_type=report_slug,
            start_date=start,
            end_date=end,
            timezone_name=timezone_value,
        )
        now = self._now()

        # =========================================================================
        # Mode: WEBSITE_INQUIRY_DELIVERY
        # =========================================================================
        if self.mode == MODE_WEBSITE_INQUIRY_DELIVERY:
            if not self.api_key.strip():
                result["reason"] = "Website metrics credential is not configured."
                return result

            effective_domain = (
                expected_sending_domain.strip().lower()
                if expected_sending_domain and expected_sending_domain.strip()
                else self.expected_sending_domain
            )

            if email_ids:
                target_ids = [str(eid).strip() for eid in email_ids if str(eid).strip()]
                batches = [
                    target_ids[index : index + MAX_EMAIL_IDS_PER_REQUEST]
                    for index in range(0, len(target_ids), MAX_EMAIL_IDS_PER_REQUEST)
                ]
            else:
                target_ids = []
                batches = [[]]

            key = self._cache_key(
                self.mode,
                client_slug,
                report_slug,
                start,
                end,
                timezone_value,
                tuple(target_ids),
                effective_domain,
            )
            cached = self._cached(key, now)
            if cached is not None:
                return cached

            merged_metrics: dict[str, int | float] = {}
            successful_batches = 0
            failed_batches = 0
            successful_report_count = 0
            failed_report_count = 0
            partial_provider_batch = False
            provider_retention_clamped = False
            unavailable_failures = 0
            attempted_failures = 0
            domain_mismatch = False

            for batch in batches:
                try:
                    response = self._request_batch(
                        batch,
                        start,
                        end,
                        timezone_value,
                        domain=effective_domain,
                    )
                    batch_totals, missing_requested_metric = self._extract_totals(response)
                    self._merge_totals(merged_metrics, batch_totals)
                    successful_batches += 1
                    successful_report_count += (
                        len(batch)
                        if batch
                        else int(batch_totals.get("delivered", batch_totals.get("sent", 0)))
                    )
                    partial_provider_batch = partial_provider_batch or missing_requested_metric
                    response_start = response.get("start_date")
                    if isinstance(response_start, str) and response_start.strip():
                        try:
                            provider_retention_clamped = provider_retention_clamped or (
                                _date_part(_validate_date(response_start, "provider_start_date"))
                                > _date_part(start)
                            )
                        except ValueError:
                            partial_provider_batch = True
                except ValueError as exc:
                    failed_batches += 1
                    failed_report_count += len(batch) if batch else 1
                    attempted_failures += 1
                    if "domain" in str(exc).lower():
                        domain_mismatch = True
                except ResendMetricsRequestError as exc:
                    failed_batches += 1
                    failed_report_count += len(batch) if batch else 1
                    attempted_failures += 1
                    if exc.status_code in {401, 403, 404}:
                        unavailable_failures += 1
                except urllib.error.HTTPError as exc:
                    failed_batches += 1
                    failed_report_count += len(batch) if batch else 1
                    attempted_failures += 1
                    if exc.code in {401, 403, 404}:
                        unavailable_failures += 1
                except PermissionError:
                    failed_batches += 1
                    failed_report_count += len(batch) if batch else 1
                    attempted_failures += 1
                    unavailable_failures += 1
                except Exception:
                    failed_batches += 1
                    failed_report_count += len(batch) if batch else 1
                    attempted_failures += 1

            result["metrics"] = merged_metrics
            result["tracked_report_count"] = (
                len(target_ids)
                if target_ids
                else int(merged_metrics.get("delivered", merged_metrics.get("sent", 0)))
            )
            result["queried_report_count"] = (
                len(target_ids)
                if target_ids
                else int(merged_metrics.get("delivered", merged_metrics.get("sent", 0)))
            )
            result["successful_report_count"] = successful_report_count
            result["failed_report_count"] = failed_report_count
            result["successful_batches"] = successful_batches
            result["failed_batches"] = failed_batches
            result["provider_retention_clamped"] = provider_retention_clamped
            self._add_derived_rates(merged_metrics)

            if domain_mismatch:
                result["status"] = SourceAvailability.ERROR.value
                result["reason"] = "Unexpected sender-domain mismatch."
                result["domain_mismatch"] = True
                return result

            if successful_batches == 0:
                result["status"] = (
                    SourceAvailability.UNAVAILABLE.value
                    if unavailable_failures == attempted_failures and attempted_failures > 0
                    else SourceAvailability.ERROR.value
                )
                result["reason"] = (
                    "Website inquiry delivery metrics are unavailable for the requested report window."
                    if result["status"] == SourceAvailability.UNAVAILABLE.value
                    else "Website inquiry delivery metrics returned an unusable provider response."
                )
                return result

            if failed_batches or partial_provider_batch or provider_retention_clamped:
                result["status"] = SourceAvailability.PARTIAL.value
                reasons = []
                if failed_batches:
                    reasons.append("Some Resend metric batches failed; totals exclude those batches.")
                if partial_provider_batch:
                    reasons.append("The provider omitted one or more requested metric totals.")
                if provider_retention_clamped:
                    reasons.append("The provider clamped the requested start date to its retention window.")
                result["reason"] = " ".join(reasons)
            else:
                result["status"] = SourceAvailability.AVAILABLE.value

            self._cache_result(key, result, now)
            return result

        # =========================================================================
        # Mode: INTERNAL_REPORT_DELIVERY
        # =========================================================================
        if not self.store.is_configured:
            result["reason"] = "Report-delivery persistence is not configured."
            return result
        if not self.api_key.strip():
            result["reason"] = "The internal Resend metrics credential is not configured."
            return result

        cutoff = None
        if self.retention_days is not None:
            cutoff = now - timedelta(days=self.retention_days)
        try:
            stored = self.store.find_sent_reports(
                client_id=client_slug,
                report_type=report_slug,
                reporting_window_start=start,
                reporting_window_end=end,
                timezone_name=timezone_value,
                retention_cutoff=cutoff,
            )
        except Exception:
            result["status"] = SourceAvailability.ERROR.value
            result["reason"] = "Report-delivery persistence could not be read."
            return result

        # Keep the total matched count separate from the retained/queried set
        # so retention loss cannot look like a smaller successful population.
        result["tracked_report_count"] = len(stored.records) + stored.expired_count
        result["queried_report_count"] = len(stored.records)
        result["retention_excluded_count"] = stored.expired_count
        result["retention_clamped"] = stored.expired_count > 0
        key = self._cache_key(
            self.mode,
            client_slug,
            report_slug,
            start,
            end,
            timezone_value,
            tuple(stored.email_ids),
        )
        cached = self._cached(key, now)
        if cached is not None:
            return cached
        if not stored.records:
            if stored.expired_count:
                result["status"] = SourceAvailability.UNAVAILABLE.value
                result["reason"] = "Matching report IDs exist only outside configured local retention."
            else:
                result["status"] = SourceAvailability.EMPTY.value
                result["reason"] = self._safe_reason(SourceAvailability.EMPTY.value)
            self._cache_result(key, result, now)
            return result

        batches = [
            stored.email_ids[index : index + MAX_EMAIL_IDS_PER_REQUEST]
            for index in range(0, len(stored.email_ids), MAX_EMAIL_IDS_PER_REQUEST)
        ]
        merged_metrics = {}
        successful_batches = 0
        failed_batches = 0
        successful_report_count = 0
        failed_report_count = 0
        partial_provider_batch = False
        provider_retention_clamped = False
        unavailable_failures = 0
        attempted_failures = 0

        for batch in batches:
            try:
                response = self._request_batch(batch, start, end, timezone_value)
                batch_totals, missing_requested_metric = self._extract_totals(response)
                self._merge_totals(merged_metrics, batch_totals)
                successful_batches += 1
                successful_report_count += len(batch)
                partial_provider_batch = partial_provider_batch or missing_requested_metric
                response_start = response.get("start_date")
                if isinstance(response_start, str) and response_start.strip():
                    try:
                        provider_retention_clamped = provider_retention_clamped or (
                            _date_part(_validate_date(response_start, "provider_start_date"))
                            > _date_part(start)
                        )
                    except ValueError:
                        partial_provider_batch = True
            except ResendMetricsRequestError as exc:
                failed_batches += 1
                failed_report_count += len(batch)
                attempted_failures += 1
                if exc.status_code in {401, 403, 404}:
                    unavailable_failures += 1
            except urllib.error.HTTPError as exc:
                failed_batches += 1
                failed_report_count += len(batch)
                attempted_failures += 1
                if exc.code in {401, 403, 404}:
                    unavailable_failures += 1
            except PermissionError:
                failed_batches += 1
                failed_report_count += len(batch)
                attempted_failures += 1
                unavailable_failures += 1
            except Exception:
                failed_batches += 1
                failed_report_count += len(batch)
                attempted_failures += 1

        result["metrics"] = merged_metrics
        result["successful_report_count"] = successful_report_count
        result["failed_report_count"] = failed_report_count
        result["successful_batches"] = successful_batches
        result["failed_batches"] = failed_batches
        result["provider_retention_clamped"] = provider_retention_clamped
        self._add_derived_rates(merged_metrics)

        if successful_batches == 0:
            result["status"] = (
                SourceAvailability.UNAVAILABLE.value
                if unavailable_failures == attempted_failures and attempted_failures > 0
                else SourceAvailability.ERROR.value
            )
            result["reason"] = self._safe_reason(
                result["status"],
                unavailable=result["status"] == SourceAvailability.UNAVAILABLE.value,
            )
            return result

        if failed_batches or partial_provider_batch or stored.expired_count or provider_retention_clamped:
            result["status"] = SourceAvailability.PARTIAL.value
            reasons = []
            if failed_batches:
                reasons.append("Some Resend metric batches failed; totals exclude those batches.")
            if partial_provider_batch:
                reasons.append("The provider omitted one or more requested metric totals.")
            if stored.expired_count:
                reasons.append("Some matching report IDs were outside local retention and were not queried.")
            if provider_retention_clamped:
                reasons.append("The provider clamped the requested start date to its retention window.")
            result["reason"] = " ".join(reasons)
        else:
            result["status"] = SourceAvailability.AVAILABLE.value
        self._cache_result(key, result, now)
        return result

    @staticmethod
    def to_contract(result: Mapping[str, Any]) -> ReportDeliveryMetrics:
        """Validate a client-safe source result against the report contract."""
        return ReportDeliveryMetrics.model_validate(dict(result))

    @staticmethod
    def to_website_inquiry_contract(
        result: Mapping[str, Any],
        *,
        source_name: str = "website_delivery_aggregate",
    ) -> WebsiteInquiryMetrics:
        """Validate a client-safe source result against the website inquiry contract."""
        raw_status = result.get("status", SourceAvailability.NOT_CONFIGURED.value)
        try:
            status = SourceAvailability(str(getattr(raw_status, "value", raw_status)))
        except ValueError:
            status = SourceAvailability.ERROR

        metrics = result.get("metrics") or {}
        current_inquiries = _numeric_metric(metrics.get("delivered", metrics.get("sent")))
        if isinstance(current_inquiries, float):
            current_inquiries = int(current_inquiries)

        return WebsiteInquiryMetrics(
            status=status,
            source=source_name,
            current_inquiries=current_inquiries,
            delivery_metrics=dict(metrics),
            credential_reference_configured=True,
            reason=result.get("reason"),
        )

    def fetch_contract(
        self,
        client_id: str,
        start_date: str,
        end_date: str,
        timezone: str,
        report_type: Any = "performance",
        *,
        timezone_name: str | None = None,
    ) -> ReportDeliveryMetrics:
        """Fetch aggregate metrics as the redacted report-facing contract."""
        return self.to_contract(
            self.fetch_metrics(
                client_id,
                start_date,
                end_date,
                timezone,
                report_type,
                timezone_name=timezone_name,
            )
        )

    def fetch_website_inquiry_contract(
        self,
        client_id: str,
        start_date: str,
        end_date: str,
        timezone: str = "America/New_York",
        report_type: Any = "performance",
        *,
        timezone_name: str | None = None,
        email_ids: list[str] | tuple[str, ...] | None = None,
        expected_sending_domain: str | None = None,
        source_name: str = "website_delivery_aggregate",
    ) -> WebsiteInquiryMetrics:
        """Fetch website inquiry metrics as the WebsiteInquiryMetrics contract."""
        raw = self.fetch_metrics(
            client_id,
            start_date,
            end_date,
            timezone,
            report_type,
            timezone_name=timezone_name,
            email_ids=email_ids,
            expected_sending_domain=expected_sending_domain,
        )
        return self.to_website_inquiry_contract(raw, source_name=source_name)

    fetch_report_delivery_metrics = fetch_contract
    fetch_report_delivery_contract = fetch_contract

    fetch_email_metrics = fetch_metrics
    get_metrics = fetch_metrics


# Match the naming used by the existing GA4/GSC/GBP adapters while retaining a
# source-oriented name for callers that prefer it.
ResendEmailMetricsExtractor = ResendEmailMetricsSource
