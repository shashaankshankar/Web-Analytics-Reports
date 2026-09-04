from __future__ import annotations
import html
from app.analytics.contracts import FullGrowthBriefing, REPORT_SPECS, ReportMode, ReportType
from app.ai.privacy import scrub_gsc_query
from app.delivery.email_components import (
    COLOR_BODY,
    COLOR_HAIRLINE,
    COLOR_MUTED,
    COLOR_ON_SURFACE,
    COLOR_PAGE_BG,
    FONT_FAMILY_MAIN,
    FONT_FAMILY_SERIF,
    SECTION_LABEL_REPORT_DELIVERY,
    SECTION_LABEL_WEBSITE_INQUIRY,
    card_surface,
    copyright_year,
    header_text_colors,
    render_action_row,
    render_bar_group,
    render_finding_card,
    render_goal_pills,
    render_keyword_table,
    render_kpi_cells,
    render_ledger_table,
    render_note_band,
    render_report_delivery_block,
    render_section_label,
    render_stat_tiles,
    render_website_inquiry_delivery_block,
    select_strongest_actions,
)
from app.delivery.discovery_copy import build_client_discovery_copies
from app.delivery.gbp_reporting import calls_rows, keyword_rows, performance_rows, profile_rows, review_rows

AGENCY_NAME = "Vector Studios"

# Statuses that mean a Google Business Profile source actually answered.
_GBP_CONNECTED_STATUSES = {"available", "partial"}


def _gbp_report_number(value):
    if value is None:
        return "Not available"
    try:
        number = float(value)
        return f"{int(number):,}" if number.is_integer() else f"{number:,.1f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _commentary(text: str, *, top: int = 12, bottom: int = 0) -> str:
    return (
        f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; line-height: 22px; '
        f'color: {COLOR_BODY}; padding: {top}px 0 {bottom}px 0;">{text}</div>'
    )


def _sub_heading(text: str) -> str:
    return (
        f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; letter-spacing: 0.1em; '
        f'text-transform: uppercase; color: {COLOR_MUTED}; padding: 20px 0 0 0;">{text}</div>'
    )


def render_growth_email_html(briefing: FullGrowthBriefing) -> str:
    """Render the performance report: a dark branded KPI strip over numbered ledger sections."""
    client_name = html.escape(briefing.company_name)
    period = html.escape(briefing.period_label)
    branding = briefing.branding
    primary_color = branding.get("primary_color", "#0A0A0B") or "#0A0A0B"
    secondary_color = branding.get("secondary_color", "#F7F4EE") or "#F7F4EE"
    accent_color = branding.get("accent_color", "#C6A15B") or "#C6A15B"
    logo_url = branding.get("logo_url")
    header_text, header_muted = header_text_colors(primary_color)
    surface = card_surface(secondary_color)

    analytics = briefing.analytics
    insights = briefing.insights
    is_baseline = briefing.report_mode == ReportMode.INITIAL_BASELINE
    suppress_comparison = is_baseline or briefing.comparison_suppressed
    report_name = (
        "Initial Measurement Baseline"
        if is_baseline
        else "28-Day Performance Report"
        if briefing.report_type == ReportType.PERFORMANCE_28D
        else f"{analytics.period_days}-Day Performance Report"
    )

    kpi_cells = render_kpi_cells(
        analytics.core_metrics,
        primary_color,
        value_size=30,
        suppress_comparison=suppress_comparison,
    )
    if suppress_comparison:
        kpi_note = "Baseline period. Change comparisons begin with the next report."
    else:
        kpi_note = (
            f"Compared with the prior {analytics.period_days} days, "
            f"{html.escape(analytics.comparison_start)} to {html.escape(analytics.comparison_end)}."
        )

    # --- 01 Executive overview -------------------------------------------------
    exec_items = "".join(
        f'<tr>'
        f'<td width="28" style="vertical-align: top; padding: 0 0 12px 0; font-family: {FONT_FAMILY_MAIN}; '
        f'font-size: 14px; line-height: 22px; font-weight: 700; color: {COLOR_ON_SURFACE};">{index}</td>'
        f'<td style="vertical-align: top; padding: 0 0 12px 0; font-family: {FONT_FAMILY_MAIN}; '
        f'font-size: 14px; line-height: 22px; color: {COLOR_BODY};">{html.escape(item)}</td>'
        '</tr>'
        for index, item in enumerate(insights.executive_summary, 1)
    )
    exec_list = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top: 12px;">{exec_items}</table>'
        if exec_items
        else ""
    )

    win_card = (
        render_finding_card("Biggest Win", insights.biggest_win, secondary_color, status="win", spaced=False)
        if insights.biggest_win
        else ""
    )
    watch_card = (
        render_finding_card("Area to Improve", insights.watch_item, secondary_color, status="watch", spaced=False)
        if insights.watch_item
        else ""
    )
    if win_card and watch_card:
        highlights = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top: 8px;"><tr>'
            f'<td class="half" width="50%" style="vertical-align: top; padding-right: 6px;">{win_card}</td>'
            f'<td class="half" width="50%" style="vertical-align: top; padding-left: 6px;">{watch_card}</td>'
            '</tr></table>'
        )
    elif win_card or watch_card:
        highlights = f'<div style="margin-top: 8px;">{win_card or watch_card}</div>'
    else:
        highlights = ""

    # --- 02 Customer inquiries & key actions -----------------------------------
    conv_events = analytics.conversion_events[:4]
    stat_tiles = render_stat_tiles(conv_events, secondary_color)
    conv_commentary = (
        _commentary(html.escape(insights.conversion_insights), bottom=14 if stat_tiles else 0)
        if insights.conversion_insights
        else ""
    )
    tiles_markup = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{stat_tiles}</tr></table>'
        if stat_tiles
        else ""
    )
    conv_body = conv_commentary + tiles_markup

    # --- 03 Traffic: commentary plus channel and page bars ---------------------
    channel_bars = render_bar_group(
        "Channels &middot; sessions",
        [(channel.channel, channel.sessions) for channel in analytics.top_channels[:4]],
        primary_color,
        secondary_color,
    )
    page_bars = render_bar_group(
        "Top pages &middot; sessions",
        [(page.page_path, page.sessions) for page in analytics.top_pages[:5]],
        accent_color,
        secondary_color,
    )
    if channel_bars and page_bars:
        bars = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top: 16px;"><tr>'
            f'<td class="half" width="50%" style="vertical-align: top; padding-right: 14px;">{channel_bars}</td>'
            f'<td class="half" width="50%" style="vertical-align: top; padding-left: 14px;">{page_bars}</td>'
            '</tr></table>'
        )
    elif channel_bars or page_bars:
        bars = f'<div style="margin-top: 16px;">{channel_bars or page_bars}</div>'
    else:
        bars = ""
    traffic_body = _commentary(html.escape(insights.traffic_and_inflow_insights)) + bars

    # --- 04 Search opportunities ----------------------------------------------
    keyword_table = render_keyword_table(analytics.striking_distance_keywords[:5], accent_color, scrub_gsc_query)
    search_heading = "High-Opportunity Google Searches" if keyword_table else "Search &amp; Content Topics to Validate"
    search_commentary = (
        _commentary(html.escape(insights.seo_and_content_opportunities))
        if insights.seo_and_content_opportunities
        else ""
    )
    search_body = search_commentary + keyword_table

    # --- 05 Local reputation & maps -------------------------------------------
    local = analytics.local_seo
    local_connected = any(
        str(getattr(local, field, "") or "").strip().lower() in _GBP_CONNECTED_STATUSES
        for field in (
            "profile_status",
            "performance_status",
            "search_keywords_status",
            "reviews_status",
            "business_calls_status",
        )
    )
    local_tag = None if local_connected else "Not connected"

    profile_detail = profile_rows(local)
    profile_html = (
        _sub_heading("Profile details")
        + render_ledger_table(
            [("Field", "left", f"color: {COLOR_MUTED};"), ("Value", "left", f"color: {COLOR_MUTED};")],
            [
                [
                    (html.escape(label), "left", f"color: {COLOR_MUTED}; font-weight: 600;"),
                    (html.escape(value), "left", f"color: {COLOR_ON_SURFACE};"),
                ]
                for label, value in profile_detail
            ],
        )
        if profile_detail
        else ""
    )

    performance_detail = performance_rows(local)
    performance_html = (
        _sub_heading("GBP Performance metrics")
        + render_ledger_table(
            [
                ("Metric", "left", f"color: {COLOR_MUTED};"),
                ("This period", "right", f"color: {COLOR_MUTED};"),
                ("Prior", "right", f"color: {COLOR_MUTED};"),
                ("Change", "right", f"color: {COLOR_MUTED};"),
            ],
            [
                [
                    (html.escape(row["label"]), "left", f"color: {COLOR_ON_SURFACE}; font-weight: 600;"),
                    (_gbp_report_number(row["current"]), "right", f"color: {COLOR_ON_SURFACE};"),
                    (_gbp_report_number(row["prior"]), "right", f"color: {COLOR_MUTED};"),
                    (
                        "baseline"
                        if row["prior"] is None
                        else (f"{row['change']:+.1f}%" if row["change"] is not None else "No % baseline"),
                        "right",
                        f"color: {COLOR_MUTED};",
                    ),
                ]
                for row in performance_detail
            ],
        )
        if performance_detail
        else ""
    )

    gbp_keyword_detail = keyword_rows(local)
    gbp_keyword_html = (
        _sub_heading("Monthly GBP search keywords")
        + render_ledger_table(
            [("Keyword", "left", f"color: {COLOR_MUTED};"), ("Reported value", "right", f"color: {COLOR_MUTED};")],
            [
                [
                    (html.escape(row["keyword"]), "left", f"color: {COLOR_ON_SURFACE}; font-weight: 600;"),
                    (html.escape(row["value"]), "right", f"color: {COLOR_MUTED};"),
                ]
                for row in gbp_keyword_detail
            ],
        )
        if gbp_keyword_detail
        else ""
    )

    review_detail = review_rows(local)
    review_summary = local.review_response_summary or {}
    review_summary_text = ""
    if review_summary:
        complete_label = "complete inventory" if review_summary.get("complete") else "partial inventory"
        coverage = review_summary.get("reply_coverage_percent")
        coverage_text = f"{coverage:.1f}% replied" if coverage is not None else "reply coverage not available"
        review_summary_text = (
            f'{html.escape(str(review_summary.get("review_count", 0)))} reviews, '
            f'{html.escape(str(review_summary.get("unreplied_count", 0)))} not replied, '
            f'{html.escape(coverage_text)} ({complete_label})'
        )
    review_html = (
        _sub_heading("Managed reviews and reply status")
        + (
            f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 12px; color: {COLOR_MUTED}; padding-top: 8px;">{review_summary_text}</div>'
            if review_summary_text
            else ""
        )
        + render_ledger_table(
            [
                ("Rating", "left", f"color: {COLOR_MUTED};"),
                ("Reply", "left", f"color: {COLOR_MUTED};"),
                ("Updated", "left", f"color: {COLOR_MUTED};"),
                ("Recent comment", "left", f"color: {COLOR_MUTED};"),
            ],
            [
                [
                    (html.escape(row["rating"]), "left", f"color: {COLOR_ON_SURFACE}; font-weight: 600;"),
                    (html.escape(row["reply_status"]), "left", f"color: {COLOR_ON_SURFACE};"),
                    (html.escape(row["updated"]), "left", f"color: {COLOR_MUTED};"),
                    (html.escape(row["comment"]), "left", f"color: {COLOR_BODY};"),
                ]
                for row in review_detail
            ],
        )
        if review_detail
        else ""
    )

    calls_detail = calls_rows(local)
    calls_html = (
        _sub_heading("Business Calls insights")
        + f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: {COLOR_BODY}; padding-top: 10px;">'
        + " ".join(
            f'<span style="margin-right: 18px;"><strong style="color: {COLOR_ON_SURFACE};">{html.escape(label)}:</strong> {html.escape(value)}</span>'
            for label, value in calls_detail
        )
        + "</div>"
        if calls_detail
        else ""
    )

    gbp_evidence = "".join((profile_html, performance_html, gbp_keyword_html, review_html, calls_html))
    local_commentary = (
        _commentary(html.escape(insights.local_seo_insights)) if insights.local_seo_insights else ""
    )
    local_body = local_commentary + gbp_evidence

    # --- Where we're focusing next --------------------------------------------
    client_discovery_copies = build_client_discovery_copies(
        insights.deep_discoveries,
        audit=briefing.exploration_audit,
        client_id=analytics.client_id,
        period_start=analytics.period_start,
        period_end=analytics.period_end,
    )
    discovery_cards = ""
    for client_copy in client_discovery_copies:
        discovery_cards += (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{surface}" '
            f'style="background-color: {surface}; margin-bottom: 10px;"><tr><td style="padding: 18px 20px;">'
            f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; font-weight: 700; letter-spacing: 0.1em; '
            f'text-transform: uppercase; color: {accent_color};">{html.escape(client_copy.title.upper())}</div>'
            f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; line-height: 21px; color: {COLOR_BODY}; '
            f'padding: 10px 0 10px 0;"><strong style="color: {COLOR_ON_SURFACE};">What we noticed:</strong> {html.escape(client_copy.what_we_noticed)}</div>'
            f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13px; line-height: 20px; color: {COLOR_ON_SURFACE}; '
            f'border-top: 1px solid {COLOR_HAIRLINE}; padding-top: 10px;">'
            f'<span style="color: {accent_color}; font-weight: 700; text-transform: uppercase; font-size: 10.5px; '
            f'letter-spacing: 0.08em;">Recommended next step:</span> {html.escape(client_copy.recommended_next_step)}</div>'
            "</td></tr></table>"
        )
    deep_insights_enabled = bool(briefing.exploration_audit and briefing.exploration_audit.enabled)
    discoveries_body = (
        _commentary(
            "These are opportunities identified from the current reporting period. "
            "Each item includes a practical next step for the practice.",
            top=0,
            bottom=14,
        )
        + (
            discovery_cards
            or _commentary(
                "No additional opportunities were identified from this period, and no recommendations were added.",
                top=0,
            )
        )
    )

    # --- Recommended next actions ----------------------------------------------
    selected_actions = select_strongest_actions(
        insights.agency_action_plan,
        REPORT_SPECS[briefing.report_type].max_actions,
    )
    action_rows = "".join(
        render_action_row(action, is_last=(index == len(selected_actions) - 1))
        for index, action in enumerate(selected_actions)
    )
    actions_body = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top: 6px;">{action_rows}</table>'
        if action_rows
        else ""
    )

    # --- Delivery health --------------------------------------------------------
    report_delivery_block = render_report_delivery_block(briefing.report_delivery_metrics, primary_color, accent_color)
    website_inquiry_block = render_website_inquiry_delivery_block(
        analytics.website_inquiry_metrics, primary_color, accent_color
    )

    # Sections are numbered in render order so an absent data source never
    # leaves a gap in the sequence.
    section_number = 0

    def section(label: str, body: str, *, tag: str | None = None) -> str:
        nonlocal section_number
        if not body:
            return ""
        section_number += 1
        return f"""
          <tr>
            <td class="pad" style="padding: 28px 36px 0 36px;">
              {render_section_label(section_number, label, accent_color, tag)}
              {body}
            </td>
          </tr>"""

    sections = "".join((
        section("Executive Overview", exec_list + highlights if (exec_list or highlights) else ""),
        section("Customer Inquiries &amp; Key Actions", conv_body),
        section("Where Visitors Came From &amp; What They Viewed", traffic_body),
        section(search_heading, search_body),
        section("Local Reputation &amp; Maps", local_body, tag=local_tag),
        section("Where We're Focusing Next", discoveries_body if deep_insights_enabled else ""),
        section(SECTION_LABEL_REPORT_DELIVERY, report_delivery_block),
        section(SECTION_LABEL_WEBSITE_INQUIRY, website_inquiry_block),
        section("Recommended Next Actions", actions_body),
        section("Current Goals", f'<div style="padding-top: 12px;">{render_goal_pills(analytics.goals)}</div>'),
    ))

    baseline_band = ""
    if is_baseline:
        baseline_text = (
            "This is an Initial Measurement Baseline covering observed data from "
            f"{html.escape(briefing.observation_window_start or analytics.period_start)} through "
            f"{html.escape(briefing.observation_window_end or analytics.period_end)}. The analytics property did "
            "not provide a full earlier comparison window, so no growth deltas or prior-period values are shown. "
            "A normal comparison report begins after a complete later measurement window is available."
        )
        baseline_band = f"""
          <tr>
            <td class="pad" bgcolor="{surface}" style="background-color: {surface}; padding: 14px 36px;">
              {render_note_band(baseline_text, accent_color)}
            </td>
          </tr>"""

    attachment_note = (
        "Detailed Executive PDF Report Attached &middot; "
        if REPORT_SPECS[briefing.report_type].requires_pdf
        else ""
    )

    logo_markup = (
        f'<img src="{html.escape(logo_url)}" alt="{client_name}" height="36" '
        'style="height: 36px; width: auto; max-width: 240px; display: block; border: 0;" />'
        if logo_url
        else f'<span style="font-family: {FONT_FAMILY_SERIF}; font-size: 19px; letter-spacing: 0.02em; color: {COLOR_ON_SURFACE};">{client_name}</span>'
    )

    return f"""<!DOCTYPE html>
<html lang="en" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <title>{client_name} &bull; {report_name}</title>
  <!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
  <style>
    body, table, td, p, span, div {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    img {{ border: 0; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }}
    @media screen and (max-width: 520px) {{
      .wrap {{ width: 100% !important; }}
      .pad {{ padding-left: 20px !important; padding-right: 20px !important; }}
      /* KPIs stay on one row at phone width; the type scales instead of stacking. */
      .kpi {{ padding-right: 6px !important; }}
      .kpi-label {{ font-size: 8.5px !important; letter-spacing: 0.06em !important; }}
      .kpi-val {{ font-size: 20px !important; line-height: 20px !important; letter-spacing: -0.5px !important; }}
      .half {{ display: block !important; width: 100% !important; padding: 0 0 12px 0 !important; box-sizing: border-box !important; }}
      .tile {{ display: inline-block !important; width: 48% !important; box-sizing: border-box !important; }}
      .stack {{ display: block !important; width: 100% !important; text-align: left !important; }}
      .stack-gap {{ padding-top: 6px !important; }}
    }}
  </style>
</head>
<body style="margin: 0; padding: 0; background-color: {COLOR_PAGE_BG};">
  <div style="display: none; max-height: 0; overflow: hidden; mso-hide: all;">{report_name} for {client_name} &mdash; {period}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: {COLOR_PAGE_BG};">
    <tr>
      <td align="center" style="padding: 24px 8px;">
        <!--[if mso]><table role="presentation" width="680" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
        <table role="presentation" class="wrap" width="680" cellpadding="0" cellspacing="0" border="0" style="width: 680px; max-width: 680px; background-color: #FFFFFF; border: 1px solid {COLOR_HAIRLINE};">

          <!-- Brand row -->
          <tr>
            <td class="pad" style="padding: 22px 36px 18px 36px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td class="stack" style="vertical-align: middle;">{logo_markup}</td>
                  <td class="stack stack-gap" align="right" style="vertical-align: middle; font-family: {FONT_FAMILY_MAIN}; font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: {COLOR_MUTED};">Performance Report</td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Branded KPI strip -->
          <tr>
            <td class="pad" bgcolor="{primary_color}" style="background-color: {primary_color}; padding: 30px 36px 28px 36px; border-bottom: 3px solid {accent_color};">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 12px; letter-spacing: 0.04em; color: {accent_color}; padding-bottom: 8px;">{period}</div>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 28px; line-height: 32px; font-weight: 600; letter-spacing: -0.5px; color: {header_text}; padding-bottom: 8px;">{report_name}</div>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 14px; line-height: 21px; color: {header_muted}; max-width: 520px; padding-bottom: 26px;">A clear summary of website visitors, customer inquiries, and growth opportunities for {client_name}.</div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>{kpi_cells}</tr>
              </table>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; color: {header_muted}; padding-top: 14px;">{kpi_note}</div>
            </td>
          </tr>
          {baseline_band}
          {sections}

          <!-- Footer -->
          <tr>
            <td class="pad" style="padding: 20px 36px 0 36px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top: 1px solid {COLOR_HAIRLINE};">
                <tr>
                  <td class="stack" style="padding: 20px 0 24px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 11.5px; color: {COLOR_MUTED};">{attachment_note}Prepared by {AGENCY_NAME} &middot; Confidential</td>
                  <td class="stack" align="right" style="padding: 0 0 24px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 11.5px; color: {COLOR_MUTED};">&copy; {copyright_year(analytics.period_end)} {client_name}</td>
                </tr>
              </table>
            </td>
          </tr>

        </table>
        <!--[if mso]></td></tr></table><![endif]-->
      </td>
    </tr>
  </table>
</body>
</html>
"""
