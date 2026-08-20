from __future__ import annotations
import html
from typing import Optional
from app.analytics.contracts import FullGrowthBriefing, WeeklyDigestOutput
from app.delivery.email_components import (
    FONT_FAMILY_MAIN,
    FONT_FAMILY_SERIF,
    is_light_color,
    render_action_card,
    render_kpi_card,
)

def render_weekly_digest_html(briefing: FullGrowthBriefing) -> str:
    """Render a mobile-first, one-minute executive Weekly Growth Digest email."""
    client_name = html.escape(briefing.company_name)
    period = html.escape(briefing.period_label)
    branding = briefing.branding
    primary_color = branding.get("primary_color", "#0A0A0B")
    secondary_color = branding.get("secondary_color", "#F7F4EE")
    accent_color = branding.get("accent_color", "#C6A15B") or "#C6A15B"
    logo_url = branding.get("logo_url")

    primary_light = is_light_color(primary_color)
    header_title_color = "#18181B" if primary_light else "#FFFFFF"
    header_subtitle_color = "rgba(0,0,0,0.6)" if primary_light else "rgba(255,255,255,0.7)"

    analytics = briefing.analytics
    insights: WeeklyDigestOutput = briefing.weekly_insights or WeeklyDigestOutput(
        biggest_win="Performance remained stable across key indicators this week.",
        acquisition_insight="Core traffic channels supported steady user engagement.",
    )

    # 1. KPI Cards (Select top 3-4 metrics for compact weekly scanning)
    kpi_metrics = [m for m in analytics.core_metrics if m.metric_name in ("sessions", "conversion_rate", "conversions", "active_users")][:4]
    kpi_html = ""
    for m in kpi_metrics:
        kpi_html += f"""
        <td style="width: 50%; padding: 4px; vertical-align: top;">
          {render_kpi_card(m, accent_color)}
        </td>
        """

    # Structure KPIs as 2x2 grid for mobile/desktop friendliness
    kpi_grid_rows = ""
    if len(kpi_metrics) >= 4:
        kpi_grid_rows = f"""
        <tr>
          <td style="width: 50%; padding: 4px; vertical-align: top;">{render_kpi_card(kpi_metrics[0], accent_color)}</td>
          <td style="width: 50%; padding: 4px; vertical-align: top;">{render_kpi_card(kpi_metrics[1], accent_color)}</td>
        </tr>
        <tr>
          <td style="width: 50%; padding: 4px; vertical-align: top;">{render_kpi_card(kpi_metrics[2], accent_color)}</td>
          <td style="width: 50%; padding: 4px; vertical-align: top;">{render_kpi_card(kpi_metrics[3], accent_color)}</td>
        </tr>
        """
    elif len(kpi_metrics) > 0:
        row_content = "".join([f'<td style="padding: 4px; vertical-align: top;">{render_kpi_card(m, accent_color)}</td>' for m in kpi_metrics])
        kpi_grid_rows = f"<tr>{row_content}</tr>"

    # 2. Biggest Win Callout
    biggest_win_html = f"""
    <div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-left: 4px solid #16A34A; border-radius: 6px; padding: 14px 16px; margin-bottom: 14px;">
      <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.1em; color: #15803D; font-weight: 700; margin-bottom: 4px;">&#9733; Biggest Win of the Week</div>
      <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #166534; line-height: 1.5; font-weight: 500;">{html.escape(insights.biggest_win)}</p>
    </div>
    """

    # 3. Needs Attention Callout (Dynamic omission if None)
    needs_attention_html = ""
    if insights.needs_attention:
        needs_attention_html = f"""
        <div style="background: #FEF2F2; border: 1px solid #FECACA; border-left: 4px solid #DC2626; border-radius: 6px; padding: 14px 16px; margin-bottom: 14px;">
          <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.1em; color: #B91C1C; font-weight: 700; margin-bottom: 4px;">&#9888; Area Needing Attention</div>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #991B1B; line-height: 1.5;">{html.escape(insights.needs_attention)}</p>
        </div>
        """

    # 4. Search Opportunity Callout (Dynamic omission)
    search_opp_html = ""
    if insights.search_opportunity:
        search_opp_html = f"""
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 4px solid {accent_color}; border-radius: 6px; padding: 14px 16px; margin-bottom: 14px;">
          <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.1em; color: {accent_color}; font-weight: 700; margin-bottom: 4px;">Search Visibility Opportunity</div>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: #334155; line-height: 1.5;">{html.escape(insights.search_opportunity)}</p>
        </div>
        """

    # 5. Local Insights (Dynamic omission if no GBP)
    local_html = ""
    if insights.local_insight:
        local_html = f"""
        <div style="background: #FAFAFA; border: 1px solid #E4E4E7; border-radius: 6px; padding: 12px 16px; margin-bottom: 14px;">
          <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: #71717A; font-weight: 600; margin-bottom: 4px;">Local Maps &amp; Direct Engagement</div>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 12.5px; color: #3F3F46; line-height: 1.45;">{html.escape(insights.local_insight)}</p>
        </div>
        """

    # 6. Next Actions (Strict limit 1-2 items)
    actions_html = ""
    for act in insights.next_actions[:2]:
        actions_html += render_action_card(act, accent_color)
    if not actions_html:
        actions_html = f'<p style="font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: #71717A;">Continue standard optimization schedule.</p>'

    logo_markup = f'<img src="{html.escape(logo_url)}" alt="{client_name}" style="max-height: 30px; max-width: 150px; margin-bottom: 12px; display: block;" />' if logo_url else ""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{client_name} &bull; Weekly Growth Digest</title>
</head>
<body style="margin: 0; padding: 0; background-color: #F4F4F5; font-family: {FONT_FAMILY_MAIN}; -webkit-font-smoothing: antialiased; color: #18181B;">
  <table role="presentation" style="width: 100%; border-collapse: collapse; background-color: #F4F4F5; padding: 24px 0;">
    <tr>
      <td align="center" style="padding: 16px 8px;">
        <table role="presentation" style="width: 100%; max-width: 580px; border-collapse: collapse; background-color: #FFFFFF; border-radius: 8px; overflow: hidden; border: 1px solid #E4E4E7; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);">
          
          <!-- Digest Header -->
          <tr>
            <td style="background-color: {primary_color}; padding: 28px 24px; color: {header_title_color}; border-bottom: 3px solid {accent_color};">
              {logo_markup}
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_color}; font-weight: 700; margin-bottom: 6px;">Weekly Growth Digest</div>
              <h1 style="margin: 0 0 4px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 24px; font-weight: 700; color: {header_title_color}; line-height: 1.2;">{client_name}</h1>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 12px; color: {header_subtitle_color};">{period}</div>
            </td>
          </tr>

          <!-- Section: 7-Day Performance Cards -->
          <tr>
            <td style="padding: 24px 20px 12px 20px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.1em; color: {accent_color}; font-weight: 700; margin-bottom: 8px;">Week at a Glance</div>
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                {kpi_grid_rows}
              </table>
            </td>
          </tr>

          <!-- Section: High-Priority Insights -->
          <tr>
            <td style="padding: 8px 20px 16px 20px;">
              {biggest_win_html}
              {needs_attention_html}
              {search_opp_html}
              {local_html}
            </td>
          </tr>

          <!-- Section: Next Actions -->
          <tr>
            <td style="padding: 0 20px 24px 20px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.1em; color: {accent_color}; font-weight: 700; margin-bottom: 10px;">Immediate Next Actions</div>
              {actions_html}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color: #FAFAFA; border-top: 1px solid #E4E4E7; padding: 18px 24px; text-align: center;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; color: #71717A;">
                Prepared for {client_name} &bull; Delivered weekly by your Growth Team
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
