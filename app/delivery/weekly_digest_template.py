from __future__ import annotations
import html
from app.analytics.contracts import FullGrowthBriefing, ReportMode, WeeklyDigestOutput
from app.delivery.email_components import (
    COLOR_HAIRLINE,
    COLOR_MUTED,
    COLOR_ON_SURFACE,
    COLOR_PAGE_BG,
    FONT_FAMILY_MAIN,
    FONT_FAMILY_SERIF,
    SECTION_LABEL_REPORT_DELIVERY,
    SECTION_LABEL_WEBSITE_INQUIRY,
    card_surface,
    header_text_colors,
    render_finding_card,
    render_goal_pills,
    render_kpi_cells,
    render_note_band,
    render_report_delivery_block,
    render_section_label,
    render_website_inquiry_delivery_block,
)

AGENCY_NAME = "Vector Studios"

# The weekly digest carries one card per insight the model actually produced,
# in the order a reader needs them: the win first, the concern in the middle,
# the forward-looking notes last.
_WEEKLY_FINDINGS = (
    ("biggest_win", "Biggest Win of the Week", "win"),
    ("acquisition_insight", "Visitor Acquisition", "neutral"),
    ("conversion_insight", "Customer Inquiries &amp; Key Actions", "neutral"),
    ("needs_attention", "Area to Improve", "watch"),
    ("search_opportunity", "Google Search Opportunity", "neutral"),
    ("local_insight", "Local Google Maps Activity", "neutral"),
)


def _copyright_year(briefing: FullGrowthBriefing) -> str:
    period_end = briefing.analytics.period_end or ""
    return period_end[:4] if len(period_end) >= 4 and period_end[:4].isdigit() else "2026"


def render_weekly_digest_html(briefing: FullGrowthBriefing) -> str:
    """Render the weekly digest: a dark branded KPI strip over numbered card sections."""
    client_name = html.escape(briefing.company_name)
    branding = briefing.branding
    primary_color = branding.get("primary_color", "#0A0A0B") or "#0A0A0B"
    secondary_color = branding.get("secondary_color", "#F7F4EE") or "#F7F4EE"
    accent_color = branding.get("accent_color", "#C6A15B") or "#C6A15B"
    logo_url = branding.get("logo_url")
    _, header_muted = header_text_colors(primary_color)
    surface = card_surface(secondary_color)

    analytics = briefing.analytics
    if briefing.weekly_insights is None:
        raise ValueError("Weekly insights are unavailable; refusing to render substitute content.")
    insights: WeeklyDigestOutput = briefing.weekly_insights
    is_baseline = briefing.report_mode == ReportMode.INITIAL_BASELINE

    # Three headline metrics fit one row at every width; the rest of the
    # deterministic metric set stays in the 28-day report.
    kpi_metrics = [
        metric for metric in analytics.core_metrics
        if metric.metric_name in ("sessions", "active_users", "conversion_rate")
    ][:3]
    if not kpi_metrics:
        kpi_metrics = analytics.core_metrics[:3]
    kpi_cells = render_kpi_cells(kpi_metrics, primary_color, value_size=36)

    if is_baseline or briefing.comparison_suppressed:
        compare_label = "Baseline period. Change comparisons begin with the next digest."
    else:
        compare_label = (
            "Compared with the prior period, "
            f"{html.escape(analytics.comparison_start)} to {html.escape(analytics.comparison_end)}."
        )

    finding_cards = "".join(
        render_finding_card(
            label,
            getattr(insights, field),
            secondary_color,
            status=status,
            accent_color=accent_color,
        )
        for field, label, status in _WEEKLY_FINDINGS
        if (getattr(insights, field) or "").strip()
    )

    baseline_band = ""
    if is_baseline:
        baseline_text = (
            "This is an Initial Measurement Baseline covering observed data from "
            f"{html.escape(briefing.observation_window_start or analytics.period_start)} through "
            f"{html.escape(briefing.observation_window_end or analytics.period_end)}. A complete earlier "
            "comparison was not available, so this digest shows current activity only and is not a "
            "week-over-week change report."
        )
        baseline_band = f"""
          <tr>
            <td class="pad" bgcolor="{surface}" style="background-color: {surface}; padding: 14px 32px;">
              {render_note_band(baseline_text, accent_color)}
            </td>
          </tr>"""

    # Sections are numbered in render order so an absent data source never
    # leaves a gap in the sequence.
    section_number = 0

    def section(label: str, body: str, *, tag: str | None = None, body_padding: str = "14px 32px 0 32px") -> str:
        nonlocal section_number
        section_number += 1
        return f"""
          <tr>
            <td class="pad" style="padding: 26px 32px 0 32px;">
              {render_section_label(section_number, label, accent_color, tag)}
            </td>
          </tr>
          <tr>
            <td class="pad" style="padding: {body_padding};">
              {body}
            </td>
          </tr>"""

    findings_section = section("What Happened This Week", finding_cards) if finding_cards else ""
    goals_section = section("Current Goals", render_goal_pills(analytics.goals), body_padding="12px 32px 0 32px")

    report_delivery_block = render_report_delivery_block(briefing.report_delivery_metrics, primary_color, accent_color)
    report_delivery_section = (
        section(SECTION_LABEL_REPORT_DELIVERY, report_delivery_block, body_padding="0 32px")
        if report_delivery_block
        else ""
    )
    website_inquiry_block = render_website_inquiry_delivery_block(
        analytics.website_inquiry_metrics, primary_color, accent_color
    )
    website_inquiry_section = (
        section(SECTION_LABEL_WEBSITE_INQUIRY, website_inquiry_block, body_padding="0 32px")
        if website_inquiry_block
        else ""
    )

    logo_markup = (
        f'<img src="{html.escape(logo_url)}" alt="{client_name}" height="36" '
        'style="height: 36px; width: auto; max-width: 220px; display: block; border: 0;" />'
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
  <title>{client_name} &bull; Weekly Growth Digest</title>
  <!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
  <style>
    body, table, td, p, span, div {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    img {{ border: 0; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }}
    @media screen and (max-width: 480px) {{
      .wrap {{ width: 100% !important; }}
      .pad {{ padding-left: 20px !important; padding-right: 20px !important; }}
      /* KPIs stay on one row at phone width; the type scales instead of stacking. */
      .kpi {{ padding-right: 10px !important; }}
      .kpi-label {{ font-size: 9px !important; letter-spacing: 0.08em !important; }}
      .kpi-val {{ font-size: 28px !important; line-height: 28px !important; }}
      .stack {{ display: block !important; width: 100% !important; text-align: left !important; }}
      .stack-gap {{ padding-top: 6px !important; }}
    }}
  </style>
</head>
<body style="margin: 0; padding: 0; background-color: {COLOR_PAGE_BG};">
  <div style="display: none; max-height: 0; overflow: hidden; mso-hide: all;">{html.escape(insights.biggest_win)}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: {COLOR_PAGE_BG};">
    <tr>
      <td align="center" style="padding: 24px 8px;">
        <!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
        <table role="presentation" class="wrap" width="600" cellpadding="0" cellspacing="0" border="0" style="width: 600px; max-width: 600px; background-color: #FFFFFF; border: 1px solid {COLOR_HAIRLINE};">

          <!-- Brand row -->
          <tr>
            <td class="pad" style="padding: 22px 32px 18px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td class="stack" style="vertical-align: middle;">{logo_markup}</td>
                  <td class="stack stack-gap" align="right" style="vertical-align: middle; font-family: {FONT_FAMILY_MAIN}; font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: {COLOR_MUTED};">Weekly Digest</td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Branded KPI strip -->
          <tr>
            <td class="pad" bgcolor="{primary_color}" style="background-color: {primary_color}; padding: 28px 32px 26px 32px; border-bottom: 3px solid {accent_color};">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 12px; letter-spacing: 0.04em; color: {accent_color}; padding-bottom: 22px;">{html.escape(briefing.period_label)}</div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>{kpi_cells}</tr>
              </table>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; color: {header_muted}; padding-top: 14px;">{compare_label}</div>
            </td>
          </tr>
          {baseline_band}
          {findings_section}
          {goals_section}
          {report_delivery_section}
          {website_inquiry_section}

          <!-- Footer -->
          <tr>
            <td class="pad" style="padding: 20px 32px 0 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top: 1px solid {COLOR_HAIRLINE};">
                <tr>
                  <td class="stack" style="padding: 20px 0 24px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 11.5px; color: {COLOR_MUTED};">Prepared by {AGENCY_NAME} &middot; Confidential</td>
                  <td class="stack" align="right" style="padding: 0 0 24px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 11.5px; color: {COLOR_MUTED};">&copy; {_copyright_year(briefing)} {client_name}</td>
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
