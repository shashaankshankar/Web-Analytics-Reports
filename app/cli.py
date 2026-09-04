from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.ai.agent import ExploratoryGrowthAgent
from app.ai.analyst import GrowthAnalyst
from app.ai.tools import MultiSourceAnalyticsToolkit
from app.analytics.contracts import (
    FullGrowthBriefing,
    REPORT_SPECS,
    ReportDeliveryMetrics,
    ReportMode,
    ReportType,
    SourceAvailability,
    WebsiteInquiryMetrics,
)
from app.analytics.metrics import aggregate_growth_metrics, calculate_date_ranges
from app.analytics.periods import ReportWindowError, coverage_from_ga4, select_report_window
from app.config import (
    ClientConfig,
    Settings,
    is_production_dispatch_allowed,
    list_available_clients,
    load_client_config,
    load_client_config_by_slug,
)
from app.delivery.audit_template import render_exploration_audit_html
from app.delivery.email_template import render_growth_email_html
from app.delivery.internal_diagnostics import AlertSeverity, evaluate_delivery_diagnostics
from app.delivery.pdf_builder import build_executive_pdf
from app.delivery.report_store import SentReportStore
from app.delivery.sender import ResendEmailSender, is_valid_email
from app.delivery.weekly_digest_template import render_weekly_digest_html
from app.sources.ga4 import GA4Extractor
from app.sources.gbp import GoogleBusinessProfileExtractor
from app.sources.gsc import SearchConsoleExtractor
from app.sources.resend_email_metrics import ResendEmailMetricsSource
from app.sources.website_inquiries import WebsiteInquiryMetricsSource

logger = logging.getLogger(__name__)

VERIFIER_FAILURE_STATUSES = {
    "deterministic_only_verifier_unavailable",
    "provider_unavailable",
    "provider_error",
    "invalid_response",
}


def _persist_dispatch_provenance(
    *,
    dispatch: dict,
    client: ClientConfig,
    report_type: str,
    observation_start_date: str,
    observation_end_date: str,
    delivery_kind: str,
    has_attachment: bool,
    recipient_role: str = "client",
    cloud_run_revision: str | None = None,
    idempotency_key: str = "",
) -> None:
    """Persist only safe provenance after Resend accepts an internal email."""
    if dispatch.get("status") != "sent":
        return
    resend_email_id = dispatch.get("id")
    if not isinstance(resend_email_id, str) or not resend_email_id.strip():
        raise RuntimeError("Email was accepted but no provider ID was available for delivery tracking.")
    settings = Settings.from_env()
    if not settings.report_delivery_store_path:
        logger.warning(
            "REPORT_EVENT report_delivery_tracking_unconfigured client_id=%s report_type=%s",
            client.client_id,
            report_type,
        )
        return
    revision = os.getenv("K_REVISION", "") if cloud_run_revision is None else str(cloud_run_revision)
    try:
        SentReportStore(settings.report_delivery_store_path).record_sent_report(
            resend_email_id=resend_email_id,
            client_id=client.client_id,
            report_type=report_type,
            reporting_window_start=observation_start_date,
            reporting_window_end=observation_end_date,
            timezone_name=client.timezone,
            sent_at=datetime.now(timezone.utc),
            recipient_role=recipient_role,
            cloud_run_revision=revision,
            idempotency_key=idempotency_key,
            technical_metadata={
                "delivery_kind": delivery_kind,
                "has_attachment": has_attachment,
                "provider_status": dispatch.get("status"),
            },
        )
    except Exception as exc:
        logger.exception(
            "REPORT_EVENT report_delivery_tracking_failed client_id=%s report_type=%s",
            client.client_id,
            report_type,
        )
        raise RuntimeError(
            "Email was accepted but delivery provenance could not be persisted; delivery metrics may be incomplete."
        ) from exc


def validate_pre_send_qa(
    briefing: FullGrowthBriefing,
    client: ClientConfig,
    pdf_bytes: Optional[bytes] = None,
    recipient: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """Execute deterministic pre-send QA validation checks."""
    issues: list[str] = []
    analytics = briefing.analytics

    if not analytics.period_start or not analytics.period_end:
        issues.append("Missing reporting period start or end dates.")
    if analytics.period_start >= analytics.period_end:
        issues.append("Period start date must be before period end date.")
    ga4_status = analytics.source_statuses.get("ga4", {})
    if isinstance(ga4_status, dict):
        ga4_status = ga4_status.get("status")
    if ga4_status in {
        SourceAvailability.ERROR.value,
        SourceAvailability.UNAVAILABLE.value,
        SourceAvailability.NOT_CONFIGURED.value,
        SourceAvailability.EMPTY.value,
    }:
        issues.append("GA4 source did not provide a usable current summary for the generated report.")

    for metric in analytics.core_metrics:
        if (metric.current_value is not None and metric.current_value < 0) or (metric.prior_value is not None and metric.prior_value < 0):
            issues.append(f"Negative metric value encountered in {metric.metric_name}.")
        if metric.metric_name == "conversion_rate" and metric.current_value is not None and not 0.0 <= metric.current_value <= 100.0:
            issues.append(f"Conversion rate {metric.current_value}% out of valid [0, 100] bound.")

    if not briefing.company_name.strip():
        issues.append("Company name is empty.")
    spec = REPORT_SPECS.get(briefing.report_type)
    if spec and spec.requires_pdf and (pdf_bytes is None or len(pdf_bytes) < 100):
        issues.append("PDF attachment required for report type but missing or empty.")
    if recipient and not is_valid_email(recipient):
        issues.append(f"Invalid recipient email format: {recipient}")
    if briefing.exploration_audit and not briefing.exploration_audit.evidence.client_id == client.client_id:
        issues.append("Exploration evidence is not scoped to the configured client.")
    if briefing.report_mode == ReportMode.INITIAL_BASELINE:
        if not briefing.measurement_start_date:
            issues.append("Initial baseline is missing measurement_start_date.")
        if briefing.comparison_suppressed is not True:
            issues.append("Initial baseline must suppress comparison data.")
    return (len(issues) == 0, issues)


def _combined_search_status(current: dict, prior: Optional[dict]) -> str:
    if prior is None:
        return current.get("status", SourceAvailability.UNAVAILABLE.value)
    statuses = {current.get("status"), prior.get("status")}
    if SourceAvailability.ERROR.value in statuses:
        return SourceAvailability.ERROR.value
    if prior.get("truncated"):
        return SourceAvailability.UNAVAILABLE.value
    if statuses == {SourceAvailability.AVAILABLE.value}:
        return SourceAvailability.AVAILABLE.value
    if SourceAvailability.PARTIAL.value in statuses:
        return SourceAvailability.PARTIAL.value
    if SourceAvailability.NOT_CONFIGURED.value in statuses:
        return SourceAvailability.NOT_CONFIGURED.value
    if SourceAvailability.UNAVAILABLE.value in statuses:
        return SourceAvailability.UNAVAILABLE.value
    return SourceAvailability.EMPTY.value


def _safe_report_delivery_metrics(
    *,
    client: ClientConfig,
    report_type: ReportType,
    start_date: str,
    end_date: str,
    reason: str,
    status: SourceAvailability = SourceAvailability.ERROR,
) -> ReportDeliveryMetrics:
    """Build a redacted delivery result when the optional source cannot run."""
    return ReportDeliveryMetrics(
        status=status,
        client_id=client.client_id,
        report_type=report_type.value,
        start_date=start_date,
        end_date=end_date,
        timezone=client.timezone,
        reason=reason,
    )


def _safe_website_inquiry_metrics(
    *,
    client: ClientConfig,
    reason: str,
    status: SourceAvailability = SourceAvailability.ERROR,
) -> WebsiteInquiryMetrics:
    """Build a redacted website-inquiry result without exposing provider errors."""
    return WebsiteInquiryMetrics(
        status=status,
        source=client.website_inquiry_metrics.aggregate_source or "website_inquiries",
        credential_reference_configured=bool(client.website_inquiry_metrics.secret_manager_ref.strip()),
        reason=reason,
    )


def _collect_delivery_metrics(
    *,
    client: ClientConfig,
    report_type: ReportType,
    start_date: str,
    end_date: str,
    report_delivery_source: object | None = None,
    website_inquiry_source: object | None = None,
) -> tuple[ReportDeliveryMetrics, WebsiteInquiryMetrics]:
    """Collect optional delivery metrics while keeping failures non-fatal and safe."""
    report_source = report_delivery_source
    if report_source is None:
        try:
            report_source = ResendEmailMetricsSource.from_settings()
        except Exception:
            report_source = None

    if report_source is None or not callable(getattr(report_source, "fetch_contract", None)):
        report_delivery = _safe_report_delivery_metrics(
            client=client,
            report_type=report_type,
            start_date=start_date,
            end_date=end_date,
            status=SourceAvailability.NOT_CONFIGURED,
            reason="Report-delivery metrics are not configured.",
        )
    else:
        try:
            report_delivery = report_source.fetch_contract(
                client.client_id,
                start_date,
                end_date,
                client.timezone,
                report_type=report_type.value,
            )
            if isinstance(report_delivery, dict):
                report_delivery = ReportDeliveryMetrics.model_validate(report_delivery)
            if not isinstance(report_delivery, ReportDeliveryMetrics):
                raise ValueError
        except Exception:
            report_delivery = _safe_report_delivery_metrics(
                client=client,
                report_type=report_type,
                start_date=start_date,
                end_date=end_date,
                reason="Report-delivery metrics are unavailable for this report window.",
            )

    website_source = website_inquiry_source
    if website_source is None:
        try:
            website_source = WebsiteInquiryMetricsSource(client=client)
        except Exception:
            website_source = None

    if website_source is None or not callable(getattr(website_source, "fetch_metrics", None)):
        website_metrics = _safe_website_inquiry_metrics(
            client=client,
            status=SourceAvailability.NOT_CONFIGURED,
            reason="Website inquiry delivery metrics are not configured.",
        )
    else:
        try:
            raw_website_metrics = website_source.fetch_metrics(
                client.client_id,
                start_date,
                end_date,
                client.timezone,
            )
            website_metrics = (
                raw_website_metrics
                if isinstance(raw_website_metrics, WebsiteInquiryMetrics)
                else WebsiteInquiryMetrics.model_validate(raw_website_metrics)
            )
        except Exception:
            website_metrics = _safe_website_inquiry_metrics(
                client=client,
                reason="Website inquiry delivery metrics are unavailable for this report window.",
            )

    logger.info(
        "REPORT_EVENT delivery_metrics status=%s tracked_reports=%s website_inquiry_status=%s",
        report_delivery.status.value,
        report_delivery.tracked_report_count,
        website_metrics.status.value,
    )

    diagnostics_result = evaluate_delivery_diagnostics(
        report_metrics=report_delivery,
        website_metrics=website_metrics,
        config=client.website_inquiry_metrics,
    )
    for alert in diagnostics_result.alerts:
        if alert.severity == AlertSeverity.CRITICAL:
            logger.error(
                "DELIVERY_DIAGNOSTIC_ALERT rule=%s category=%s message=%s",
                alert.rule,
                alert.category.value,
                alert.message,
            )
        elif alert.severity == AlertSeverity.WARNING:
            logger.warning(
                "DELIVERY_DIAGNOSTIC_ALERT rule=%s category=%s message=%s",
                alert.rule,
                alert.category.value,
                alert.message,
            )
        else:
            logger.info(
                "DELIVERY_DIAGNOSTIC_ALERT rule=%s category=%s message=%s",
                alert.rule,
                alert.category.value,
                alert.message,
            )

    return report_delivery, website_metrics


def generate_report(
    client_slug: str,
    report_type: str | ReportType = ReportType.PERFORMANCE_28D,
    days: Optional[int] = None,
    send_email: bool = False,
    output_dir: Optional[Path] = None,
    explore_deep_insights: Optional[bool] = None,
    dry_run: bool = False,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    recipient_override: Optional[str] = None,
    test_send: bool = False,
    report_delivery_source: object | None = None,
    website_inquiry_source: object | None = None,
) -> FullGrowthBriefing:
    """Run the real-source growth reporting pipeline for one configured client."""
    if isinstance(report_type, str):
        rtype = ReportType.WEEKLY if report_type.lower() in ("weekly", "7d", "7") else ReportType.PERFORMANCE_28D
    else:
        rtype = report_type
    spec = REPORT_SPECS[rtype]
    period_days = days if days is not None else spec.default_days

    if test_send and not send_email:
        raise RuntimeError("--test-send requires --send.")
    if explore_deep_insights is True and rtype != ReportType.PERFORMANCE_28D:
        raise RuntimeError("Deep Insights is supported for performance reports only.")

    client = load_client_config_by_slug(client_slug)
    deep_insights_enabled = (
        bool(explore_deep_insights)
        if explore_deep_insights is not None
        else (
            rtype == ReportType.PERFORMANCE_28D
            and client.reporting.performance_report.deep_insights
        )
    )
    if rtype != ReportType.PERFORMANCE_28D:
        deep_insights_enabled = False

    if send_email:
        if recipient_override:
            raise RuntimeError("Recipient overrides are not permitted for production delivery.")
        if not is_production_dispatch_allowed(client.client_id):
            raise RuntimeError("Client is not enabled for production delivery.")
        if rtype == ReportType.WEEKLY and not client.reporting.weekly_digest.enabled:
            raise RuntimeError("Weekly delivery is disabled for this client.")
        if rtype == ReportType.PERFORMANCE_28D and not client.reporting.performance_report.enabled:
            raise RuntimeError("Performance delivery is disabled for this client.")

    print(f"[*] Loaded client configuration: {client.company_name} ({client.client_id})")
    print(f"[*] Report Type: {spec.display_name} ({period_days} requested days)")

    requested_start_date, end_date, requested_prior_start_date, requested_prior_end_date = calculate_date_ranges(
        days=period_days,
        timezone_str=client.timezone,
    )
    try:
        date_plan = select_report_window(
            requested_period_start=requested_start_date,
            requested_period_end=end_date,
            requested_comparison_start=requested_prior_start_date,
            requested_comparison_end=requested_prior_end_date,
            measurement_start_date=client.measurement_start_date,
        )
    except ReportWindowError as exc:
        raise RuntimeError(str(exc)) from exc

    observation_start_date = date_plan.observation_start
    baseline_mode = date_plan.mode == ReportMode.INITIAL_BASELINE
    period_label = (
        f"Weekly Period ({observation_start_date} to {end_date})"
        if rtype == ReportType.WEEKLY and not baseline_mode
        else (
            f"Initial Measurement Baseline (observed {observation_start_date} to {end_date}; "
            f"measurement begins {date_plan.measurement_start_date})"
            if baseline_mode
            else f"{period_days}-Day Period ({observation_start_date} to {end_date})"
        )
    )
    print(f"[*] Analysis window: {period_label}")
    if baseline_mode:
        print(
            f"[*] Comparison suppressed: {date_plan.comparison_suppression_reason} "
            f"Requested prior period was {requested_prior_start_date} to {requested_prior_end_date}."
        )

    print("[*] Ingestion: Connecting to live Google API endpoints...")
    ga4_ext = GA4Extractor(client.ga4_property_id)
    ga4_data = ga4_ext.fetch_metrics_and_channels(
        observation_start_date,
        end_date,
        None if baseline_mode else requested_prior_start_date,
        None if baseline_mode else requested_prior_end_date,
    )
    if baseline_mode:
        try:
            date_plan = select_report_window(
                requested_period_start=requested_start_date,
                requested_period_end=end_date,
                requested_comparison_start=requested_prior_start_date,
                requested_comparison_end=requested_prior_end_date,
                measurement_start_date=client.measurement_start_date,
                current_covered=coverage_from_ga4(ga4_data, "current"),
                comparison_covered=None,
            )
        except ReportWindowError as exc:
            if ga4_data.get("status") != SourceAvailability.AVAILABLE.value:
                raise RuntimeError(
                    f"GA4 source is {ga4_data.get('status', 'unknown')}; refusing to generate a report: "
                    f"{ga4_data.get('reason', 'no diagnostic available')}"
                ) from exc
            raise RuntimeError(str(exc)) from exc
    else:
        try:
            date_plan = select_report_window(
                requested_period_start=requested_start_date,
                requested_period_end=end_date,
                requested_comparison_start=requested_prior_start_date,
                requested_comparison_end=requested_prior_end_date,
                measurement_start_date=client.measurement_start_date,
                current_covered=coverage_from_ga4(ga4_data, "current"),
                comparison_covered=coverage_from_ga4(ga4_data, "prior"),
            )
        except ReportWindowError as exc:
            if ga4_data.get("status") != SourceAvailability.AVAILABLE.value:
                raise RuntimeError(
                    f"GA4 source is {ga4_data.get('status', 'unknown')}; refusing to generate a report: "
                    f"{ga4_data.get('reason', 'no diagnostic available')}"
                ) from exc
            raise RuntimeError(str(exc)) from exc
    if ga4_data.get("status") != SourceAvailability.AVAILABLE.value:
        raise RuntimeError(
            f"GA4 source is {ga4_data.get('status', 'unknown')}; refusing to generate a report: "
            f"{ga4_data.get('reason', 'no diagnostic available')}"
        )

    gsc_ext = SearchConsoleExtractor(client.gsc_site_url)
    if baseline_mode:
        gsc_current = gsc_ext.fetch_search_analytics(
            observation_start_date,
            end_date,
            strict=False,
        )
        gsc_prior = None
    else:
        gsc_current, gsc_prior = gsc_ext.fetch_comparative_search_analytics(
            observation_start_date,
            end_date,
            requested_prior_start_date,
            requested_prior_end_date,
            strict=False,
        )
    gsc_queries = gsc_current.get("rows", [])
    prior_gsc_queries = gsc_prior.get("rows", []) if gsc_prior else None

    gbp_kwargs = {"account_id": client.gbp_account_id}
    if client.gbp_public_place_id:
        gbp_kwargs["public_place_id"] = client.gbp_public_place_id
    if client.gbp_candidate_location_ids:
        gbp_kwargs["candidate_location_ids"] = client.gbp_candidate_location_ids
    if client.company_name:
        gbp_kwargs["business_title"] = client.company_name
    gbp_ext = GoogleBusinessProfileExtractor(client.gbp_location_id, **gbp_kwargs)
    gbp_data = gbp_ext.fetch_local_insights(observation_start_date, end_date, strict=False)
    gbp_prior = None
    if not baseline_mode:
        fetch_periodic = getattr(gbp_ext, "fetch_periodic_insights", None)
        if callable(fetch_periodic):
            gbp_prior = fetch_periodic(requested_prior_start_date, requested_prior_end_date)
        else:
            gbp_prior = {
                "status": SourceAvailability.UNAVAILABLE.value,
                "reason": "GBP connector does not expose a prior-period method.",
            }
        gbp_data.update({
            "prior_status": gbp_prior.get("status"),
            "prior_performance_metrics": gbp_prior.get("performance_metrics", {}) or {},
            "prior_monthly_search_keywords": gbp_prior.get("monthly_search_keywords", []) or [],
            "prior_answered_calls": gbp_prior.get("answered_calls"),
            "prior_missed_calls": gbp_prior.get("missed_calls"),
            "prior_phone_calls": gbp_prior.get("phone_calls"),
            "prior_direction_requests": gbp_prior.get("direction_requests"),
            "prior_website_clicks": gbp_prior.get("website_clicks"),
        })
        if isinstance(gbp_data.get("capabilities"), dict):
            gbp_data["capabilities"]["period_comparison"] = gbp_prior.get(
                "status", SourceAvailability.UNAVAILABLE.value
            )

    source_statuses = {
        "ga4": {
            "status": ga4_data.get("status"),
            "current_status": ga4_data.get("current_status"),
            "prior_status": ga4_data.get("prior_status"),
            "query_statuses": ga4_data.get("query_statuses", {}),
        },
        "gsc": {
            "status": _combined_search_status(gsc_current, gsc_prior),
            "current": gsc_current.get("status"),
            "prior": gsc_prior.get("status") if gsc_prior else "suppressed",
            "current_truncated": bool(gsc_current.get("truncated", False)),
            "prior_truncated": bool(gsc_prior.get("truncated", False)) if gsc_prior else False,
        },
        "gbp": {
            "status": gbp_data.get("status"),
            "current": gbp_data.get("status"),
            "prior": gbp_prior.get("status") if gbp_prior else "suppressed",
            "capabilities": gbp_data.get("capabilities", {}),
        },
    }
    source_diagnostics = {
        "ga4": ga4_data.get("reason") or ", ".join(ga4_data.get("errors", [])),
        "gsc": "; ".join(filter(None, [
            gsc_current.get("reason"),
            gsc_prior.get("reason") if gsc_prior else "Search Console comparison was suppressed for the initial measurement baseline.",
            "Prior Search Console response was truncated." if gsc_prior and gsc_prior.get("truncated") else None,
        ])),
        "gbp": "; ".join(filter(None, [
            gbp_data.get("reason", ""),
            *[str(item) for item in gbp_data.get("limitations", []) if item],
            gbp_prior.get("reason", "") if gbp_prior else (
                "GBP prior-period comparison was suppressed for the initial measurement baseline."
            ),
        ])),
    }

    report_delivery_metrics, website_inquiry_metrics = _collect_delivery_metrics(
        client=client,
        report_type=rtype,
        start_date=observation_start_date,
        end_date=end_date,
        report_delivery_source=report_delivery_source,
        website_inquiry_source=website_inquiry_source,
    )

    print("[*] Pre-processing & calculating metric deltas...")
    growth_input = aggregate_growth_metrics(
        client=client,
        start_date=observation_start_date,
        end_date=end_date,
        prior_start_date=requested_prior_start_date,
        prior_end_date=requested_prior_end_date,
        ga4_data=ga4_data,
        gsc_queries=gsc_queries,
        gbp_data=gbp_data,
        report_type=rtype,
        period_days=period_days,
        prior_gsc_queries=prior_gsc_queries,
        source_statuses=source_statuses,
        source_diagnostics=source_diagnostics,
        prior_gsc_status=gsc_prior.get("status") if gsc_prior else None,
        prior_gsc_truncated=bool(gsc_prior.get("truncated", False)) if gsc_prior else False,
        report_mode=date_plan.mode,
        measurement_start_date=date_plan.measurement_start_date,
        requested_period_start=date_plan.requested_period_start,
        requested_period_end=date_plan.requested_period_end,
        requested_comparison_start=date_plan.requested_comparison_start,
        requested_comparison_end=date_plan.requested_comparison_end,
        comparison_suppressed=date_plan.comparison_suppressed,
        comparison_suppression_reason=date_plan.comparison_suppression_reason,
        website_inquiry_metrics=website_inquiry_metrics,
    )

    print(f"[*] Running AI Growth Analyst synthesis ({rtype.value})...")
    analyst = GrowthAnalyst(model=model, reasoning_effort=reasoning_effort)
    weekly_insights = None
    if rtype == ReportType.WEEKLY:
        weekly_insights = analyst.analyze_weekly(growth_input)
        insights = analyst.analyze(growth_input)
    else:
        insights = analyst.analyze(growth_input)

    exploration_audit = None
    if deep_insights_enabled:
        print("[*] Launching evidence-backed multi-source exploration agent...")
        toolkit = MultiSourceAnalyticsToolkit(
            client=client,
            start_date=observation_start_date,
            end_date=end_date,
            prior_start_date=requested_prior_start_date,
            prior_end_date=requested_prior_end_date,
            report_mode=date_plan.mode,
            measurement_start_date=date_plan.measurement_start_date,
            requested_period_start=date_plan.requested_period_start,
            requested_period_end=date_plan.requested_period_end,
            requested_comparison_start=date_plan.requested_comparison_start,
            requested_comparison_end=date_plan.requested_comparison_end,
            comparison_suppression_reason=date_plan.comparison_suppression_reason,
            expected_totals=ga4_data.get("summary", {}),
            ga4_extractor=ga4_ext,
            gsc_extractor=gsc_ext,
            gbp_extractor=gbp_ext,
        )
        agent = ExploratoryGrowthAgent(model=model, reasoning_effort=reasoning_effort)
        exploration = agent.explore(client, growth_input, toolkit)
        exploration_audit = exploration.audit
        insights.deep_discoveries = exploration.discoveries
        print(f"[+] Evidence-backed exploration completed with {len(exploration.discoveries)} accepted discoveries ({exploration.audit.status}).")

    now_str = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    briefing = FullGrowthBriefing(
        client_id=client.client_id,
        company_name=client.company_name,
        domain=client.domain,
        industry=client.industry,
        generated_at=now_str,
        period_label=period_label,
        report_type=rtype,
        report_mode=date_plan.mode,
        measurement_start_date=date_plan.measurement_start_date,
        observation_window_start=observation_start_date,
        observation_window_end=end_date,
        requested_period_start=date_plan.requested_period_start,
        requested_period_end=date_plan.requested_period_end,
        requested_comparison_start=date_plan.requested_comparison_start,
        requested_comparison_end=date_plan.requested_comparison_end,
        comparison_suppressed=date_plan.comparison_suppressed,
        comparison_suppression_reason=date_plan.comparison_suppression_reason,
        branding=client.branding.model_dump(),
        analytics=growth_input,
        insights=insights,
        weekly_insights=weekly_insights,
        exploration_audit=exploration_audit,
        report_delivery_metrics=report_delivery_metrics,
    )

    if rtype == ReportType.WEEKLY:
        html_content = render_weekly_digest_html(briefing)
        pdf_bytes = None
    else:
        html_content = render_growth_email_html(briefing)
        pdf_bytes = build_executive_pdf(briefing)

    out_dir = output_dir or (Path(__file__).resolve().parents[1] / "output")
    if date_plan.mode == ReportMode.INITIAL_BASELINE and rtype == ReportType.WEEKLY:
        filename_report_kind = "weekly_initial_baseline"
    elif date_plan.mode == ReportMode.INITIAL_BASELINE:
        filename_report_kind = "initial_baseline"
    else:
        filename_report_kind = rtype.value
    base_filename = f"{client.client_id}_{filename_report_kind}_{observation_start_date}_{end_date}"
    html_file = out_dir / f"{base_filename}.html"
    pdf_file = out_dir / f"{base_filename}.pdf" if pdf_bytes else None
    report_html_alias = out_dir / f"{client.client_id}_{rtype.value}_briefing.html"
    report_pdf_alias = out_dir / f"{client.client_id}_{rtype.value}_growth_report.pdf" if pdf_bytes else None
    legacy_html_file = out_dir / f"{client.client_id}_briefing.html" if rtype == ReportType.PERFORMANCE_28D else None
    legacy_pdf_file = out_dir / f"{client.client_id}_growth_report.pdf" if pdf_bytes else None
    audit_file = out_dir / f"{base_filename}_deep_insights_audit.json" if exploration_audit else None
    audit_html_file = out_dir / f"{base_filename}_deep_insights_audit.html" if exploration_audit else None
    audit_html = render_exploration_audit_html(exploration_audit) if exploration_audit else None

    if dry_run:
        print("[*] Dry-run enabled: Artifacts rendered in memory; file writes and email dispatch skipped.")
        return briefing

    out_dir.mkdir(parents=True, exist_ok=True)
    html_file.write_text(html_content, encoding="utf-8")
    report_html_alias.write_text(html_content, encoding="utf-8")
    if legacy_html_file:
        legacy_html_file.write_text(html_content, encoding="utf-8")
    print(f"[+] Rendered HTML brief: {html_file}")
    if pdf_bytes and pdf_file:
        pdf_file.write_bytes(pdf_bytes)
        report_pdf_alias.write_bytes(pdf_bytes)
        if legacy_pdf_file:
            legacy_pdf_file.write_bytes(pdf_bytes)
        print(f"[+] Generated Executive PDF: {pdf_file} ({len(pdf_bytes):,} bytes)")
    if audit_file and audit_html_file and audit_html is not None and exploration_audit is not None:
        audit_file.write_text(exploration_audit.model_dump_json(indent=2), encoding="utf-8")
        audit_html_file.write_text(audit_html, encoding="utf-8")
        print(f"[+] Saved Deep Insights audit: {audit_file}")

    target_recipient = recipient_override or client.recipients.get("client")
    passed_qa, qa_issues = validate_pre_send_qa(
        briefing=briefing,
        client=client,
        pdf_bytes=pdf_bytes,
        recipient=target_recipient if send_email else None,
    )
    if not passed_qa:
        message = f"Pre-send QA Gate Failed with {len(qa_issues)} issues: {qa_issues}"
        if send_email:
            raise RuntimeError(message)
        print(f"[!] Warning: {message}")

    if send_email:
        client_email = target_recipient
        if not client_email:
            raise RuntimeError("No client recipient is configured; refusing to skip delivery.")
        sender = ResendEmailSender()
        if not sender.is_configured:
            raise RuntimeError("Resend delivery is not configured; refusing to send.")

        if deep_insights_enabled:
            if exploration_audit and (
                exploration_audit.status in VERIFIER_FAILURE_STATUSES
                or exploration_audit.verifier_status in VERIFIER_FAILURE_STATUSES
            ):
                raise RuntimeError(
                    "Deep Insights verifier was unavailable or failed; local deterministic artifacts may be inspected, "
                    "but email delivery is blocked."
                )
            audit_recipient = client.recipients.get("agency_audit")
            if not audit_recipient or not is_valid_email(audit_recipient):
                raise RuntimeError("Deep Insights is enabled but no valid agency_audit recipient is configured.")
            if not audit_html or not exploration_audit:
                raise RuntimeError("Deep Insights audit could not be prepared; refusing to send the client report.")
            audit_raw = f"{client.client_id}:deep-insights-audit:{rtype.value}:{date_plan.mode.value}:{observation_start_date}:{end_date}"
            if test_send:
                audit_raw += f":test:{uuid.uuid4()}"
            audit_key = hashlib.sha256(audit_raw.encode("utf-8")).hexdigest()
            audit_dispatch = sender.send_briefing(
                to_recipients=[audit_recipient],
                subject=f"{client.company_name} | Deep Insights Audit | {observation_start_date} to {end_date}",
                html_content=audit_html,
                idempotency_key=audit_key,
            )
            if audit_dispatch.get("status") != "sent":
                raise RuntimeError("Deep Insights audit email was not accepted; refusing to send the client report.")
            print(f"[+] Deep Insights audit dispatch status: {audit_dispatch.get('status')}")
            _persist_dispatch_provenance(
                dispatch=audit_dispatch,
                client=client,
                report_type="deep_insights_audit",
                observation_start_date=observation_start_date,
                observation_end_date=end_date,
                delivery_kind="deep_insights_audit",
                has_attachment=False,
                recipient_role="agency_audit",
                cloud_run_revision=os.getenv("K_REVISION", ""),
                idempotency_key=audit_key,
            )

        agency_cc = client.recipients.get("agency_cc")
        subject = (
            f"{client.company_name} | Weekly Growth Digest | {observation_start_date} to {end_date}"
            if rtype == ReportType.WEEKLY
            else (
                f"{client.company_name} | Initial Measurement Baseline | {observation_start_date} to {end_date}"
                if date_plan.mode == ReportMode.INITIAL_BASELINE
                else f"{client.company_name} | {period_days}-Day Performance Report | {observation_start_date} to {end_date}"
            )
        )
        idempotency_raw = f"{client.client_id}:{rtype.value}:{date_plan.mode.value}:{observation_start_date}:{end_date}"
        if test_send:
            idempotency_raw += f":test:{uuid.uuid4()}"
            print("[!] Test send enabled: using a fresh idempotency key for this deliberate re-send.")
        idempotency_key = hashlib.sha256(idempotency_raw.encode("utf-8")).hexdigest()
        dispatch = sender.send_briefing(
            to_recipients=[client_email],
            subject=subject,
            html_content=html_content,
            pdf_attachment=pdf_bytes,
            pdf_filename=f"{base_filename}.pdf" if pdf_bytes else "Report.pdf",
            cc_recipients=[agency_cc] if agency_cc else [],
            idempotency_key=idempotency_key,
        )
        print(f"[+] Dispatch status: {dispatch.get('status')}")
        _persist_dispatch_provenance(
            dispatch=dispatch,
            client=client,
            report_type=rtype.value,
            observation_start_date=observation_start_date,
            observation_end_date=end_date,
            delivery_kind="client_report",
            has_attachment=pdf_bytes is not None,
            recipient_role="client",
            cloud_run_revision=os.getenv("K_REVISION", ""),
            idempotency_key=idempotency_key,
        )

    return briefing


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate client performance reports and digests")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    subparsers.add_parser("list-clients", help="List all configured client slugs")
    subparsers.add_parser("validate-configs", help="Validate all client JSON configurations")

    gen_parser = subparsers.add_parser("generate", help="Generate a real-source growth report for a client")
    gen_parser.add_argument("--client", "-c", required=True, help="Client ID slug")
    gen_parser.add_argument("--report", "-r", choices=["weekly", "performance"], default="performance")
    gen_parser.add_argument("--days", "-d", type=int, default=None, help="Period days override")
    gen_parser.add_argument("--send", "-s", action="store_true", help="Send the report email via Resend")
    gen_parser.add_argument("--test-send", action="store_true", help="Deliberately resend with a fresh idempotency key")
    gen_parser.add_argument("--to", type=str, default=None, help="Recipient override for local QA only")
    explore_group = gen_parser.add_mutually_exclusive_group()
    explore_group.add_argument("--explore", "--deep-insights", dest="explore", action="store_true", help="Enable Deep Insights for this performance run")
    explore_group.add_argument("--no-explore", dest="explore", action="store_false", help="Disable Deep Insights for this run")
    gen_parser.set_defaults(explore=None)
    gen_parser.add_argument("--output", "-o", type=Path, default=None, help="Output directory for generated artifacts")
    gen_parser.add_argument("--dry-run", action="store_true", help="Preview output without saving files or sending emails")
    gen_parser.add_argument("--model", type=str, default=None, help="Override LLM model name")
    gen_parser.add_argument("--reasoning-effort", type=str, default=None, help="Override reasoning effort")

    args = parser.parse_args()
    if args.command == "list-clients":
        print("Available client configurations:")
        for client in list_available_clients():
            print(f"  - {client}")
        return
    if args.command == "validate-configs":
        clients = list_available_clients()
        print(f"[*] Validating {len(clients)} client configurations...")
        errors = 0
        for slug in clients:
            try:
                config = load_client_config(slug)
                print(f"  [✓] {slug}: {config.company_name} ({config.domain}) - Industry: {config.industry}")
            except Exception as exc:
                print(f"  [✗] {slug}: INVALID -> {exc}", file=sys.stderr)
                errors += 1
        if errors:
            print(f"\n[!] Config validation failed with {errors} errors.", file=sys.stderr)
            raise SystemExit(1)
        print(f"\n[✓] All {len(clients)} client configurations valid.")
        return
    if args.command == "generate":
        try:
            generate_report(
                client_slug=args.client,
                report_type=args.report,
                days=args.days,
                send_email=args.send,
                output_dir=args.output,
                explore_deep_insights=args.explore,
                dry_run=args.dry_run,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                recipient_override=args.to,
                test_send=args.test_send,
            )
            print("\n[✓] Growth briefing pipeline completed successfully.")
        except Exception as exc:
            print(f"\n[!] Error generating growth report: {exc}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            raise SystemExit(1)
        return
    parser.print_help()
    raise SystemExit(1)


if __name__ == "__main__":
    main()
