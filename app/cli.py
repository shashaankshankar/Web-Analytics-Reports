from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.ai.analyst import GrowthAnalyst
from app.analytics.contracts import FullGrowthBriefing
from app.analytics.metrics import aggregate_growth_metrics, calculate_date_ranges
from app.config import ClientConfig, list_available_clients, load_client_config
from app.delivery.email_template import render_growth_email_html
from app.delivery.pdf_builder import build_executive_pdf
from app.delivery.sender import ResendEmailSender
from app.sources.ga4 import GA4Extractor
from app.sources.gbp import GoogleBusinessProfileExtractor
from app.sources.gsc import SearchConsoleExtractor


def generate_report(
    client_slug: str,
    days: int = 28,
    send_email: bool = False,
    output_dir: Optional[Path] = None,
    mock_data: bool = False,
) -> FullGrowthBriefing:
    """Run complete growth intelligence pipeline for a specific client."""
    client = load_client_config(client_slug)
    print(f"[*] Loaded client configuration: {client.company_name} ({client.client_id})")

    # 1. Date calculation
    start_date, end_date, prior_start_date, prior_end_date = calculate_date_ranges(
        days=days,
        timezone_str=client.timezone,
    )
    period_label = f"Last {days} Days ({start_date} to {end_date})"
    print(f"[*] Analysis window: {period_label} vs prior ({prior_start_date} to {prior_end_date})")

    # 2. Data Ingestion
    if mock_data or client.ga4_property_id in ("", "mock", "123456789", "987654321"):
        print("[*] Ingestion: Utilizing deterministic synthetic metrics for evaluation/testing...")
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
            {"query": f"top rated {client.industry.replace('_', ' ')}", "clicks": 19, "impressions": 1120, "ctr": 0.017, "position": 9.8},
            {"query": "consultation booking online", "clicks": 12, "impressions": 650, "ctr": 0.018, "position": 16.5},
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
        gsc_queries = gsc_ext.fetch_search_analytics(start_date, end_date)

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
    )

    # 4. AI Growth Analyst Synthesis
    print("[*] Running AI Growth Analyst synthesis...")
    analyst = GrowthAnalyst()
    insights = analyst.analyze(growth_input)

    # 5. Build Full Briefing Model
    now_str = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    briefing = FullGrowthBriefing(
        client_id=client.client_id,
        company_name=client.company_name,
        domain=client.domain,
        industry=client.industry,
        generated_at=now_str,
        period_label=period_label,
        branding=client.branding.model_dump(),
        analytics=growth_input,
        insights=insights,
    )

    # 6. Render Artifacts
    out_dir = output_dir or (Path(__file__).resolve().parents[1] / "output")
    out_dir.mkdir(parents=True, exist_ok=True)

    html_content = render_growth_email_html(briefing)
    html_file = out_dir / f"{client.client_id}_briefing.html"
    html_file.write_text(html_content, encoding="utf-8")
    print(f"[+] Rendered HTML email brief: {html_file}")

    pdf_bytes = build_executive_pdf(briefing)
    pdf_file = out_dir / f"{client.client_id}_growth_report.pdf"
    pdf_file.write_bytes(pdf_bytes)
    print(f"[+] Generated Executive PDF: {pdf_file} ({len(pdf_bytes):,} bytes)")

    # 7. Optional Email Dispatch
    if send_email:
        client_email = client.recipients.get("client")
        agency_cc = client.recipients.get("agency_cc")
        if not client_email:
            print("[!] Warning: No client email configured in recipients; skipping dispatch.")
        else:
            print(f"[*] Dispatching briefing to {client_email} (CC: {agency_cc or 'None'})...")
            sender = ResendEmailSender()
            to_list = [client_email]
            cc_list = [agency_cc] if agency_cc else []
            subject = f"Monthly Growth & Local SEO Briefing - {client.company_name}"
            res = sender.send_briefing(
                to_recipients=to_list,
                subject=subject,
                html_content=html_content,
                pdf_attachment=pdf_bytes,
                pdf_filename=f"{client.client_id}_growth_report.pdf",
                cc_recipients=cc_list,
            )
            print(f"[+] Dispatch status: {res.get('status')} (ID: {res.get('id', 'simulated')})")

    return briefing


def main():
    parser = argparse.ArgumentParser(description="Generate client performance reports and briefings")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # List clients command
    subparsers.add_parser("list-clients", help="List all configured client slugs")

    # Generate report command
    gen_parser = subparsers.add_parser("generate", help="Generate growth briefing report for a client")
    gen_parser.add_argument("--client", "-c", required=True, help="Client ID slug (e.g. example-dental)")
    gen_parser.add_argument("--days", "-d", type=int, default=28, help="Period days to analyze (default: 28)")
    gen_parser.add_argument("--send", "-s", action="store_true", help="Send the report email via Resend")
    gen_parser.add_argument("--mock", "-m", action="store_true", help="Use mock/synthetic analytics data")
    gen_parser.add_argument("--output", "-o", type=Path, default=None, help="Output directory for generated artifacts")

    args = parser.parse_args()

    if args.command == "list-clients":
        clients = list_available_clients()
        print("Available client configurations:")
        for c in clients:
            print(f"  - {c}")
        sys.exit(0)

    elif args.command == "generate":
        try:
            generate_report(
                client_slug=args.client,
                days=args.days,
                send_email=args.send,
                output_dir=args.output,
                mock_data=args.mock,
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
