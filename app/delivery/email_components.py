from __future__ import annotations
import html
from typing import Any, Dict, List, Optional
from app.analytics.contracts import ActionItem, MetricDelta, PagePerformance, StrikingDistanceKeyword

# Centralized visual design tokens for consistent, mobile-first email layouts
STYLE_CARD = "background: #FFFFFF; border: 1px solid #E4E4E7; border-radius: 8px; padding: 16px 14px; text-align: left;"
STYLE_CALLOUT = "background: #FAFAFA; border: 1px solid #E4E4E7; border-radius: 6px; padding: 14px 16px;"
FONT_FAMILY_MAIN = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
FONT_FAMILY_SERIF = "Georgia, 'Times New Roman', serif"

def is_light_color(hex_str: str) -> bool:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return ((0.299 * r + 0.587 * g + 0.114 * b) / 255.0) > 0.65
    except Exception:
        return False

def render_kpi_card(metric: MetricDelta, accent_color: str = "#C6A15B") -> str:
    """Render a clean, mobile-safe KPI card block."""
    if metric.unit == "percentage":
        val_str = f"{metric.current_value:.1f}%"
    elif metric.unit == "currency":
        val_str = f"${metric.current_value:,.2f}"
    else:
        val_str = f"{int(metric.current_value):,}"

    if metric.direction == "up":
        arrow = "&#8593;"
        badge_bg = "#ECFDF5"
        badge_color = "#047857"
        badge_border = "#A7F3D0"
    elif metric.direction == "down":
        arrow = "&#8595;"
        badge_bg = "#FEF2F2"
        badge_color = "#B91C1C"
        badge_border = "#FECACA"
    else:
        arrow = "&rarr;"
        badge_bg = "#F3F4F6"
        badge_color = "#4B5563"
        badge_border = "#E5E7EB"

    if metric.is_percentage_rate and metric.percentage_points_change is not None:
        pct_str = f"{metric.percentage_points_change:+.1f}% pts"
    elif metric.percentage_change is not None:
        pct_str = f"{metric.percentage_change:+.1f}%"
    else:
        pct_str = "stable"

    disp_name = html.escape(metric.display_name)
    return f"""
    <div style="{STYLE_CARD}; margin-bottom: 8px;">
      <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: #71717A; font-weight: 600; margin-bottom: 6px;">{disp_name}</div>
      <div style="font-family: {FONT_FAMILY_SERIF}; font-size: 22px; font-weight: 700; color: #09090B; letter-spacing: -0.02em; margin-bottom: 8px; line-height: 1.1;">{val_str}</div>
      <div style="display: inline-block; background: {badge_bg}; border: 1px solid {badge_border}; color: {badge_color}; font-size: 10.5px; font-weight: 600; padding: 2px 7px; border-radius: 4px; font-family: {FONT_FAMILY_MAIN};">
        {arrow} {pct_str}
      </div>
    </div>
    """

def render_action_card(action: ActionItem, accent_color: str = "#C6A15B") -> str:
    """Render a prioritized action card item."""
    title = html.escape(action.title)
    desc = html.escape(action.description)
    priority = html.escape(action.priority)
    impact_area = html.escape(action.impact_area)
    evidence = html.escape(action.evidence) if action.evidence else ""

    if action.priority.lower() == "high":
        p_badge = f"background: {accent_color}; color: #FFFFFF;"
    else:
        p_badge = "background: #F4F4F5; color: #52525B; border: 1px solid #E4E4E7;"

    evidence_html = f'<div style="margin-top: 6px; font-size: 11px; color: #71717A; font-family: {FONT_FAMILY_MAIN};"><strong>Evidence:</strong> {evidence}</div>' if evidence else ""

    return f"""
    <div style="background: #FFFFFF; border: 1px solid #E4E4E7; border-left: 3px solid {accent_color}; border-radius: 6px; padding: 14px 16px; margin-bottom: 10px;">
      <table role="presentation" style="width: 100%; border-collapse: collapse; margin-bottom: 4px;">
        <tr>
          <td style="vertical-align: middle;">
            <strong style="font-family: {FONT_FAMILY_MAIN}; color: #09090B; font-size: 13.5px; font-weight: 700;">{title}</strong>
          </td>
          <td style="text-align: right; vertical-align: middle; white-space: nowrap;">
            <span style="font-size: 11px; color: #71717A; margin-right: 6px; font-weight: 500;">{impact_area}</span>
            <span style="{p_badge} padding: 2px 7px; border-radius: 3px; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;">{priority}</span>
          </td>
        </tr>
      </table>
      <p style="margin: 0; color: #52525B; font-size: 12.5px; line-height: 1.5; font-family: {FONT_FAMILY_MAIN};">{desc}</p>
      {evidence_html}
    </div>
    """
