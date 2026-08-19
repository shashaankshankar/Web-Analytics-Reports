from __future__ import annotations
import html

from app.analytics.contracts import FullGrowthBriefing


def render_growth_email_html(briefing: FullGrowthBriefing) -> str:
    """Render a modern, responsive HTML growth briefing email."""
    client_name = briefing.company_name
    period = briefing.period_label
    branding = briefing.branding
    primary_color = branding.get("primary_color", "#1E3A8A")
    secondary_color = branding.get("secondary_color", "#3B82F6")
    accent_color = branding.get("accent_color", "#F59E0B")

    analytics = briefing.analytics
    insights = briefing.insights

    # Metric cards HTML
    metrics_cards_html = ""
    for m in analytics.core_metrics:
        val_str = f"{int(m.current_value):,}" if m.unit == "count" else f"{m.current_value:.1f}%"
        change_color = "#10B981" if m.direction == "up" else ("#EF4444" if m.direction == "down" else "#6B7280")
        pct_str = f"{m.percentage_change:+.1f}%" if m.percentage_change is not None else "stable"
        
        metrics_cards_html += f"""
        <td style="width: 25%; padding: 8px; vertical-align: top;">
          <div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 16px; text-align: center;">
            <div style="font-size: 11px; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; margin-bottom: 4px;">{m.display_name}</div>
            <div style="font-size: 22px; font-weight: 700; color: #0F172A; margin-bottom: 4px;">{val_str}</div>
            <div style="font-size: 12px; font-weight: 600; color: {change_color};">{pct_str} <span style="color: #94A3B8; font-weight: normal;">vs prior</span></div>
          </div>
        </td>
        """

    # Exec summary list HTML
    exec_summary_html = ""
    for item in insights.executive_summary:
        exec_summary_html += f"""
        <li style="margin-bottom: 10px; color: #334155; line-height: 1.5; font-size: 14px;">
          {item}
        </li>
        """

    # Striking keywords table rows
    kw_rows_html = ""
    for kw in analytics.striking_distance_keywords[:5]:
        kw_rows_html += f"""
        <tr style="border-bottom: 1px solid #F1F5F9;">
          <td style="padding: 10px 12px; font-weight: 600; color: #1E293B; font-size: 13px;">{kw.query}</td>
          <td style="padding: 10px 12px; text-align: center; color: #475569; font-size: 13px;">{kw.impressions:,}</td>
          <td style="padding: 10px 12px; text-align: center; color: #475569; font-size: 13px;">{kw.position:.1f}</td>
          <td style="padding: 10px 12px; text-align: right; color: #2563EB; font-weight: 600; font-size: 13px;">{kw.opportunity_score:.0f}</td>
        </tr>
        """
    if not kw_rows_html:
        kw_rows_html = '<tr><td colspan="4" style="padding: 12px; text-align: center; color: #94A3B8; font-size: 13px;">No striking-distance queries recorded.</td></tr>'

    # Deep discoveries HTML
    discoveries_html = ""
    if insights.deep_discoveries:
        for disc in insights.deep_discoveries:
            disc_title = html.escape(disc.title)
            disc_source = html.escape(disc.source)
            disc_insight = html.escape(disc.insight)
            disc_rec = html.escape(disc.recommended_action)
            discoveries_html += f"""
            <div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-left: 4px solid {accent_color}; border-radius: 6px; padding: 14px 16px; margin-bottom: 12px;">
              <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;">
                <strong style="color: #0F172A; font-size: 14px;">{disc_title}</strong>
                <span style="background-color: #EEF2FF; color: {secondary_color}; padding: 2px 8px; border-radius: 9999px; font-size: 11px; font-weight: 600;">{disc_source}</span>
              </div>
              <p style="margin: 0 0 8px 0; color: #475569; font-size: 13px; line-height: 1.4;">{disc_insight}</p>
              <div style="font-size: 12px; color: #1E293B; background: #FFFFFF; border: 1px dashed #CBD5E1; border-radius: 4px; padding: 6px 10px;">
                <strong>Recommended Action:</strong> {disc_rec}
              </div>
            </div>
            """

    # Action items HTML
    action_items_html = ""
    for act in insights.agency_action_plan:
        priority_badge = f'<span style="background-color: #FEF3C7; color: #92400E; padding: 2px 8px; border-radius: 9999px; font-size: 11px; font-weight: 600; margin-left: 8px;">{act.priority} Priority</span>'
        action_items_html += f"""
        <div style="background-color: #FFFFFF; border: 1px solid #E2E8F0; border-left: 4px solid {primary_color}; border-radius: 6px; padding: 14px 16px; margin-bottom: 12px;">
          <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;">
            <strong style="color: #0F172A; font-size: 14px;">{act.title}</strong>
            {priority_badge}
          </div>
          <p style="margin: 0; color: #475569; font-size: 13px; line-height: 1.4;">{act.description}</p>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{client_name} Growth Briefing</title>
</head>
<body style="margin: 0; padding: 0; background-color: #F1F5F9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased;">
  <table role="presentation" style="width: 100%; border-collapse: collapse; background-color: #F1F5F9; padding: 24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" style="width: 100%; max-width: 640px; border-collapse: collapse; background-color: #FFFFFF; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); margin: 20px 10px;">
          
          <!-- Header Banner -->
          <tr>
            <td style="background: linear-gradient(135deg, {primary_color} 0%, {secondary_color} 100%); padding: 32px 28px; color: #FFFFFF;">
              <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; opacity: 0.85; font-weight: 700; margin-bottom: 8px;">Monthly Performance Report</div>
              <h1 style="margin: 0 0 6px 0; font-size: 24px; font-weight: 800; letter-spacing: -0.02em;">{client_name}</h1>
              <div style="font-size: 13px; opacity: 0.9;">{period}</div>
            </td>
          </tr>

          <!-- Executive Snapshot -->
          <tr>
            <td style="padding: 28px 28px 16px 28px;">
              <h2 style="margin: 0 0 14px 0; font-size: 16px; font-weight: 700; color: #0F172A; text-transform: uppercase; letter-spacing: 0.05em;">Executive Snapshot</h2>
              <ul style="margin: 0; padding-left: 20px;">
                {exec_summary_html}
              </ul>
            </td>
          </tr>

          <!-- Core Metrics Grid -->
          <tr>
            <td style="padding: 0 20px 24px 20px;">
              <table role="presentation" style="width: 100%; border-collapse: collapse;">
                <tr>
                  {metrics_cards_html}
                </tr>
              </table>
            </td>
          </tr>

          <!-- Traffic & Inflow Insights -->
          <tr>
            <td style="padding: 0 28px 24px 28px;">
              <h2 style="margin: 0 0 12px 0; font-size: 16px; font-weight: 700; color: #0F172A;">Traffic & Inflow Dynamics</h2>
              <p style="margin: 0; color: #475569; font-size: 14px; line-height: 1.6; background-color: #F8FAFC; padding: 14px; border-radius: 8px; border: 1px solid #E2E8F0;">
                {insights.traffic_and_inflow_insights}
              </p>
            </td>
          </tr>

          <!-- Striking Distance SEO Keywords -->
          <tr>
            <td style="padding: 0 28px 24px 28px;">
              <h2 style="margin: 0 0 12px 0; font-size: 16px; font-weight: 700; color: #0F172A;">Striking-Distance Search Opportunities</h2>
              <p style="margin: 0 0 12px 0; color: #64748B; font-size: 13px;">High-impression search queries currently ranking on page 2 (positions 8-20) targeted for page 1 breakthrough:</p>
              <table role="presentation" style="width: 100%; border-collapse: collapse; border: 1px solid #E2E8F0; border-radius: 8px; overflow: hidden;">
                <thead>
                  <tr style="background-color: #F8FAFC; border-bottom: 1px solid #E2E8F0;">
                    <th style="padding: 10px 12px; text-align: left; font-size: 12px; font-weight: 600; color: #475569;">Target Search Query</th>
                    <th style="padding: 10px 12px; text-align: center; font-size: 12px; font-weight: 600; color: #475569;">Impressions</th>
                    <th style="padding: 10px 12px; text-align: center; font-size: 12px; font-weight: 600; color: #475569;">Avg Pos</th>
                    <th style="padding: 10px 12px; text-align: right; font-size: 12px; font-weight: 600; color: #475569;">Opp. Score</th>
                  </tr>
                </thead>
                <tbody>
                  {kw_rows_html}
                </tbody>
              </table>
            </td>
          </tr>

          {f'''<!-- Deep Discoveries -->
          <tr>
            <td style="padding: 0 28px 24px 28px;">
              <h2 style="margin: 0 0 12px 0; font-size: 16px; font-weight: 700; color: #0F172A;">Autonomous Multi-Source Deep Discoveries</h2>
              {discoveries_html}
            </td>
          </tr>''' if discoveries_html else ''}

          <!-- Agency Action Items -->
          <tr>
            <td style="padding: 0 28px 28px 28px;">
              <h2 style="margin: 0 0 14px 0; font-size: 16px; font-weight: 700; color: #0F172A;">Agency Growth Action Plan</h2>
              {action_items_html}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color: #F8FAFC; border-top: 1px solid #E2E8F0; padding: 20px 28px; text-align: center;">
              <div style="font-size: 12px; color: #64748B; margin-bottom: 4px;">Attached is your PDF performance summary.</div>
              <div style="font-size: 11px; color: #94A3B8;">Confidential report prepared for {client_name}</div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
