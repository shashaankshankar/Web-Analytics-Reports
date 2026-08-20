from __future__ import annotations
import html
from app.analytics.contracts import FullGrowthBriefing, ReportType
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

def render_growth_email_html(briefing: FullGrowthBriefing) -> str:
    """Render a publication-grade, editorial executive performance briefing email incorporating client branding."""
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
    insights = briefing.insights
    report_name = "Monthly Intelligence Briefing" if briefing.report_type == ReportType.PERFORMANCE_28D else f"{analytics.period_days}-Day Performance Report"

    # 1. Metric Cards Grid
    metrics_cards_html = ""
    num_metrics = len(analytics.core_metrics) or 1
    col_width_pct = max(18, min(33, int(100 / num_metrics)))
    for m in analytics.core_metrics:
        metrics_cards_html += f"""
        <td style="width: {col_width_pct}%; padding: 0 4px; vertical-align: top;">
          {render_kpi_card(m, primary_color, accent_color)}
        </td>
        """

    # 2. Executive Snapshot Takeaways (Numbered Editorial List)
    exec_summary_html = ""
    for idx, item in enumerate(insights.executive_summary, 1):
        escaped_item = html.escape(item)
        exec_summary_html += f"""
        <tr>
          <td style="vertical-align: top; width: 36px; padding: 10px 0;">
            <div style="width: 24px; height: 24px; border-radius: 50%; border: 1.5px solid {primary_color}; color: {primary_color}; font-family: {FONT_FAMILY_MAIN}; font-size: 12px; font-weight: 700; line-height: 24px; text-align: center;">{idx}</div>
          </td>
          <td style="vertical-align: middle; padding: 10px 0 10px 8px; font-family: {FONT_FAMILY_MAIN}; font-size: 14px; color: #45464D; line-height: 1.55;">
            {escaped_item}
          </td>
        </tr>
        """

    # 3. Decision Highlights: Biggest Win & Watch Item
    biggest_win_block = ""
    if insights.biggest_win:
        biggest_win_block = f"""
        <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-left: 4px solid #16A34A; border-radius: 2px; padding: 16px 18px; margin-top: 14px;">
          <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #16A34A; font-weight: 700; margin-bottom: 6px;">&#9733; Biggest Win</div>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #191C1E; line-height: 1.5;">{html.escape(insights.biggest_win)}</p>
        </div>
        """

    watch_item_block = ""
    if insights.watch_item:
        watch_item_block = f"""
        <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-left: 4px solid #BA1A1A; border-radius: 2px; padding: 16px 18px; margin-top: 12px;">
          <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #BA1A1A; font-weight: 700; margin-bottom: 6px;">&#9888; Area to Improve</div>
          <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #191C1E; line-height: 1.5;">{html.escape(insights.watch_item)}</p>
        </div>
        """

    # 4. Conversion Breakdown Table
    conv_rows_html = ""
    for ce in analytics.conversion_events[:4]:
        ev_name = html.escape(ce.display_name)
        pct_str = f"{ce.percentage_change:+.1f}%" if ce.percentage_change is not None else "-"
        if ce.direction == "up":
            dir_icon = "&#8593;"
            color_style = "color: #16A34A;"
        elif ce.direction == "down":
            dir_icon = "&#8595;"
            color_style = "color: #BA1A1A;"
        else:
            dir_icon = "&rarr;"
            color_style = "color: #515F74;"
        conv_rows_html += f"""
        <tr style="border-bottom: 1px solid rgba(198, 198, 205, 0.3);">
          <td style="padding: 12px 14px; font-family: {FONT_FAMILY_MAIN}; font-weight: 600; color: #191C1E; font-size: 13.5px;">{ev_name}</td>
          <td style="padding: 12px 14px; text-align: center; color: #191C1E; font-size: 13.5px; font-weight: 600; font-family: {FONT_FAMILY_MAIN};">{ce.current_count:,}</td>
          <td style="padding: 12px 14px; text-align: center; color: #515F74; font-size: 13px; font-family: {FONT_FAMILY_MAIN};">{ce.prior_count:,}</td>
          <td style="padding: 12px 14px; text-align: right; {color_style} font-weight: 600; font-size: 13px; font-family: {FONT_FAMILY_MAIN};">{dir_icon} {pct_str}</td>
        </tr>
        """

    conv_section = ""
    if conv_rows_html:
        conv_insights_escaped = html.escape(insights.conversion_insights) if insights.conversion_insights else ""
        conv_commentary = f'<p style="font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; color: #45464D; line-height: 1.55; margin: 0 0 14px 0;">{conv_insights_escaped}</p>' if conv_insights_escaped else ""
        conv_section = f"""
        <!-- Section: Key Inquiries & Actions -->
        <tr>
          <td style="padding: 24px 32px;">
            <h2 style="margin: 0 0 14px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 20px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Customer Inquiries &amp; Key Actions</h2>
            {conv_commentary}
            <table role="presentation" style="width: 100%; border-collapse: collapse; background-color: #FFFFFF; border: 1px solid rgba(198, 198, 205, 0.4); border-radius: 2px; overflow: hidden;">
              <thead>
                <tr style="background-color: #F7F9FB; border-bottom: 1px solid rgba(198, 198, 205, 0.4);">
                  <th style="padding: 10px 14px; text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #515F74; font-family: {FONT_FAMILY_MAIN};">Action / Goal</th>
                  <th style="padding: 10px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #515F74; font-family: {FONT_FAMILY_MAIN};">This Period</th>
                  <th style="padding: 10px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #515F74; font-family: {FONT_FAMILY_MAIN};">Prior Period</th>
                  <th style="padding: 10px 14px; text-align: right; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #515F74; font-family: {FONT_FAMILY_MAIN};">Change</th>
                </tr>
              </thead>
              <tbody>
                {conv_rows_html}
              </tbody>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding: 0 32px;">
            <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
          </td>
        </tr>
        """

    # 5. Striking Distance Keywords Rows
    kw_rows_html = ""
    for kw in analytics.striking_distance_keywords[:5]:
        kw_query = html.escape(kw.query)
        kw_rows_html += f"""
        <tr style="border-bottom: 1px solid rgba(198, 198, 205, 0.3);">
          <td style="padding: 12px 14px; font-family: {FONT_FAMILY_MAIN}; font-weight: 600; color: #191C1E; font-size: 13.5px;">
            {kw_query}
          </td>
          <td style="padding: 12px 14px; text-align: center; color: #515F74; font-size: 13px; font-family: {FONT_FAMILY_MAIN};">
            {kw.impressions:,}
          </td>
          <td style="padding: 12px 14px; text-align: center; color: #191C1E; font-size: 13px; font-weight: 600; font-family: {FONT_FAMILY_MAIN};">
            {kw.position:.1f}
          </td>
          <td style="padding: 12px 14px; text-align: right; color: {accent_color}; font-weight: 700; font-size: 13.5px; font-family: {FONT_FAMILY_MAIN};">
            {kw.opportunity_score:.0f}
          </td>
        </tr>
        """
    if not kw_rows_html:
        kw_rows_html = '<tr><td colspan="4" style="padding: 16px; text-align: center; color: #515F74; font-size: 13px; font-family: ' + FONT_FAMILY_MAIN + ';">No high-opportunity search terms found for this cycle.</td></tr>'

    # 6. Autonomous Deep Discoveries
    discoveries_html = ""
    if insights.deep_discoveries:
        for disc in insights.deep_discoveries:
            disc_title = html.escape(disc.title)
            disc_source = html.escape(disc.source)
            disc_insight = html.escape(disc.insight)
            disc_rec = html.escape(disc.recommended_action)
            discoveries_html += f"""
            <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-left: 4px solid {accent_color}; border-radius: 2px; padding: 18px 20px; margin-bottom: 16px;">
              <table role="presentation" style="width: 100%; border-collapse: collapse; margin-bottom: 8px;">
                <tr>
                  <td style="vertical-align: middle;">
                    <span style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #515F74; font-weight: 700; display: block; margin-bottom: 4px;">{disc_source}</span>
                    <h3 style="margin: 0; font-family: {FONT_FAMILY_MAIN}; color: #191C1E; font-size: 16px; font-weight: 700; line-height: 1.3;">{disc_title}</h3>
                  </td>
                </tr>
              </table>
              <p style="margin: 0 0 12px 0; color: #45464D; font-size: 13.5px; line-height: 1.55; font-family: {FONT_FAMILY_MAIN};">{disc_insight}</p>
              <div style="font-size: 12.5px; color: #191C1E; background: #F7F9FB; border: 1px dashed #C6C6CD; border-radius: 2px; padding: 10px 14px; font-family: {FONT_FAMILY_MAIN};">
                <span style="color: {accent_color}; font-weight: 700; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em;">Next Step:</span> {disc_rec}
              </div>
            </div>
            """

    # 7. Prioritized Agency Action Plan
    action_items_html = ""
    num_actions = len(insights.agency_action_plan)
    for idx, act in enumerate(insights.agency_action_plan):
        is_last = (idx == num_actions - 1)
        action_items_html += render_action_card(act, primary_color, accent_color, is_last=is_last)

    logo_markup = f'<img src="{html.escape(logo_url)}" alt="{client_name}" height="52" style="height: 52px; width: auto; max-width: 260px; max-height: 56px; display: block; border: 0;" />' if logo_url else f'<span style="font-family: {FONT_FAMILY_SERIF}; font-size: 22px; font-weight: 700; color: {header_text_color}; letter-spacing: -0.01em;">{client_name}</span>'
    traffic_insights_escaped = html.escape(insights.traffic_and_inflow_insights)
    local_insights_escaped = html.escape(insights.local_seo_insights) if insights.local_seo_insights else ""

    discoveries_section = f"""<!-- Section: Key Opportunities & Discoveries -->
          <tr>
            <td style="padding: 24px 32px;">
              <h2 style="margin: 0 0 16px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 20px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Key Opportunities &amp; Discoveries</h2>
              {discoveries_html}
            </td>
          </tr>
          <tr>
            <td style="padding: 0 32px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>""" if discoveries_html else ""

    local_section = f"""<!-- Section: Local Reputation & Maps -->
          <tr>
            <td style="padding: 24px 32px;">
              <h2 style="margin: 0 0 12px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 20px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Local Reputation &amp; Maps</h2>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 14px; color: #45464D; line-height: 1.6; background: #FFFFFF; border: 1px solid #E0E3E5; border-radius: 2px; padding: 18px 20px;">
                {local_insights_escaped}
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding: 0 32px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>""" if local_insights_escaped else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{client_name} &bull; {report_name}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&family=Work+Sans:wght@400;600;700&display=swap" rel="stylesheet">
</head>
<body style="margin: 0; padding: 0; background-color: #F7F9FB; font-family: {FONT_FAMILY_MAIN}; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; color: #191C1E;">
  <table role="presentation" style="width: 100%; border-collapse: collapse; background-color: #F7F9FB; padding: 32px 0;">
    <tr>
      <td align="center" style="padding: 24px 12px;">
        <table role="presentation" style="width: 100%; max-width: 680px; border-collapse: collapse; background-color: #FFFFFF; border-radius: 2px; overflow: hidden; border: 1px solid #E0E3E5; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);">
          
          <!-- Top Navigation / Branding Bar -->
          <tr>
            <td style="background-color: {primary_color}; padding: 18px 32px; border-bottom: 2px solid {accent_color};">
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                <tr>
                  <td style="vertical-align: middle;">
                    {logo_markup}
                  </td>
                  <td style="text-align: right; vertical-align: middle;">
                    <span style="display: inline-block; background-color: {pill_bg}; color: {pill_text}; padding: 6px 12px; border-radius: 2px; font-family: {FONT_FAMILY_MAIN}; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;">
                      Executive Report
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Header Section -->
          <tr>
            <td style="padding: 32px 32px 24px 32px;">
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: #515F74; font-weight: 700; margin-bottom: 8px;">
                Confidential Report &bull; {period}
              </div>
              <h1 style="margin: 0 0 12px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 32px; font-weight: 700; letter-spacing: -0.02em; color: {primary_color}; line-height: 1.2;">
                {report_name}
              </h1>
              <p style="margin: 0; font-family: {FONT_FAMILY_MAIN}; font-size: 16px; color: #45464D; line-height: 1.55;">
                A clear summary of website visitors, customer inquiries, and growth opportunities for <strong style="color: #191C1E;">{client_name}</strong>.
              </p>
            </td>
          </tr>

          <!-- Editorial Divider -->
          <tr>
            <td style="padding: 0 32px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>

          <!-- Section: Executive Overview -->
          <tr>
            <td style="padding: 28px 32px 24px 32px;">
              <h2 style="margin: 0 0 16px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 20px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Executive Overview</h2>
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                {exec_summary_html}
              </table>
              {biggest_win_block}
              {watch_item_block}
            </td>
          </tr>

          <!-- Editorial Divider -->
          <tr>
            <td style="padding: 0 32px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>

          <!-- Section: Core Metrics -->
          <tr>
            <td style="padding: 28px 32px 24px 32px;">
              <h2 style="margin: 0 0 16px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 20px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Core Growth Metrics</h2>
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                <tr>
                  {metrics_cards_html}
                </tr>
              </table>
            </td>
          </tr>

          <!-- Editorial Divider -->
          <tr>
            <td style="padding: 0 32px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>

          {conv_section}

          <!-- Section: Inflow & Channel Dynamics -->
          <tr>
            <td style="padding: 24px 32px;">
              <h2 style="margin: 0 0 12px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 20px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Visitor Inflow &amp; Popular Pages</h2>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 14px; color: #45464D; line-height: 1.6; background: #F7F9FB; border: 1px solid #E0E3E5; border-radius: 2px; padding: 18px 20px;">
                {traffic_insights_escaped}
              </div>
            </td>
          </tr>

          <!-- Editorial Divider -->
          <tr>
            <td style="padding: 0 32px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>

          <!-- Section: Striking Distance Search Opportunities -->
          <tr>
            <td style="padding: 24px 32px;">
              <h2 style="margin: 0 0 8px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 20px; font-weight: 600; color: {primary_color}; line-height: 1.3;">High-Opportunity Google Searches</h2>
              <p style="margin: 0 0 14px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: #515F74;">Key searches where your website currently ranks on page 2 and can reach page 1 with targeted content updates:</p>
              <table role="presentation" style="width: 100%; border-collapse: collapse; background-color: #FFFFFF; border: 1px solid rgba(198, 198, 205, 0.4); border-radius: 2px; overflow: hidden;">
                <thead>
                  <tr style="background-color: #F7F9FB; border-bottom: 1px solid rgba(198, 198, 205, 0.4);">
                    <th style="padding: 10px 14px; text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #515F74; font-family: {FONT_FAMILY_MAIN};">Search Term</th>
                    <th style="padding: 10px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #515F74; font-family: {FONT_FAMILY_MAIN};">Search Views</th>
                    <th style="padding: 10px 14px; text-align: center; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #515F74; font-family: {FONT_FAMILY_MAIN};">Google Rank</th>
                    <th style="padding: 10px 14px; text-align: right; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: {accent_color}; font-family: {FONT_FAMILY_MAIN};">Opportunity</th>
                  </tr>
                </thead>
                <tbody>
                  {kw_rows_html}
                </tbody>
              </table>
            </td>
          </tr>

          <!-- Editorial Divider -->
          <tr>
            <td style="padding: 0 32px;">
              <div style="height: 1px; background-color: #000000; opacity: 0.15; width: 100%;"></div>
            </td>
          </tr>

          {discoveries_section}
          {local_section}

          <!-- Section: Strategic Action Plan -->
          <tr>
            <td style="padding: 24px 32px 32px 32px;">
              <h2 style="margin: 0 0 16px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 20px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Recommended Next Actions</h2>
              <div style="background-color: #FFFFFF; border: 1px solid #E0E3E5; border-radius: 2px; overflow: hidden;">
                {action_items_html}
              </div>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color: #FFFFFF; border-top: 1px solid rgba(0, 0, 0, 0.12); padding: 24px 32px; text-align: center;">
              <div style="display: inline-block; background: #F2F4F6; border: 1px solid #E0E3E5; border-left: 3px solid {accent_color}; padding: 6px 14px; border-radius: 2px; font-family: {FONT_FAMILY_MAIN}; font-size: 11.5px; font-weight: 600; color: #191C1E; margin-bottom: 12px;">
                &#128206; Detailed Executive PDF Report Attached
              </div>
              <p style="margin: 0 0 12px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 12px; color: #515F74;">
                &copy; 2026 {client_name} &bull; Confidential Executive Report.
              </p>
              <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: #515F74;">
                <a href="#" style="color: #515F74; text-decoration: none;">Privacy Policy</a> &nbsp;|&nbsp; <a href="#" style="color: #515F74; text-decoration: none;">Contact Support</a> &nbsp;|&nbsp; <a href="#" style="color: #515F74; text-decoration: none;">Unsubscribe</a>
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

