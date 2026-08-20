from __future__ import annotations
import html
from app.analytics.contracts import FullGrowthBriefing, ReportType
from app.delivery.email_components import (
    FONT_FAMILY_MAIN,
    FONT_FAMILY_SERIF,
    STYLE_CARD,
    is_light_color,
    render_action_card,
    render_kpi_card,
)

def render_growth_email_html(briefing: FullGrowthBriefing) -> str:
    """Render a publication-grade, decision-focused executive performance briefing email."""
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
    insights = briefing.insights
    report_name = "28-Day Performance Report" if briefing.report_type == ReportType.PERFORMANCE_28D else f"{analytics.period_days}-Day Performance Report"

    # 1. Metric Cards Grid
    metrics_cards_html = ""
    num_metrics = len(analytics.core_metrics) or 1
    col_width_pct = max(18, min(33, int(100 / num_metrics)))
    for m in analytics.core_metrics:
        metrics_cards_html += f"""
        <td style="width: {col_width_pct}%; padding: 0 4px; vertical-align: top;">
          {render_kpi_card(m, accent_color)}
        </td>
        """

    # 2. Executive Snapshot Takeaways (Numbered Editorial List)
    exec_summary_html = ""
    for idx, item in enumerate(insights.executive_summary, 1):
        escaped_item = html.escape(item)
        exec_summary_html += f"""
        <tr>
          <td style="vertical-align: top; width: 28px; padding: 8px 0;">
            <div style="width: 22px; height: 22px; border-radius: 50%; background: {accent_color}; color: #FFFFFF; font-family: {FONT_FAMILY_SERIF}; font-size: 11px; font-weight: 700; line-height: 22px; text-align: center;">{idx}</div>
          </td>
          <td style="vertical-align: middle; padding: 8px 0 8px 10px; font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #27272A; line-height: 1.55;">
            {escaped_item}
          </td>
        </tr>
        """

    # 3. Decision Highlights: Biggest Win & Watch Item
    biggest_win_block = ""
    if insights.biggest_win:
        biggest_win_block = f"""
        <div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-left: 4px solid #16A34A; border-radius: 6px; padding: 14px 16px; margin-bottom: 12px;">
          <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.1em; color: #15803D; font-weight: 700; margin-bottom: 4px;">&#9733; Biggest Win</div>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: #166534; line-height: 1.5;">{html.escape(insights.biggest_win)}</p>
        </div>
        """

    watch_item_block = ""
    if insights.watch_item:
        watch_item_block = f"""
        <div style="background: #FFFBEB; border: 1px solid #FDE68A; border-left: 4px solid #D97706; border-radius: 6px; padding: 14px 16px; margin-bottom: 12px;">
          <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.1em; color: #B45309; font-weight: 700; margin-bottom: 4px;">&#9888; Primary Risk / Watch Item</div>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: #92400E; line-height: 1.5;">{html.escape(insights.watch_item)}</p>
        </div>
        """

    # 4. Conversion Breakdown Table
    conv_rows_html = ""
    for ce in analytics.conversion_events[:4]:
        ev_name = html.escape(ce.display_name)
        pct_str = f"{ce.percentage_change:+.1f}%" if ce.percentage_change is not None else "-"
        if ce.direction == "up":
            dir_icon = "&#8593;"
            color_style = "color: #047857;"
        elif ce.direction == "down":
            dir_icon = "&#8595;"
            color_style = "color: #B91C1C;"
        else:
            dir_icon = "&rarr;"
            color_style = "color: #4B5563;"
        conv_rows_html += f"""
        <tr style="border-bottom: 1px solid #F4F4F5;">
          <td style="padding: 10px 14px; font-family: {FONT_FAMILY_MAIN}; font-weight: 600; color: #18181B; font-size: 13px;">{ev_name}</td>
          <td style="padding: 10px 14px; text-align: center; color: #18181B; font-size: 13px; font-weight: 600; font-family: {FONT_FAMILY_MAIN};">{ce.current_count:,}</td>
          <td style="padding: 10px 14px; text-align: center; color: #71717A; font-size: 12.5px; font-family: {FONT_FAMILY_MAIN};">{ce.prior_count:,}</td>
          <td style="padding: 10px 14px; text-align: right; {color_style} font-weight: 600; font-size: 12.5px; font-family: {FONT_FAMILY_MAIN};">{dir_icon} {pct_str}</td>
        </tr>
        """

    conv_section = ""
    if conv_rows_html:
        conv_insights_escaped = html.escape(insights.conversion_insights) if insights.conversion_insights else ""
        conv_commentary = f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: #3F3F46; line-height: 1.55; margin-bottom: 12px;">{conv_insights_escaped}</div>' if conv_insights_escaped else ""
        conv_section = f"""
        <tr>
          <td style="padding: 0 32px 24px 32px;">
            <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_color}; font-weight: 700; margin-bottom: 8px;">Key Conversion Performance</div>
            {conv_commentary}
            <table role="presentation" style="width: 100%; border-collapse: collapse; border: 1px solid #E4E4E7; border-radius: 4px; overflow: hidden;">
              <thead>
                <tr style="background-color: #FAFAFA; border-bottom: 1px solid #E4E4E7;">
                  <th style="padding: 9px 14px; text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Action</th>
                  <th style="padding: 9px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Current</th>
                  <th style="padding: 9px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Prior</th>
                  <th style="padding: 9px 14px; text-align: right; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Change</th>
                </tr>
              </thead>
              <tbody>
                {conv_rows_html}
              </tbody>
            </table>
          </td>
        </tr>
        """

    # 5. Striking Distance Keywords Rows
    kw_rows_html = ""
    for kw in analytics.striking_distance_keywords[:5]:
        kw_query = html.escape(kw.query)
        kw_rows_html += f"""
        <tr style="border-bottom: 1px solid #F4F4F5;">
          <td style="padding: 11px 14px; font-family: {FONT_FAMILY_MAIN}; font-weight: 600; color: #18181B; font-size: 13px;">
            {kw_query}
          </td>
          <td style="padding: 11px 14px; text-align: center; color: #52525B; font-size: 12.5px; font-family: {FONT_FAMILY_MAIN};">
            {kw.impressions:,}
          </td>
          <td style="padding: 11px 14px; text-align: center; color: #18181B; font-size: 12.5px; font-weight: 600; font-family: {FONT_FAMILY_MAIN};">
            {kw.position:.1f}
          </td>
          <td style="padding: 11px 14px; text-align: right; color: {accent_color}; font-weight: 700; font-size: 13px; font-family: {FONT_FAMILY_SERIF};">
            {kw.opportunity_score:.0f}
          </td>
        </tr>
        """
    if not kw_rows_html:
        kw_rows_html = '<tr><td colspan="4" style="padding: 16px; text-align: center; color: #A1A1AA; font-size: 13px;">No striking-distance queries recorded for this cycle.</td></tr>'

    # 6. Autonomous Deep Discoveries
    discoveries_html = ""
    if insights.deep_discoveries:
        for disc in insights.deep_discoveries:
            disc_title = html.escape(disc.title)
            disc_source = html.escape(disc.source)
            disc_insight = html.escape(disc.insight)
            disc_rec = html.escape(disc.recommended_action)
            discoveries_html += f"""
            <div style="background: #FFFFFF; border: 1px solid #E4E4E7; border-left: 3px solid {accent_color}; border-radius: 4px; padding: 16px 18px; margin-bottom: 14px;">
              <table role="presentation" style="width: 100%; border-collapse: collapse; margin-bottom: 8px;">
                <tr>
                  <td style="vertical-align: middle;">
                    <strong style="font-family: {FONT_FAMILY_SERIF}; color: #09090B; font-size: 14.5px; font-weight: 700;">{disc_title}</strong>
                  </td>
                  <td style="text-align: right; vertical-align: middle;">
                    <span style="display: inline-block; background: #F4F4F5; color: #52525B; border: 1px solid #E4E4E7; padding: 2px 8px; border-radius: 3px; font-size: 10.5px; font-weight: 600; letter-spacing: 0.03em; text-transform: uppercase;">{disc_source}</span>
                  </td>
                </tr>
              </table>
              <p style="margin: 0 0 10px 0; color: #52525B; font-size: 13px; line-height: 1.5; font-family: {FONT_FAMILY_MAIN};">{disc_insight}</p>
              <div style="font-size: 12px; color: #18181B; background: #FAFAFA; border: 1px dashed #D4D4D8; border-radius: 4px; padding: 8px 12px; font-family: {FONT_FAMILY_MAIN};">
                <span style="color: {accent_color}; font-weight: 700; text-transform: uppercase; font-size: 10.5px; letter-spacing: 0.05em;">Action:</span> {disc_rec}
              </div>
            </div>
            """

    # 7. Prioritized Agency Action Plan
    action_items_html = ""
    for act in insights.agency_action_plan:
        action_items_html += render_action_card(act, accent_color)

    logo_markup = f'<img src="{html.escape(logo_url)}" alt="{client_name}" style="max-height: 32px; max-width: 160px; margin-bottom: 14px; display: block;" />' if logo_url else ""
    traffic_insights_escaped = html.escape(insights.traffic_and_inflow_insights)
    local_insights_escaped = html.escape(insights.local_seo_insights) if insights.local_seo_insights else ""

    discoveries_section = f"""<!-- Section: Autonomous Deep Discoveries -->
          <tr>
            <td style="padding: 0 32px 24px 32px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_color}; font-weight: 700; margin-bottom: 12px;">Autonomous Deep Discoveries</div>
              {discoveries_html}
            </td>
          </tr>""" if discoveries_html else ""

    local_section = f"""<!-- Section: Local SEO Dynamics -->
          <tr>
            <td style="padding: 0 32px 24px 32px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_color}; font-weight: 700; margin-bottom: 10px;">Local Market &amp; Reputation</div>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #3F3F46; line-height: 1.6; background: #FAFAFA; border: 1px solid #E4E4E7; border-radius: 4px; padding: 16px 18px;">
                {local_insights_escaped}
              </div>
            </td>
          </tr>""" if local_insights_escaped else ""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{client_name} &bull; {report_name}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #F4F4F5; font-family: {FONT_FAMILY_MAIN}; -webkit-font-smoothing: antialiased; color: #18181B;">
  <table role="presentation" style="width: 100%; border-collapse: collapse; background-color: #F4F4F5; padding: 32px 0;">
    <tr>
      <td align="center" style="padding: 24px 12px;">
        <table role="presentation" style="width: 100%; max-width: 640px; border-collapse: collapse; background-color: #FFFFFF; border-radius: 8px; overflow: hidden; border: 1px solid #E4E4E7; box-shadow: 0 4px 16px -2px rgba(0, 0, 0, 0.06);">
          
          <!-- Header Banner -->
          <tr>
            <td style="background-color: {primary_color}; padding: 36px 32px; color: {header_title_color}; border-bottom: 2px solid {accent_color};">
              {logo_markup}
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.14em; color: {accent_color}; font-weight: 700; margin-bottom: 8px;">{report_name}</div>
              <h1 style="margin: 0 0 6px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 26px; font-weight: 700; letter-spacing: -0.01em; color: {header_title_color}; line-height: 1.2;">{client_name}</h1>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 12.5px; color: {header_subtitle_color};">{period}</div>
            </td>
          </tr>

          <!-- Section: Executive Overview -->
          <tr>
            <td style="padding: 32px 32px 16px 32px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_color}; font-weight: 700; margin-bottom: 12px;">Executive Overview</div>
              <table role="presentation" style="width: 100%; border-collapse: collapse; margin-bottom: 14px;">
                {exec_summary_html}
              </table>
              {biggest_win_block}
              {watch_item_block}
            </td>
          </tr>

          <!-- Section: Core Performance Metrics -->
          <tr>
            <td style="padding: 0 28px 24px 28px;">
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                <tr>
                  {metrics_cards_html}
                </tr>
              </table>
            </td>
          </tr>

          {conv_section}

          <!-- Divider -->
          <tr>
            <td style="padding: 0 32px;">
              <hr style="border: 0; border-top: 1px solid #E4E4E7; margin: 0;" />
            </td>
          </tr>

          <!-- Section: Inflow & Channel Dynamics -->
          <tr>
            <td style="padding: 28px 32px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_color}; font-weight: 700; margin-bottom: 10px;">Traffic &amp; Inflow Dynamics</div>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #3F3F46; line-height: 1.6; background: #FAFAFA; border: 1px solid #E4E4E7; border-radius: 4px; padding: 16px 18px;">
                {traffic_insights_escaped}
              </div>
            </td>
          </tr>

          <!-- Section: Striking Distance Search Opportunities -->
          <tr>
            <td style="padding: 0 32px 28px 32px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_color}; font-weight: 700; margin-bottom: 6px;">Striking-Distance Search Opportunities</div>
              <p style="margin: 0 0 12px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 12.5px; color: #71717A;">Queries ranking on page 2 (positions 8&ndash;20) poised for breakthrough with targeted on-page optimization:</p>
              <table role="presentation" style="width: 100%; border-collapse: collapse; border: 1px solid #E4E4E7; border-radius: 4px; overflow: hidden;">
                <thead>
                  <tr style="background-color: #FAFAFA; border-bottom: 1px solid #E4E4E7;">
                    <th style="padding: 10px 14px; text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Target Search Query</th>
                    <th style="padding: 10px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Impressions</th>
                    <th style="padding: 10px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Avg Rank</th>
                    <th style="padding: 10px 14px; text-align: right; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: {accent_color};">Opp. Score</th>
                  </tr>
                </thead>
                <tbody>
                  {kw_rows_html}
                </tbody>
              </table>
            </td>
          </tr>

          {discoveries_section}
          {local_section}

          <!-- Section: Strategic Action Plan -->
          <tr>
            <td style="padding: 0 32px 32px 32px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_color}; font-weight: 700; margin-bottom: 12px;">Strategic Action Plan &amp; Priorities</div>
              {action_items_html}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color: #FAFAFA; border-top: 1px solid #E4E4E7; padding: 24px 32px; text-align: center;">
              <div style="display: inline-block; background: #F4F4F5; border: 1px solid #E4E4E7; padding: 6px 14px; border-radius: 4px; font-family: {FONT_FAMILY_MAIN}; font-size: 11.5px; font-weight: 600; color: #3F3F46; margin-bottom: 10px;">
                &#128206; Detailed Executive PDF Report Attached
              </div>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; color: #A1A1AA;">
                Prepared exclusively for {client_name} &bull; Confidential
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
