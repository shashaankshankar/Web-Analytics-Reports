from __future__ import annotations
import html
from typing import Any, Dict, List, Optional, Sequence
from app.analytics.contracts import ActionItem, MetricDelta, PagePerformance, StrikingDistanceKeyword

# Editorial design tokens matching the updated design language
COLOR_BG = "#F7F9FB"
COLOR_SURFACE = "#FFFFFF"
COLOR_SURFACE_LOW = "#F2F4F6"
COLOR_ON_SURFACE = "#191C1E"
COLOR_SECONDARY = "#515F74"
COLOR_SURFACE_VARIANT = "#E0E3E5"
COLOR_TERTIARY = "#000000"
COLOR_OUTLINE_VARIANT = "#C6C6CD"

STYLE_CARD = "background: #FFFFFF; border: 1px solid #E0E3E5; border-radius: 2px; padding: 16px 14px; text-align: left;"
STYLE_CALLOUT = "background: #F7F9FB; border: 1px solid #E0E3E5; border-radius: 2px; padding: 14px 16px;"
FONT_FAMILY_MAIN = "'Work Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
FONT_FAMILY_SERIF = "'Source Serif 4', Georgia, 'Times New Roman', serif"

def is_light_color(hex_str: str) -> bool:
    h = hex_str.lstrip('#')
    if len(h) == 3:
        h = ''.join([c * 2 for c in h])
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return ((0.299 * r + 0.587 * g + 0.114 * b) / 255.0) > 0.65
    except Exception:
        return False

def render_kpi_card(metric: MetricDelta, primary_color: str = '#000000', accent_color: str = '#C6A15B') -> str:
    """Render a clean, editorial KPI card matching the design language."""
    if metric.unit == 'percentage':
        val_str = f'{metric.current_value:.1f}%'
    elif metric.unit == 'currency':
        val_str = f'${metric.current_value:,.2f}'
    else:
        val_str = f'{int(metric.current_value):,}'

    if metric.direction == 'up':
        arrow = '&#8593;'
        badge_color = '#16A34A'
    elif metric.direction == 'down':
        arrow = '&#8595;'
        badge_color = '#BA1A1A'
    else:
        arrow = '&rarr;'
        badge_color = '#515F74'

    val_color = primary_color if primary_color else '#191C1E'

    if metric.is_percentage_rate and metric.percentage_points_change is not None:
        pct_str = f'{metric.percentage_points_change:+.1f}% pts'
    elif metric.percentage_change is not None:
        pct_str = f'{metric.percentage_change:+.1f}%'
    elif metric.prior_value is None:
        arrow = ''
        badge_color = '#515F74'
        pct_str = 'baseline'
    else:
        pct_str = 'stable'

    disp_name = html.escape(metric.display_name)
    return f'''
    <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-radius: 2px; padding: 16px 14px; text-align: left; height: 100%; box-sizing: border-box;">
      <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #515F74; font-weight: 700; margin-bottom: 8px;">{disp_name}</div>
      <div style="font-family: {FONT_FAMILY_SERIF}; font-size: 26px; font-weight: 600; color: {val_color}; letter-spacing: -0.02em; margin-bottom: 8px; line-height: 1.1;">{val_str}</div>
      <div style="font-family: {FONT_FAMILY_MAIN}; font-size: 12px; font-weight: 600; color: {badge_color}; letter-spacing: 0.02em;">
        <span style="font-size: 13px; font-weight: bold; vertical-align: baseline;">{arrow}</span> {pct_str}
      </div>
    </div>
    '''


def render_goals_block(
    goals: Sequence[str],
    primary_color: str = '#000000',
    accent_color: str = '#C6A15B',
) -> str:
    """Render the configured client goals without inventing or rewriting them."""
    cleaned_goals = [goal.strip() for goal in goals if isinstance(goal, str) and goal.strip()]
    if cleaned_goals:
        goal_items = ''.join(
            f'<li style="padding: 3px 0; color: #191C1E;">{html.escape(goal)}</li>'
            for goal in cleaned_goals
        )
    else:
        goal_items = '<li style="padding: 3px 0; color: #515F74;">No specific client goals are configured.</li>'

    return f'''
    <div style="background: #F7F9FB; border: 1px solid #E0E3E5; border-left: 4px solid {accent_color}; border-radius: 2px; padding: 14px 16px;">
      <ol style="margin: 0; padding-left: 22px; font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; line-height: 1.5;">
        {goal_items}
      </ol>
    </div>
    '''


_ACTION_PRIORITY_ORDER = {'high': 0, 'medium': 1, 'low': 2}


def select_strongest_actions(actions: Sequence[ActionItem], limit: int) -> List[ActionItem]:
    """Select the strongest actions by explicit priority, preserving provider order for ties."""
    if limit <= 0:
        return []
    ranked_actions = sorted(
        enumerate(actions),
        key=lambda indexed_action: (
            _ACTION_PRIORITY_ORDER.get((indexed_action[1].priority or '').strip().lower(), 3),
            indexed_action[0],
        ),
    )
    return [action for _, action in ranked_actions[:limit]]

def render_action_card(
    action: ActionItem,
    primary_color: str = '#000000',
    accent_color: str = '#C6A15B',
    is_last: bool = False,
    responsive_mobile: bool = False,
) -> str:
    """Render a prioritized strategic action row item matching client-friendly language."""
    title = html.escape(action.title)
    desc = html.escape(action.description)
    raw_priority = (action.priority or '').strip().lower()
    border_bottom = 'border-bottom: 1px solid rgba(198, 198, 205, 0.3);' if not is_last else ''

    if raw_priority == 'high':
        priority_label = 'Top Priority'
        p_badge = 'background: #FFDAD6; color: #93000A;'
    elif raw_priority == 'medium':
        priority_label = 'Recommended Next Step'
        p_badge = 'background: #D5E3FD; color: #57657B;'
    else:
        priority_label = 'Standard Optimization'
        p_badge = 'background: #E0E3E5; color: #45464D;'

    copy_class = 'weekly-action-copy' if responsive_mobile else ''
    badge_class = 'weekly-action-badge' if responsive_mobile else ''
    check_class = 'weekly-action-check' if responsive_mobile else ''

    return f'''
    <div style="padding: 14px 16px; {border_bottom}">
      <table role="presentation" style="width: 100%; border-collapse: collapse;">
        <tr>
          <td class="{check_class}" style="width: 24px; vertical-align: top; padding-top: 2px; padding-right: 12px;">
            <div style="width: 16px; height: 16px; border: 1.5px solid {primary_color if primary_color else '#76777D'}; border-radius: 2px; background: #FFFFFF;"></div>
          </td>
          <td class="{copy_class}" style="vertical-align: middle;">
            <div style="font-family: {FONT_FAMILY_MAIN}; color: #191C1E; font-size: 14px; font-weight: 600; letter-spacing: 0.01em; margin-bottom: 2px;">{title}</div>
            <div style="font-family: {FONT_FAMILY_MAIN}; color: #515F74; font-size: 13px; line-height: 1.45;">{desc}</div>
          </td>
          <td class="{badge_class}" style="text-align: right; vertical-align: middle; white-space: nowrap; padding-left: 12px;">
            <span style="{p_badge} padding: 3px 8px; border-radius: 2px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; font-family: {FONT_FAMILY_MAIN}; display: inline-block;">{priority_label}</span>
          </td>
        </tr>
      </table>
    </div>
    '''
