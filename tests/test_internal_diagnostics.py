"""Tests for internal delivery diagnostics evaluation and alerting rules."""

from __future__ import annotations

import pytest

from app.analytics.contracts import ReportDeliveryMetrics, SourceAvailability, WebsiteInquiryMetrics
from app.config import WebsiteInquiryMetricsConfig
from app.delivery.internal_diagnostics import (
    RULE_INQUIRY_DELIVERY_FAILURE,
    RULE_METRICS_WEBHOOK_DISAGREEMENT,
    RULE_MISSING_WEBSITE_CREDENTIALS,
    RULE_PERMANENT_BOUNCE,
    RULE_RECIPIENT_SUPPRESSION,
    RULE_SPAM_COMPLAINT,
    RULE_UNEXPECTED_SENDER_DOMAIN,
    AlertCategory,
    AlertSeverity,
    evaluate_delivery_diagnostics,
    evaluate_report_delivery_diagnostics,
    evaluate_website_inquiry_diagnostics,
)


def test_rule_1_permanent_bounce_triggers_critical_alert():
    report_metrics = ReportDeliveryMetrics(
        status=SourceAvailability.AVAILABLE,
        metrics={"bounced_permanent": 1, "bounced": 1, "sent": 10, "delivered": 9},
    )
    alerts = evaluate_report_delivery_diagnostics(report_metrics)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.rule == RULE_PERMANENT_BOUNCE
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.category == AlertCategory.REPORT_DELIVERY
    assert "permanent bounce" in alert.message.lower()
    assert alert.details["bounced_permanent"] == 1


def test_rule_2_spam_complaint_triggers_critical_alert():
    report_metrics = ReportDeliveryMetrics(
        status=SourceAvailability.AVAILABLE,
        metrics={"complained": 1, "sent": 10, "delivered": 9},
    )
    alerts = evaluate_report_delivery_diagnostics(report_metrics)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.rule == RULE_SPAM_COMPLAINT
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.category == AlertCategory.REPORT_DELIVERY
    assert "spam complaint" in alert.message.lower()
    assert alert.details["complained"] == 1


def test_rule_3_recipient_suppression_triggers_critical_alert():
    report_metrics = ReportDeliveryMetrics(
        status=SourceAvailability.AVAILABLE,
        metrics={"suppressed": 2, "sent": 10, "delivered": 8},
    )
    alerts = evaluate_report_delivery_diagnostics(report_metrics)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.rule == RULE_RECIPIENT_SUPPRESSION
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.category == AlertCategory.REPORT_DELIVERY
    assert "suppression" in alert.message.lower()
    assert alert.details["suppressed"] == 2


def test_rule_4_failed_inquiry_delivery_triggers_critical_alert():
    website_metrics = WebsiteInquiryMetrics(
        status=SourceAvailability.AVAILABLE,
        current_inquiries=5,
        delivery_metrics={"sent": 6, "delivered": 5, "failed": 1, "bounced": 1},
    )
    alerts = evaluate_website_inquiry_diagnostics(website_metrics)
    assert any(a.rule == RULE_INQUIRY_DELIVERY_FAILURE for a in alerts)
    failure_alert = next(a for a in alerts if a.rule == RULE_INQUIRY_DELIVERY_FAILURE)
    assert failure_alert.severity == AlertSeverity.CRITICAL
    assert failure_alert.category == AlertCategory.WEBSITE_INQUIRY
    assert "failed" in failure_alert.message.lower()


def test_rule_5_metrics_webhook_disagreement_triggers_warning_alert():
    # Recorded event has 8 form submissions, but provider delivery metrics shows 5 delivered
    website_metrics = WebsiteInquiryMetrics(
        status=SourceAvailability.AVAILABLE,
        current_inquiries=8,
        inquiry_events={"form_submit": 8},
        delivery_metrics={"delivered": 5, "sent": 5},
    )
    alerts = evaluate_website_inquiry_diagnostics(website_metrics)
    assert any(a.rule == RULE_METRICS_WEBHOOK_DISAGREEMENT for a in alerts)
    disagreement_alert = next(a for a in alerts if a.rule == RULE_METRICS_WEBHOOK_DISAGREEMENT)
    assert disagreement_alert.severity == AlertSeverity.WARNING
    assert disagreement_alert.category == AlertCategory.WEBSITE_INQUIRY
    assert disagreement_alert.details["recorded_event_count"] == 8
    assert disagreement_alert.details["resend_delivery_count"] == 5
    assert disagreement_alert.details["difference"] == 3


def test_rule_6_missing_website_credentials_triggers_warning_alert():
    config = WebsiteInquiryMetricsConfig(
        enabled=True,
        provider="secret_manager",
        secret_manager_ref="projects/example/secrets/missing-secret/versions/latest",
    )
    website_metrics = WebsiteInquiryMetrics(
        status=SourceAvailability.NOT_CONFIGURED,
        credential_reference_configured=False,
        reason="Website metrics credential reference is not resolvable.",
    )
    alerts = evaluate_website_inquiry_diagnostics(website_metrics, config=config)
    assert any(a.rule == RULE_MISSING_WEBSITE_CREDENTIALS for a in alerts)
    missing_alert = next(a for a in alerts if a.rule == RULE_MISSING_WEBSITE_CREDENTIALS)
    assert missing_alert.severity == AlertSeverity.WARNING
    assert missing_alert.category == AlertCategory.WEBSITE_INQUIRY
    assert "credentials are missing" in missing_alert.message.lower()


def test_rule_7_unexpected_sender_domain_triggers_critical_alert():
    config = WebsiteInquiryMetricsConfig(
        enabled=True,
        provider="secret_manager",
        secret_manager_ref="projects/example/secrets/valid-secret/versions/latest",
        expected_website_sending_domain="thehouseofdentalwp.com",
    )
    website_metrics = WebsiteInquiryMetrics(
        status=SourceAvailability.AVAILABLE,
        delivery_metrics={"delivered": 5, "domain": "rogue-sender.com"},
    )
    alerts = evaluate_website_inquiry_diagnostics(website_metrics, config=config)
    assert any(a.rule == RULE_UNEXPECTED_SENDER_DOMAIN for a in alerts)
    domain_alert = next(a for a in alerts if a.rule == RULE_UNEXPECTED_SENDER_DOMAIN)
    assert domain_alert.severity == AlertSeverity.CRITICAL
    assert domain_alert.category == AlertCategory.WEBSITE_INQUIRY
    assert "domain mismatch" in domain_alert.message.lower()


def test_evaluate_delivery_diagnostics_aggregates_multiple_sources():
    report_metrics = ReportDeliveryMetrics(
        status=SourceAvailability.AVAILABLE,
        metrics={"complained": 1, "sent": 10, "delivered": 9},
    )
    website_metrics = WebsiteInquiryMetrics(
        status=SourceAvailability.AVAILABLE,
        delivery_metrics={"failed": 2, "sent": 5, "delivered": 3},
    )
    result = evaluate_delivery_diagnostics(report_metrics, website_metrics)
    assert result.has_critical is True
    assert len(result.alerts) == 2
    categories = {a.category for a in result.alerts}
    assert categories == {AlertCategory.REPORT_DELIVERY, AlertCategory.WEBSITE_INQUIRY}
