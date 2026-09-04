"""Internal delivery diagnostics evaluator and alerting rules.

Evaluates internal report delivery metrics and client website inquiry metrics
separately against seven operational health and delivery rules.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field

from app.analytics.contracts import ReportDeliveryMetrics, SourceAvailability, WebsiteInquiryMetrics
from app.config import WebsiteInquiryMetricsConfig


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertCategory(str, Enum):
    REPORT_DELIVERY = "report_delivery"
    WEBSITE_INQUIRY = "website_inquiry"


# Rule identifiers
RULE_PERMANENT_BOUNCE = "permanent_bounce"
RULE_SPAM_COMPLAINT = "spam_complaint"
RULE_RECIPIENT_SUPPRESSION = "recipient_suppression"
RULE_INQUIRY_DELIVERY_FAILURE = "inquiry_delivery_failure"
RULE_METRICS_WEBHOOK_DISAGREEMENT = "metrics_webhook_disagreement"
RULE_MISSING_WEBSITE_CREDENTIALS = "missing_website_credentials"
RULE_UNEXPECTED_SENDER_DOMAIN = "unexpected_sender_domain"


class DeliveryAlert(BaseModel):
    """An alert raised by internal delivery diagnostics evaluation."""

    rule: str
    severity: AlertSeverity
    category: AlertCategory
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticsEvaluationResult(BaseModel):
    """Aggregate result of diagnostics evaluation across report and website sources."""

    alerts: list[DeliveryAlert] = Field(default_factory=list)
    has_critical: bool = False
    has_warning: bool = False

    def add_alert(self, alert: DeliveryAlert) -> None:
        self.alerts.append(alert)
        if alert.severity == AlertSeverity.CRITICAL:
            self.has_critical = True
        elif alert.severity == AlertSeverity.WARNING:
            self.has_warning = True


def _to_metrics_dict(data: ReportDeliveryMetrics | Mapping[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, ReportDeliveryMetrics):
        return data.metrics or {}
    if isinstance(data, Mapping):
        return dict(data.get("metrics") or {})
    return {}


def evaluate_report_delivery_diagnostics(
    report_metrics: ReportDeliveryMetrics | Mapping[str, Any] | None,
) -> list[DeliveryAlert]:
    """Evaluate internal report delivery metrics against alerting rules."""
    alerts: list[DeliveryAlert] = []
    if report_metrics is None:
        return alerts

    metrics = _to_metrics_dict(report_metrics)
    if not metrics:
        return alerts

    # Rule 1: Permanent bounce detected
    bounced_perm = metrics.get("bounced_permanent") or 0
    bounced_total = metrics.get("bounced") or 0
    if bounced_perm > 0 or (bounced_perm == 0 and bounced_total > 0 and metrics.get("bounced_transient", 0) == 0):
        alerts.append(
            DeliveryAlert(
                rule=RULE_PERMANENT_BOUNCE,
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.REPORT_DELIVERY,
                message="Permanent bounce detected; report delivery to recipient failed permanently.",
                details={"bounced_permanent": bounced_perm, "bounced_total": bounced_total},
            )
        )

    # Rule 2: Spam complaint detected
    complained = metrics.get("complained") or 0
    if complained > 0:
        alerts.append(
            DeliveryAlert(
                rule=RULE_SPAM_COMPLAINT,
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.REPORT_DELIVERY,
                message="Spam complaint recorded from report recipient.",
                details={"complained": complained},
            )
        )

    # Rule 3: Suppression detected
    suppressed = metrics.get("suppressed") or 0
    if suppressed > 0:
        alerts.append(
            DeliveryAlert(
                rule=RULE_RECIPIENT_SUPPRESSION,
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.REPORT_DELIVERY,
                message="Report recipient is currently on the suppression list.",
                details={"suppressed": suppressed},
            )
        )

    return alerts


def evaluate_website_inquiry_diagnostics(
    website_metrics: WebsiteInquiryMetrics | Mapping[str, Any] | None,
    *,
    config: WebsiteInquiryMetricsConfig | None = None,
    domain_mismatch_detected: bool = False,
    domain_details: Mapping[str, Any] | None = None,
) -> list[DeliveryAlert]:
    """Evaluate client website inquiry delivery against alerting rules."""
    alerts: list[DeliveryAlert] = []

    # Check status and raw representation
    status_str: str = ""
    reason_str: str = ""
    inquiry_events: dict[str, Any] = {}
    delivery_metrics: dict[str, Any] = {}
    current_inquiries: Optional[int] = None
    cred_configured: bool = False

    if isinstance(website_metrics, WebsiteInquiryMetrics):
        status_str = getattr(website_metrics.status, "value", str(website_metrics.status))
        reason_str = website_metrics.reason or ""
        inquiry_events = website_metrics.inquiry_events or {}
        delivery_metrics = website_metrics.delivery_metrics or {}
        current_inquiries = website_metrics.current_inquiries
        cred_configured = website_metrics.credential_reference_configured
    elif isinstance(website_metrics, Mapping):
        status_str = str(getattr(website_metrics.get("status"), "value", website_metrics.get("status", "")))
        reason_str = str(website_metrics.get("reason") or "")
        inquiry_events = dict(website_metrics.get("inquiry_events") or {})
        delivery_metrics = dict(website_metrics.get("delivery_metrics") or website_metrics.get("metrics") or {})
        current_inquiries = website_metrics.get("current_inquiries")
        cred_configured = bool(website_metrics.get("credential_reference_configured", False))

    # Rule 6: Missing website credentials
    # When website inquiry metrics is enabled in config, but credentials are missing or unresolvable
    if config is not None and config.enabled:
        missing_ref = not config.secret_manager_ref.strip()
        is_not_configured = status_str == SourceAvailability.NOT_CONFIGURED.value
        cred_error = "credential" in reason_str.lower() or not cred_configured

        if missing_ref or is_not_configured or cred_error:
            alerts.append(
                DeliveryAlert(
                    rule=RULE_MISSING_WEBSITE_CREDENTIALS,
                    severity=AlertSeverity.WARNING,
                    category=AlertCategory.WEBSITE_INQUIRY,
                    message="Client website inquiry metrics is enabled but credentials are missing or unresolvable.",
                    details={
                        "secret_manager_ref": config.secret_manager_ref,
                        "status": status_str,
                        "reason": reason_str,
                    },
                )
            )

    # Rule 4: Failed inquiry delivery
    # Failed delivery on client website inquiries (failed > 0, bounced_permanent > 0, or bounced > 0)
    failed_count = delivery_metrics.get("failed") or 0
    bounced_perm = delivery_metrics.get("bounced_permanent") or 0
    bounced_total = delivery_metrics.get("bounced") or 0
    if failed_count > 0 or bounced_perm > 0 or bounced_total > 0:
        alerts.append(
            DeliveryAlert(
                rule=RULE_INQUIRY_DELIVERY_FAILURE,
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.WEBSITE_INQUIRY,
                message="Client website inquiry delivery failed; customer inquiry email was not delivered.",
                details={
                    "failed": failed_count,
                    "bounced_permanent": bounced_perm,
                    "bounced_total": bounced_total,
                },
            )
        )

    # Rule 5: Metrics / webhook disagreement
    # Discrepancy between recorded inquiry events (e.g. form_submit) and Resend metrics totals
    if inquiry_events and delivery_metrics:
        # Check form_submit or inquiry count against delivered / sent
        event_count = inquiry_events.get("form_submit")
        if event_count is None and "inquiry" in inquiry_events:
            event_count = inquiry_events.get("inquiry")
        delivery_count = delivery_metrics.get("delivered", delivery_metrics.get("sent"))

        if event_count is not None and delivery_count is not None and int(event_count) != int(delivery_count):
            alerts.append(
                DeliveryAlert(
                    rule=RULE_METRICS_WEBHOOK_DISAGREEMENT,
                    severity=AlertSeverity.WARNING,
                    category=AlertCategory.WEBSITE_INQUIRY,
                    message="Discrepancy detected between recorded inquiry events and Resend metrics totals.",
                    details={
                        "recorded_event_count": event_count,
                        "resend_delivery_count": delivery_count,
                        "difference": abs(int(event_count) - int(delivery_count)),
                    },
                )
            )
    elif current_inquiries is not None and delivery_metrics:
        delivery_count = delivery_metrics.get("delivered", delivery_metrics.get("sent"))
        if delivery_count is not None and int(current_inquiries) != int(delivery_count):
            alerts.append(
                DeliveryAlert(
                    rule=RULE_METRICS_WEBHOOK_DISAGREEMENT,
                    severity=AlertSeverity.WARNING,
                    category=AlertCategory.WEBSITE_INQUIRY,
                    message="Discrepancy detected between current inquiry count and Resend metrics totals.",
                    details={
                        "current_inquiries": current_inquiries,
                        "resend_delivery_count": delivery_count,
                    },
                )
            )

    # Rule 7: Unexpected sender-domain mismatch
    domain_mismatch = (
        domain_mismatch_detected
        or "domain mismatch" in reason_str.lower()
        or (isinstance(website_metrics, Mapping) and website_metrics.get("domain_mismatch") is True)
    )
    if not domain_mismatch and config is not None and config.expected_website_sending_domain:
        expected = config.expected_website_sending_domain.strip().lower()
        returned_domain = ""
        if domain_details and "domain" in domain_details:
            returned_domain = str(domain_details["domain"]).strip().lower()
        elif "domain" in delivery_metrics:
            returned_domain = str(delivery_metrics["domain"]).strip().lower()

        if returned_domain and returned_domain != expected:
            domain_mismatch = True

    if domain_mismatch:
        alerts.append(
            DeliveryAlert(
                rule=RULE_UNEXPECTED_SENDER_DOMAIN,
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.WEBSITE_INQUIRY,
                message="Email sending domain mismatch detected against expected domain.",
                details={
                    "expected_domain": config.expected_website_sending_domain if config else None,
                    "domain_details": dict(domain_details or {}),
                },
            )
        )

    return alerts


def evaluate_delivery_diagnostics(
    report_metrics: ReportDeliveryMetrics | Mapping[str, Any] | None = None,
    website_metrics: WebsiteInquiryMetrics | Mapping[str, Any] | None = None,
    *,
    config: WebsiteInquiryMetricsConfig | None = None,
    domain_mismatch_detected: bool = False,
    domain_details: Mapping[str, Any] | None = None,
) -> DiagnosticsEvaluationResult:
    """Evaluate both report delivery and website inquiry delivery sources."""
    result = DiagnosticsEvaluationResult()

    report_alerts = evaluate_report_delivery_diagnostics(report_metrics)
    for alert in report_alerts:
        result.add_alert(alert)

    website_alerts = evaluate_website_inquiry_diagnostics(
        website_metrics,
        config=config,
        domain_mismatch_detected=domain_mismatch_detected,
        domain_details=domain_details,
    )
    for alert in website_alerts:
        result.add_alert(alert)

    return result


evaluate_all_diagnostics = evaluate_delivery_diagnostics
