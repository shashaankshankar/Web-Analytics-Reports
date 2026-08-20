from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.ai.agent import ExploratoryGrowthAgent
from app.ai.analyst import GrowthAnalyst
from app.ai.tools import MultiSourceAnalyticsToolkit
from app.analytics.contracts import FullGrowthBriefing, REPORT_SPECS, ReportType
from app.analytics.metrics import aggregate_growth_metrics, calculate_date_ranges
from app.config import ClientConfig, list_available_clients, load_client_config
from app.delivery.email_template import render_growth_email_html
from app.delivery.pdf_builder import build_executive_pdf
from app.delivery.sender import ResendEmailSender
from app.delivery.weekly_digest_template import render_weekly_digest_html
from app.sources.ga4 import GA4Extractor
from app.sources.gbp import GoogleBusinessProfileExtractor
from app.sources.gsc import SearchConsoleExtractor

def validate_pre_send_qa(
    briefing: FullGrowthBriefing,
    client: ClientConfig,
    pdf_bytes: Optional[bytes] = None,
    recipient: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """Execute deterministic pre-send QA validation checks."""
    issues: list[str] = []

    # 1. Date and period sanity
    if not briefing.analytics.period_start or not briefing.analytics.period_end:
        issues.append("Missing reporting period start or end dates.")
    if briefing.analytics.period_start >= briefing.analytics.period_end:
        issues.append("Period start date must be before period end date.")

    # 2. Metric validity
    for m in briefing.analytics.core_metrics:
        if m.current_value < 0 or m.prior_value < 0:
            issues.append(f"Negative metric value encountered in {m.metric_name}.")
        if m.metric_name == "conversion_rate" and (m.current_value > 100.0 or m.current_value < 0.0):
            issues.append(f"Conversion rate {m.current_value}% out of valid [0, 100] bound.")

    # 3. Content checks
    if not briefing.company_name or briefing.company_name.strip() == "":
        issues.append("Company name is empty.")

    # 4. Delivery & attachment checks
    spec = REPORT_SPECS.get(briefing.report_type)
    if spec and spec.requires_pdf and (pdf_bytes is None or len(pdf_bytes) < 100):
        issues.append("PDF attachment required for report type but missing or empty.")

    if recipient and "@" not in recipient:
        issues.append(f"Invalid recipient email format: {recipient}")

    return (len(issues) == 0, issues)

def generate_report(
    client_slug: str,
    report_type: str | ReportType = ReportType.PERFORMANCE_28D,
    days: Optional[int] = None,
    send_email: bool = False,
    output_dir: Optional[Path] = None,
    mock_data: bool = False,
    explore_deep_insights: bool = False,
    dry_run: bool = False,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    recipient_override: Optional[str] = None,
) -> FullGrowthBriefing:
    """Run complete growth intelligence pipeline for a specific client."""
    if isinstance(report_type, str):
        rtype = ReportType.WEEKLY if report_type.lower() in ("weekly", "7d", "7") else ReportType.PERFORMANCE_28D
    else:
        rtype = report_type

    spec = REPORT_SPECS[rtype]
    period_days = days if days is not None else spec.default_days

    client = load_client_config(client_slug)
    print(f"[*] Loaded client configuration: {client.company_name} ({client.client_id})")
    print(f"[*] Report Type: {spec.display_name} ({period_days} days)")

    # 1. Date calculation
    start_date, end_date, prior_start_date, prior_end_date = calculate_date_ranges(
        days=period_days,
        timezone_str=client.timezone,
    )
    if rtype == ReportType.WEEKLY:
        period_label = f"Weekly Period ({start_date} to {end_date})"
    else:
        period_label = f"{period_days}-Day Period ({start_date} to {end_date})"
    print(f"[*] Analysis window: {period_label} vs prior ({prior_start_date} to {prior_end_date})")

    # 2. Data Ingestion
    if mock_data or client.ga4_property_id in ("", "mock", "123456789", "987654321"):
        print("[*] Ingestion: Utilizing deterministic synthetic metrics for evaluation/testing...")
        if rtype == ReportType.WEEKLY:
            ga4_data = {
                "summary": {
                    "activeUsers": 380,
                    "sessions": 460,
                    "engagementRate": 0.655,
                    "bounceRate": 0.345,
                    "conversions": 14,
                },
                "prior_summary": {
                    "activeUsers": 340,
                    "sessions": 410,
                    "engagementRate": 0.612,
                    "bounceRate": 0.388,
                    "conversions": 11,
                },
                "channels": [
                    {"channel": "Organic Search", "sessions": 240, "activeUsers": 190, "conversions": 8, "priorSessions": 200, "sessionChange": 40},
                    {"channel": "Direct", "sessions": 120, "activeUsers": 100, "conversions": 4, "priorSessions": 110, "sessionChange": 10},
                    {"channel": "Organic Social", "sessions": 60, "activeUsers": 50, "conversions": 1, "priorSessions": 55, "sessionChange": 5},
                    {"channel": "Referral", "sessions": 40, "activeUsers": 35, "conversions": 1, "priorSessions": 45, "sessionChange": -5},
                ],
                "pages": [
                    {"pagePath": "/", "sessions": 210, "activeUsers": 170, "priorSessions": 180, "sessionChange": 30},
                    {"pagePath": "/services", "sessions": 110, "activeUsers": 85, "priorSessions": 90, "sessionChange": 20},
                    {"pagePath": "/contact", "sessions": 70, "activeUsers": 60, "priorSessions": 55, "sessionChange": 15},
                    {"pagePath": "/pricing", "sessions": 50, "activeUsers": 40, "priorSessions": 45, "sessionChange": 5},
                ],
                "events": {"generate_lead": 9, "phone_click": 5},
                "prior_events": {"generate_lead": 7, "phone_click": 4},
            }
            gsc_queries = [
                {"query": f"{client.company_name.lower()} reviews", "clicks": 22, "impressions": 110, "ctr": 0.20, "position": 2.0},
                {"query": f"best {client.industry.replace('_', ' ')} near me", "clicks": 8, "impressions": 380, "ctr": 0.021, "position": 10.8},
                {"query": "consultation booking online", "clicks": 4, "impressions": 160, "ctr": 0.025, "position": 14.1},
            ]
            prior_gsc_queries = [
                {"query": f"{client.company_name.lower()} reviews", "clicks": 18, "impressions": 95, "ctr": 0.189, "position": 2.2},
                {"query": f"best {client.industry.replace('_', ' ')} near me", "clicks": 5, "impressions": 310, "ctr": 0.016, "position": 12.5},
            ]
            gbp_data = {
                "phone_calls": 11,
                "prior_phone_calls": 9,
                "direction_requests": 18,
                "prior_direction_requests": 14,
                "website_clicks": 30,
                "prior_website_clicks": 26,
                "average_rating": 4.9,
                "total_reviews_count": 95,
                "recent_review_snippets": ["Outstanding weekly care!", "Smooth visit."],
            }
        else:
            ga4_data = {
                "summary": {
                    "activeUsers": 1420,
                    "sessions": 1850,
                    "engagementRate": 0.642,
                    "bounceRate": 0.358,
                    "conversions": 48,
                },
                "prior_summary": {
                    "activeUsers": 1210,
                    "sessions": 1590,
                    "engagementRate": 0.589,
                    "bounceRate": 0.411,
                    "conversions": 39,
                },
                "channels": [
                    {"channel": "Organic Search", "sessions": 920, "activeUsers": 710, "conversions": 26, "priorSessions": 780, "sessionChange": 140},
                    {"channel": "Direct", "sessions": 450, "activeUsers": 380, "conversions": 12, "priorSessions": 420, "sessionChange": 30},
                    {"channel": "Organic Social", "sessions": 280, "activeUsers": 210, "conversions": 6, "priorSessions": 230, "sessionChange": 50},
                    {"channel": "Referral", "sessions": 200, "activeUsers": 160, "conversions": 4, "priorSessions": 160, "sessionChange": 40},
                ],
                "pages": [
                    {"pagePath": "/", "sessions": 850, "activeUsers": 640, "priorSessions": 720, "sessionChange": 130},
                    {"pagePath": "/services", "sessions": 410, "activeUsers": 310, "priorSessions": 340, "sessionChange": 70},
                    {"pagePath": "/contact", "sessions": 260, "activeUsers": 220, "priorSessions": 210, "sessionChange": 50},
                    {"pagePath": "/pricing", "sessions": 190, "activeUsers": 150, "priorSessions": 180, "sessionChange": 10},
                ],
                "events": {"generate_lead": 32, "phone_click": 16},
                "prior_events": {"generate_lead": 25, "phone_click": 14},
            }
            gsc_queries = [
                {"query": f"{client.company_name.lower()} reviews", "clicks": 85, "impressions": 420, "ctr": 0.202, "position": 2.1},
                {"query": f"best {client.industry.replace('_', ' ')} near me", "clicks": 28, "impressions": 1450, "ctr": 0.019, "position": 11.4},
                {"query": f"emergency {client.industry.replace('_', ' ')} cost", "clicks": 14, "impressions": 980, "ctr": 0.014, "position": 14.2},
                {"query": f"top rated {client.industry.replace('_', ' ')} ", "clicks": 19, "impressions": 1120, "ctr": 0.017, "position": 9.8},
                {"query": "consultation booking online", "clicks": 12, "impressions": 650, "ctr": 0.018, "position": 16.5},
            ]
            prior_gsc_queries = [
                {"query": f"{client.company_name.lower()} reviews", "clicks": 70, "impressions": 390, "ctr": 0.179, "position": 2.3},
                {"query": f"best {client.industry.replace('_', ' ')} near me", "clicks": 19, "impressions": 1200, "ctr": 0.015, "position": 13.6},
                {"query": f"emergency {client.industry.replace('_', ' ')} cost", "clicks": 12, "impressions": 900, "ctr": 0.013, "position": 14.5},
            ]
            gbp_data = {
                "phone_calls": 42,
                "prior_phone_calls": 35,
                "direction_requests": 68,
                "prior_direction_requests": 54,
                "website_clicks": 112,
                "prior_website_clicks": 98,
                "average_rating": 4.9,
                "total_reviews_count": 94,
                "recent_review_snippets": ["Outstanding experience!", "Very responsive team."],
            }
    else:
        print("[*] Ingestion: Connecting to live Google API endpoints...")
        ga4_ext = GA4Extractor(client.ga4_property_id)
        ga4_data = ga4_ext.fetch_metrics_and_channels(start_date, end_date, prior_start_date, prior_end_date)

        gsc_ext = SearchConsoleExtractor(client.gsc_site_url)
        gsc_queries, prior_gsc_queries = gsc_ext.fetch_comparative_search_analytics(start_date, end_date, prior_start_date, prior_end_date)

        gbp_ext = GoogleBusinessProfileExtractor(client.gbp_location_id)
        gbp_data = gbp_ext.fetch_local_insights(start_date, end_date)

    # 3. Deterministic Aggregation
    print("[*] Pre-processing & calculating metric deltas...")
    growth_input = aggregate_growth_metrics(
        client=client,
        start_date=start_date,
        end_date=end_date,
        prior_start_date=prior_start_date,
        prior_end_date=prior_end_date,
        ga4_data=ga4_data,
        gsc_queries=gsc_queries,
        gbp_data=gbp_data,
        report_type=rtype,
        period_days=period_days,
        prior_gsc_queries=prior_gsc_queries,
    )

    # 4. AI Growth Analyst Synthesis
    print(f"[*] Running AI Growth Analyst synthesis ({rtype.value})...")
    analyst = GrowthAnalyst(api_key="" if mock_data else None, model=model, reasoning_effort=reasoning_effort)

    weekly_insights = None
    if rtype == ReportType.WEEKLY:
        weekly_insights = analyst.analyze_weekly(growth_input)
        # Populate basic briefing model insights for compatibility
        insights = analyst.analyze(growth_input)
    else:
        insights = analyst.analyze(growth_input)

    # 4b. Optional Exploratory Deep Discoveries
    if explore_deep_insights and rtype == ReportType.PERFORMANCE_28D:
        print("[*] Launching Autonomous Multi-Source Exploration Agent...")
        toolkit = MultiSourceAnalyticsToolkit(
            client=client,
            start_date=start_date,
            end_date=end_date,
            prior_start_date=prior_start_date,
            prior_end_date=prior_end_date,
            ga4_extractor=ga4_ext if 'ga4_ext' in locals() else None,
            gsc_extractor=gsc_ext if 'gsc_ext' in locals() else None,
            gbp_extractor=gbp_ext if 'gbp_ext' in locals() else None,
            mock_data=mock_data or client.ga4_property_id in ('', 'mock', '123456789', '987654321'),
        )
        agent = ExploratoryGrowthAgent(api_key="" if mock_data else None, model=model, reasoning_effort=reasoning_effort)
        discoveries = agent.explore(client, growth_input, toolkit)
        insights.deep_discoveries = discoveries
        print(f"[+] Autonomous agent completed with {len(discoveries)} deep discoveries.")

    # 5. Build Full Briefing Model
    now_str = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    briefing = FullGrowthBriefing(
        client_id=client.client_id,
        company_name=client.company_name,
        domain=client.domain,
        industry=client.industry,
        generated_at=now_str,
        period_label=period_label,
        report_type=rtype,
        branding=client.branding.model_dump(),
        analytics=growth_input,
        insights=insights,
        weekly_insights=weekly_insights,
    )

    # 6. Render Artifacts
    if rtype == ReportType.WEEKLY:
        html_content = render_weekly_digest_html(briefing)
        pdf_bytes = None
    else:
        html_content = render_growth_email_html(briefing)
        pdf_bytes = build_executive_pdf(briefing)

    out_dir = output_dir or (Path(__file__).resolve().parents[1] / "output")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Specific Period-Safe Filenames
    base_filename = f"{client.client_id}_{rtype.value}_{start_date}_{end_date}"
    html_file = out_dir / f"{base_filename}.html"
    pdf_file = out_dir / f"{base_filename}.pdf" if pdf_bytes else None

    # Legacy naming support for backward compatibility with existing tests/tooling
    legacy_html_file = out_dir / f"{client.client_id}_briefing.html"
    legacy_pdf_file = out_dir / f"{client.client_id}_growth_report.pdf"

    if dry_run:
        print("[*] Dry-run enabled: Artifacts rendered in memory; file writes and email dispatch skipped.")
        return briefing

    html_file.write_text(html_content, encoding="utf-8")
    legacy_html_file.write_text(html_content, encoding="utf-8")
    print(f"[+] Rendered HTML brief: {html_file}")

    if pdf_bytes and pdf_file:
        pdf_file.write_bytes(pdf_bytes)
        legacy_pdf_file.write_bytes(pdf_bytes)
        print(f"[+] Generated Executive PDF: {pdf_file} ({len(pdf_bytes):,} bytes)")

    # 7. QA Validation Gate
    target_recipient = recipient_override or client.recipients.get("client")
    passed_qa, qa_issues = validate_pre_send_qa(
        briefing=briefing,
        client=client,
        pdf_bytes=pdf_bytes,
        recipient=target_recipient if send_email else None,
    )
    if not passed_qa:
        err_msg = f"Pre-send QA Gate Failed with {len(qa_issues)} issues: {qa_issues}"
        if send_email:
            raise RuntimeError(err_msg)
        else:
            print(f"[!] Warning: {err_msg}")

    # 8. Optional Email Dispatch
    if send_email:
        client_email = target_recipient
        agency_cc = client.recipients.get("agency_cc")
        if not client_email:
            print("[!] Warning: No client email configured; skipping dispatch.")
        else:
            print(f"[*] Dispatching briefing to {client_email} (CC: {agency_cc or 'None'})...")
            sender = ResendEmailSender()
            to_list = [client_email]
            cc_list = [agency_cc] if agency_cc else []
            if rtype == ReportType.WEEKLY:
                subject = f"{client.company_name} | Weekly Growth Digest | {start_date} to {end_date}"
            else:
                subject = f"{client.company_name} | {period_days}-Day Performance Report | {start_date} to {end_date}"

            # Idempotency key to prevent double dispatch
            idempotency_raw = f"{client.client_id}:{rtype.value}:{start_date}:{end_date}"
            idempotency_key = hashlib.sha256(idempotency_raw.encode("utf-8")).hexdigest()

            res = sender.send_briefing(
                to_recipients=to_list,
                subject=subject,
                html_content=html_content,
                pdf_attachment=pdf_bytes,
                pdf_filename=f"{base_filename}.pdf" if pdf_bytes else "Report.pdf",
                cc_recipients=cc_list,
                idempotency_key=idempotency_key,
            )
            print(f"[+] Dispatch status: {res.get('status')} (ID: {res.get('id', 'simulated')})")

    return briefing

def main():
    parser = argparse.ArgumentParser(description="Generate client performance reports and digests")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("list-clients", help="List all configured client slugs")
    subparsers.add_parser("validate-configs", help="Validate all client JSON configurations")

    gen_parser = subparsers.add_parser("generate", help="Generate growth briefing report for a client")
    gen_parser.add_argument("--client", "-c", required=True, help="Client ID slug (e.g. example-dental)")
    gen_parser.add_argument("--report", "-r", choices=["weekly", "performance"], default="performance", help="Report cadence variant (weekly=7d, performance=28d)")
    gen_parser.add_argument("--days", "-d", type=int, default=None, help="Period days override")
    gen_parser.add_argument("--send", "-s", action="store_true", help="Send the report email via Resend")
    gen_parser.add_argument("--to", type=str, default=None, help="Recipient override for QA testing")
    gen_parser.add_argument("--mock", "-m", action="store_true", help="Use mock/synthetic analytics data")
    gen_parser.add_argument("--explore", "--deep-insights", dest="explore", action="store_true", help="Run exploratory multi-source analysis agent")
    gen_parser.add_argument("--output", "-o", type=Path, default=None, help="Output directory for generated artifacts")
    gen_parser.add_argument("--dry-run", action="store_true", help="Preview output without saving files or sending emails")
    gen_parser.add_argument("--model", type=str, default=None, help="Override LLM model name")
    gen_parser.add_argument("--reasoning-effort", type=str, default=None, help="Override reasoning effort")

    args = parser.parse_args()

    if args.command == "list-clients":
        clients = list_available_clients()
        print("Available client configurations:")
        for c in clients:
            print(f"  - {c}")
        sys.exit(0)

    elif args.command == "validate-configs":
        clients = list_available_clients()
        print(f"[*] Validating {len(clients)} client configurations...")
        errors = 0
        for slug in clients:
            try:
                cfg = load_client_config(slug)
                print(f"  [✓] {slug}: {cfg.company_name} ({cfg.domain}) - Industry: {cfg.industry}")
            except Exception as e:
                print(f"  [✗] {slug}: INVALID -> {e}", file=sys.stderr)
                errors += 1
        if errors > 0:
            print(f"\n[!] Config validation failed with {errors} errors.", file=sys.stderr)
            sys.exit(1)
        print(f"\n[✓] All {len(clients)} client configurations valid.")
        sys.exit(0)

    elif args.command == "generate":
        try:
            generate_report(
                client_slug=args.client,
                report_type=args.report,
                days=args.days,
                send_email=args.send,
                output_dir=args.output,
                mock_data=args.mock,
                explore_deep_insights=args.explore,
                dry_run=args.dry_run,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                recipient_override=args.to,
            )
            print("\n[✓] Growth briefing pipeline completed successfully.")
        except Exception as e:
            print(f"\n[!] Error generating growth report: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
