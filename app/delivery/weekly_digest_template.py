from __future__ import annotations
import html
from typing import Optional
from app.analytics.contracts import FullGrowthBriefing, WeeklyDigestOutput
from app.delivery.email_components import (
    COLOR_BG,
    COLOR_ON_SURFACE,
    COLOR_SECONDARY,
    COLOR_SURFACE,
    COLOR_SURFACE_VARIANT,
    COLOR_TERTIARY,
    FONT_FAMILY_MAIN,
    FONT_FAMILY_SERIF,
    is_light_color,
    render_action_card,
    render_kpi_card,
)

def render_weekly_digest_html(briefing: FullGrowthBriefing) -> str:
    """Render an editorial, mobile-first one-minute executive Weekly Growth Digest email incorporating client branding."""
    client_name = html.escape(briefing.company_name)
    period = html.escape(briefing.period_label)
    branding = briefing.branding
    primary_color = branding.get("primary_color", "#0A0A0B") or "#0A0A0B"
    secondary_color = branding.get("secondary_color", "#515F74") or "#515F74"
    accent_color = branding.get("accent_color", "#C6A15B") or "#C6A15B"
    logo_url = branding.get("logo_url")

    primary_is_light = is_light_color(primary_color)
    header_text_color = "#191C1E" if primary_is_light else "#FFFFFF"
    pill_bg = accent_color if not primary_is_light else primary_color
    pill_text = "#0A0A0B" if is_light_color(pill_bg) else "#FFFFFF"

    analytics = briefing.analytics
    insights: WeeklyDigestOutput = briefing.weekly_insights or WeeklyDigestOutput(
        biggest_win="Performance remained stable across key indicators this week.",
        acquisition_insight="Core traffic channels supported steady user engagement.",
    )

    # 1. KPI Cards (Select top 4 metrics for 2x2 grid)
    kpi_metrics = [m for m in analytics.core_metrics if m.metric_name in ("sessions", "conversion_rate", "conversions", "active_users")][:4]
    if not kpi_metrics and analytics.core_metrics:
        kpi_metrics = analytics.core_metrics[:4]

    # Structure KPIs as 2x2 grid
    kpi_grid_rows = ""
    if len(kpi_metrics) >= 4:
        kpi_grid_rows = f"""
        <tr>
          <td style="width: 50%; padding: 4px; vertical-align: top;">{render_kpi_card(kpi_metrics[0], primary_color, accent_color)}</td>
          <td style="width: 50%; padding: 4px; vertical-align: top;">{render_kpi_card(kpi_metrics[1], primary_color, accent_color)}</td>
        </tr>
        <tr>
          <td style="width: 50%; padding: 4px; vertical-align: top;">{render_kpi_card(kpi_metrics[2], primary_color, accent_color)}</td>
          <td style="width: 50%; padding: 4px; vertical-align: top;">{render_kpi_card(kpi_metrics[3], primary_color, accent_color)}</td>
        </tr>
        """
    elif len(kpi_metrics) > 0:
        row_content = "".join([f'<td style="padding: 4px; vertical-align: top;">{render_kpi_card(m, primary_color, accent_color)}</td>' for m in kpi_metrics])
        kpi_grid_rows = f"<tr>{row_content}</tr>"

    # 2. Executive Overview / High-Priority Insights (Discoveries styled cards)
    biggest_win_html = f"""
    <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-left: 4px solid #16A34A; border-radius: 2px; padding: 18px 20px; margin-bottom: 14px;">
      <span style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #16A34A; font-weight: 700; display: block; margin-bottom: 4px;">&#9733; Biggest Win of the Week</span>
      <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 14px; color: #191C1E; line-height: 1.5; font-weight: 500;">{html.escape(insights.biggest_win)}</p>
    </div>
    """

    needs_attention_html = ""
    if insights.needs_attention:
        needs_attention_html = f"""
        <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-left: 4px solid #BA1A1A; border-radius: 2px; padding: 18px 20px; margin-bottom: 14px;">
          <span style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #BA1A1A; font-weight: 700; display: block; margin-bottom: 4px;">&#9888; Area to Improve</span>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 14px; color: #191C1E; line-height: 1.5;">{html.escape(insights.needs_attention)}</p>
        </div>
        """

    search_opp_html = ""
    if insights.search_opportunity:
        search_opp_html = f"""
        <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-left: 4px solid {accent_color}; border-radius: 2px; padding: 18px 20px; margin-bottom: 14px;">
          <span style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #515F74; font-weight: 700; display: block; margin-bottom: 4px;">Google Search Opportunity</span>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #45464D; line-height: 1.5;">{html.escape(insights.search_opportunity)}</p>
        </div>
        """

    local_html = ""
    if insights.local_insight:
        local_html = f"""
        <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-radius: 2px; padding: 16px 18px; margin-bottom: 14px;">
          <span style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #515F74; font-weight: 700; display: block; margin-bottom: 4px;">Local Google Maps Activity</span>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #45464D; line-height: 1.5;">{html.escape(insights.local_insight)}</p>
        </div>
        """

    # 3. Next Actions (Strategic Action Plan)
    actions_html = ""
    num_acts = len(insights.next_actions[:2])
    for idx, act in enumerate(insights.next_actions[:2]):
        is_last = (idx == num_acts - 1)
        actions_html += render_action_card(act, primary_color, accent_color, is_last=is_last)
    if not actions_html:
        actions_html = f'<div style="padding: 16px; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: #515F74;">Continue standard optimization schedule.</div>'

    logo_markup = f'<img src="{html.escape(logo_url)}" alt="{client_name}" height="52" style="height: 52px; width: auto; max-width: 260px; max-height: 56px; display: block; border: 0;" />' if logo_url else f'<span style="font-family: {FONT_FAMILY_SERIF}; font-size: 20px; font-weight: 700; color: {header_text_color}; letter-spacing: -0.01em;">{client_name}</span>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{client_name} &bull; Weekly Growth Digest</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&family=Work+Sans:wght@400;600;700&display=swap" rel="stylesheet">
</head>
<body style="margin: 0; padding: 0; background-color: #F7F9FB; font-family: {FONT_FAMILY_MAIN}; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; color: #191C1E;">
  <table role="presentation" style="width: 100%; border-collapse: collapse; background-color: #F7F9FB; padding: 24px 0;">
    <tr>
      <td align="center" style="padding: 16px 8px;">
        <table role="presentation" style="width: 100%; max-width: 600px; border-collapse: collapse; background-color: #FFFFFF; border-radius: 2px; overflow: hidden; border: 1px solid #E0E3E5; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);">
          
          <!-- Top Navigation / Branding Bar -->
          <tr>
            <td style="background-color: {primary_color}; padding: 18px 24px; border-bottom: 2px solid {accent_color};">
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                <tr>
                  <td style="vertical-align: middle;">
                    {logo_markup}
                  </td>
                  <td style="text-align: right; vertical-align: middle;">
                    <span style="display: inline-block; background-color: {pill_bg}; color: {pill_text}; padding: 5px 10px; border-radius: 2px; font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;">
                      Weekly Digest
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Header Section -->
          <tr>
            <td style="padding: 28px 24px 20px 24px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: #515F74; font-weight: 700; margin-bottom: 6px;">
                Confidential Report &bull; {period}
              </div>
              <h1 style="margin: 0 0 10px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 28px; font-weight: 700; letter-spacing: -0.02em; color: {primary_color}; line-height: 1.2;">
                Weekly Growth Digest
              </h1>
              <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 15px; color: #45464D; line-height: 1.55;">
                Executive summary of key performance indicators and critical insights for <strong style="color: #191C1E;">{client_name}</strong>.
              </p>
            </td>
          </tr>

          <!-- Editorial Divider -->
          <tr>
            <td style="padding: 0 24px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>

          <!-- Section: 7-Day Performance Cards -->
          <tr>
            <td style="padding: 24px 20px 16px 20px;">
              <h2 style="margin: 0 0 14px 4px; font-family: {FONT_FAMILY_SERIF}; font-size: 18px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Week at a Glance</h2>
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                {kpi_grid_rows}
              </table>
            </td>
          </tr>

          <!-- Editorial Divider -->
          <tr>
            <td style="padding: 0 24px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>

          <!-- Section: High-Priority Insights -->
          <tr>
            <td style="padding: 20px 24px 16px 24px;">
              <h2 style="margin: 0 0 14px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 18px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Key Insights &amp; Updates</h2>
              {biggest_win_html}
              {needs_attention_html}
              {search_opp_html}
              {local_html}
            </td>
          </tr>

          <!-- Editorial Divider -->
          <tr>
            <td style="padding: 0 24px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>

          <!-- Section: Immediate Next Actions -->
          <tr>
            <td style="padding: 20px 24px 28px 24px;">
              <h2 style="margin: 0 0 14px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 18px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Recommended Next Actions</h2>
              <div style="background-color: #FFFFFF; border: 1px solid #E0E3E5; border-radius: 2px; overflow: hidden;">
                {actions_html}
              </div>
            </td>
          </tr>

        <!-- Footer -->
        <tr>
          <td style="background-color: #FFFFFF; border-top: 1px solid rgba(0, 0, 0, 0.12); padding: 20px 24px; text-align: center;">
            <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 12px; color: #515F74;">
               &copy; 2026 {client_name} &bull; Prepared by Vector Studios.
            </p>
           </td>
         </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
