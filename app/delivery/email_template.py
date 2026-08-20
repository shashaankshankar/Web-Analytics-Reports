from __future__ import annotations
import html

from app.analytics.contracts import FullGrowthBriefing


def is_light_color(hex_str: str) -> bool:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return ((0.299 * r + 0.587 * g + 0.114 * b) / 255.0) > 0.65
    except Exception:
        return False


def render_growth_email_html(briefing: FullGrowthBriefing) -> str:
    """Render a publication-grade, luxury executive growth briefing email."""
    client_name = html.escape(briefing.company_name)
    period = html.escape(briefing.period_label)
    branding = briefing.branding
    primary_color = branding.get("primary_color", "#0A0A0B")
    secondary_color = branding.get("secondary_color", "#F7F4EE")
    accent_color = branding.get("accent_color", "#C6A15B")
    logo_url = branding.get("logo_url")

    primary_light = is_light_color(primary_color)
    header_title_color = "#18181B" if primary_light else "#FFFFFF"
    header_subtitle_color = "rgba(0,0,0,0.6)" if primary_light else "rgba(255,255,255,0.7)"
    accent_gold = accent_color if accent_color else "#C6A15B"

    analytics = briefing.analytics
    insights = briefing.insights

    # 1. Metric Cards Grid (Editorial KPI Blocks)
    metrics_cards_html = ""
    num_metrics = len(analytics.core_metrics) or 1
    col_width_pct = max(18, min(33, int(100 / num_metrics)))

    for m in analytics.core_metrics:
        val_str = f"{int(m.current_value):,}" if m.unit == "count" else f"{m.current_value:.1f}%"
        
        if m.direction == "up":
            arrow = "&#8593;"
            trend_badge_bg = "#ECFDF5"
            trend_badge_color = "#047857"
            trend_border = "#A7F3D0"
        elif m.direction == "down":
            arrow = "&#8595;"
            trend_badge_bg = "#FEF2F2"
            trend_badge_color = "#B91C1C"
            trend_border = "#FECACA"
        else:
            arrow = "&rarr;"
            trend_badge_bg = "#F3F4F6"
            trend_badge_color = "#4B5563"
            trend_border = "#E5E7EB"

        if m.is_percentage_rate and m.percentage_points_change is not None:
            pct_str = f"{m.percentage_points_change:+.1f}% pts"
        elif m.percentage_change is not None:
            pct_str = f"{m.percentage_change:+.1f}%"
        else:
            pct_str = "stable"

        disp_name = html.escape(m.display_name)

        metrics_cards_html += f"""
        <td style="width: {col_width_pct}%; padding: 0 4px; vertical-align: top;">
          <div style="background: #FFFFFF; border: 1px solid #E4E4E7; border-radius: 6px; padding: 14px 10px; text-align: left; height: 100%;">
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: #71717A; font-weight: 600; margin-bottom: 6px;">{disp_name}</div>
            <div style="font-family: Georgia, 'Times New Roman', serif; font-size: 20px; font-weight: 700; color: #09090B; letter-spacing: -0.02em; margin-bottom: 8px;">{val_str}</div>
            <div style="display: inline-block; background: {trend_badge_bg}; border: 1px solid {trend_border}; color: {trend_badge_color}; font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 4px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
              {arrow} {pct_str}
            </div>
          </div>
        </td>
        """

    # 2. Executive Snapshot Takeaways (Numbered Editorial List)
    exec_summary_html = ""
    for idx, item in enumerate(insights.executive_summary, 1):
        escaped_item = html.escape(item)
        exec_summary_html += f"""
        <tr>
          <td style="vertical-align: top; width: 28px; padding: 8px 0;">
            <div style="width: 22px; height: 22px; border-radius: 50%; background: {accent_gold}; color: #FFFFFF; font-family: Georgia, serif; font-size: 11px; font-weight: 700; line-height: 22px; text-align: center;">{idx}</div>
          </td>
          <td style="vertical-align: middle; padding: 8px 0 8px 10px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 13.5px; color: #27272A; line-height: 1.55;">
            {escaped_item}
          </td>
        </tr>
        """

    # 3. Striking Distance Keywords Rows
    kw_rows_html = ""
    for kw in analytics.striking_distance_keywords[:5]:
        kw_query = html.escape(kw.query)
        kw_rows_html += f"""
        <tr style="border-bottom: 1px solid #F4F4F5;">
          <td style="padding: 11px 14px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-weight: 600; color: #18181B; font-size: 13px;">
            {kw_query}
          </td>
          <td style="padding: 11px 14px; text-align: center; color: #52525B; font-size: 12.5px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
            {kw.impressions:,}
          </td>
          <td style="padding: 11px 14px; text-align: center; color: #18181B; font-size: 12.5px; font-weight: 600; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
            {kw.position:.1f}
          </td>
          <td style="padding: 11px 14px; text-align: right; color: {accent_gold}; font-weight: 700; font-size: 13px; font-family: Georgia, serif;">
            {kw.opportunity_score:.0f}
          </td>
        </tr>
        """
    if not kw_rows_html:
        kw_rows_html = '<tr><td colspan="4" style="padding: 16px; text-align: center; color: #A1A1AA; font-size: 13px;">No striking-distance queries recorded for this cycle.</td></tr>'

    # 4. Autonomous Deep Discoveries (Executive Callouts)
    discoveries_html = ""
    if insights.deep_discoveries:
        for disc in insights.deep_discoveries:
            disc_title = html.escape(disc.title)
            disc_source = html.escape(disc.source)
            disc_insight = html.escape(disc.insight)
            disc_rec = html.escape(disc.recommended_action)
            discoveries_html += f"""
            <div style="background: #FFFFFF; border: 1px solid #E4E4E7; border-left: 3px solid {accent_gold}; border-radius: 4px; padding: 16px 18px; margin-bottom: 14px;">
              <table role="presentation" style="width: 100%; border-collapse: collapse; margin-bottom: 8px;">
                <tr>
                  <td style="vertical-align: middle;">
                    <strong style="font-family: Georgia, 'Times New Roman', serif; color: #09090B; font-size: 14.5px; font-weight: 700;">{disc_title}</strong>
                  </td>
                  <td style="text-align: right; vertical-align: middle;">
                    <span style="display: inline-block; background: #F4F4F5; color: #52525B; border: 1px solid #E4E4E7; padding: 2px 8px; border-radius: 3px; font-size: 10.5px; font-weight: 600; letter-spacing: 0.03em; text-transform: uppercase;">{disc_source}</span>
                  </td>
                </tr>
              </table>
              <p style="margin: 0 0 10px 0; color: #52525B; font-size: 13px; line-height: 1.5; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">{disc_insight}</p>
              <div style="font-size: 12px; color: #18181B; background: #FAFAFA; border: 1px dashed #D4D4D8; border-radius: 4px; padding: 8px 12px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
                <span style="color: {accent_gold}; font-weight: 700; text-transform: uppercase; font-size: 10.5px; letter-spacing: 0.05em;">Action:</span> {disc_rec}
              </div>
            </div>
            """

    # 5. Agency Action Plan (Prioritized Growth Deliverables)
    action_items_html = ""
    for act in insights.agency_action_plan:
        act_title = html.escape(act.title)
        act_desc = html.escape(act.description)
        act_priority = html.escape(act.priority)
        act_area = html.escape(act.impact_area)
        
        if act.priority.lower() == "high":
            p_badge_style = f"background: {accent_gold}; color: #FFFFFF;"
        else:
            p_badge_style = "background: #F4F4F5; color: #52525B; border: 1px solid #E4E4E7;"

        action_items_html += f"""
        <div style="background: #FFFFFF; border: 1px solid #E4E4E7; border-radius: 4px; padding: 16px 18px; margin-bottom: 12px;">
          <table role="presentation" style="width: 100%; border-collapse: collapse; margin-bottom: 6px;">
            <tr>
              <td style="vertical-align: middle;">
                <strong style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; color: #09090B; font-size: 14px; font-weight: 700;">{act_title}</strong>
              </td>
              <td style="text-align: right; vertical-align: middle; white-space: nowrap;">
                <span style="font-size: 11px; color: #71717A; margin-right: 6px; font-weight: 500;">{act_area}</span>
                <span style="{p_badge_style} padding: 2px 8px; border-radius: 3px; font-size: 10.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;">{act_priority}</span>
              </td>
            </tr>
          </table>
          <p style="margin: 0; color: #52525B; font-size: 13px; line-height: 1.5; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">{act_desc}</p>
        </div>
        """

    logo_markup = f'<img src="{html.escape(logo_url)}" alt="{client_name}" style="max-height: 32px; max-width: 160px; margin-bottom: 14px; display: block;" />' if logo_url else ""
    traffic_insights_escaped = html.escape(insights.traffic_and_inflow_insights)
    local_insights_escaped = html.escape(insights.local_seo_insights) if insights.local_seo_insights else ""

    discoveries_section = f"""<!-- Section 5: Autonomous Multi-Source Deep Discoveries -->
          <tr>
            <td style="padding: 0 32px 24px 32px;">
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_gold}; font-weight: 700; margin-bottom: 12px;">Autonomous Deep Discoveries</div>
              {discoveries_html}
            </td>
          </tr>""" if discoveries_html else ""

    local_section = f"""<!-- Section 6: Local SEO Dynamics (if present) -->
          <tr>
            <td style="padding: 0 32px 24px 32px;">
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_gold}; font-weight: 700; margin-bottom: 10px;">Local Market &amp; Reputation</div>
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 13.5px; color: #3F3F46; line-height: 1.6; background: #FAFAFA; border: 1px solid #E4E4E7; border-radius: 4px; padding: 16px 18px;">
                {local_insights_escaped}
              </div>
            </td>
          </tr>""" if local_insights_escaped else ""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{client_name} &bull; Monthly Growth Briefing</title>
</head>
<body style="margin: 0; padding: 0; background-color: #F4F4F5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; color: #18181B;">
  <table role="presentation" style="width: 100%; border-collapse: collapse; background-color: #F4F4F5; padding: 32px 0;">
    <tr>
      <td align="center" style="padding: 24px 12px;">
        <table role="presentation" style="width: 100%; max-width: 640px; border-collapse: collapse; background-color: #FFFFFF; border-radius: 8px; overflow: hidden; border: 1px solid #E4E4E7; box-shadow: 0 4px 16px -2px rgba(0, 0, 0, 0.06);">
          
          <!-- Editorial Top Banner -->
          <tr>
            <td style="background-color: {primary_color}; padding: 36px 32px; color: {header_title_color}; border-bottom: 2px solid {accent_gold};">
              {logo_markup}
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.14em; color: {accent_gold}; font-weight: 700; margin-bottom: 8px;">Monthly Intelligence Briefing</div>
              <h1 style="margin: 0 0 6px 0; font-family: Georgia, 'Times New Roman', serif; font-size: 26px; font-weight: 700; letter-spacing: -0.01em; color: {header_title_color}; line-height: 1.2;">{client_name}</h1>
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 12.5px; color: {header_subtitle_color};">{period}</div>
            </td>
          </tr>

          <!-- Section 1: Executive Snapshot -->
          <tr>
            <td style="padding: 32px 32px 20px 32px;">
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_gold}; font-weight: 700; margin-bottom: 12px;">Executive Overview</div>
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                {exec_summary_html}
              </table>
            </td>
          </tr>

          <!-- Section 2: Core Growth Metrics -->
          <tr>
            <td style="padding: 0 28px 28px 28px;">
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                <tr>
                  {metrics_cards_html}
                </tr>
              </table>
            </td>
          </tr>

          <!-- Divider -->
          <tr>
            <td style="padding: 0 32px;">
              <hr style="border: 0; border-top: 1px solid #E4E4E7; margin: 0;" />
            </td>
          </tr>

          <!-- Section 3: Inflow & Channel Dynamics -->
          <tr>
            <td style="padding: 28px 32px;">
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_gold}; font-weight: 700; margin-bottom: 10px;">Traffic &amp; Inflow Dynamics</div>
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 13.5px; color: #3F3F46; line-height: 1.6; background: #FAFAFA; border: 1px solid #E4E4E7; border-radius: 4px; padding: 16px 18px;">
                {traffic_insights_escaped}
              </div>
            </td>
          </tr>

          <!-- Section 4: Striking Distance Keyword Opportunities -->
          <tr>
            <td style="padding: 0 32px 28px 32px;">
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_gold}; font-weight: 700; margin-bottom: 6px;">Striking-Distance Search Opportunities</div>
              <p style="margin: 0 0 12px 0; font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 12.5px; color: #71717A;">Queries ranking on page 2 (positions 8&ndash;20) poised for page 1 breakthrough with targeted on-page optimization:</p>
              <table role="presentation" style="width: 100%; border-collapse: collapse; border: 1px solid #E4E4E7; border-radius: 4px; overflow: hidden;">
                <thead>
                  <tr style="background-color: #FAFAFA; border-bottom: 1px solid #E4E4E7;">
                    <th style="padding: 10px 14px; text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Target Search Query</th>
                    <th style="padding: 10px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Impressions</th>
                    <th style="padding: 10px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #71717A;">Avg Rank</th>
                    <th style="padding: 10px 14px; text-align: right; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: {accent_gold};">Opp. Score</th>
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

          <!-- Section 7: Strategic Growth Action Plan -->
          <tr>
            <td style="padding: 0 32px 32px 32px;">
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: {accent_gold}; font-weight: 700; margin-bottom: 12px;">Monthly Action Plan &amp; Retainer Deliverables</div>
              {action_items_html}
            </td>
          </tr>

          <!-- Editorial Footer -->
          <tr>
            <td style="background-color: #FAFAFA; border-top: 1px solid #E4E4E7; padding: 24px 32px; text-align: center;">
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 12px; color: #71717A; margin-bottom: 4px;">
                A comprehensive executive PDF report with full technical appendices is attached.
              </div>
              <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 11px; color: #A1A1AA;">
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
