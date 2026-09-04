from __future__ import annotations
import html
import math
import re
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Sequence
from app.analytics.contracts import ActionItem, MetricDelta, PagePerformance, SourceAvailability, StrikingDistanceKeyword

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
    if metric.current_value is None:
        val_str = 'Not available'
    elif metric.unit == 'percentage':
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


# Only these provider aggregates are suitable for the client-facing report.
# IDs, recipient fields, source names, reasons, and any unrecognized provider
# fields intentionally stay out of both the HTML and PDF renderers.
_REPORT_DELIVERY_METRIC_SPECS = (
    ('sent', 'Messages sent', False),
    ('delivered', 'Messages delivered', False),
    ('delivery_rate', 'Delivery rate', True),
    ('bounced', 'Bounced messages', False),
    ('bounce_rate', 'Bounce rate', True),
    ('unique_opened', 'Estimated opens', False),
    ('opened', 'Estimated opens', False),
    ('open_rate', 'Estimated open rate', True),
    ('unique_clicked', 'Estimated clicks', False),
    ('clicked', 'Estimated clicks', False),
    ('click_rate', 'Estimated click rate', True),
)

_CLIENT_RENDERABLE_DELIVERY_STATUSES = frozenset({
    SourceAvailability.AVAILABLE.value,
    SourceAvailability.PARTIAL.value,
    SourceAvailability.EMPTY.value,
    SourceAvailability.UNAVAILABLE.value,
    SourceAvailability.ERROR.value,
})

_CLIENT_RENDERABLE_METRIC_STATUSES = frozenset({
    SourceAvailability.AVAILABLE.value,
    SourceAvailability.PARTIAL.value,
})

_INQUIRY_EVENT_LABELS = {
    'contact_form_submit': 'Inquiry form submissions',
    'form_submit': 'Inquiry form submissions',
    'generate_lead': 'Inquiry form submissions',
    'inquiry_submit': 'Inquiry form submissions',
    'inquiry_submitted': 'Inquiry form submissions',
    'email_sent': 'Inquiry messages sent',
    'notification_sent': 'Inquiry messages sent',
    'sent': 'Inquiry messages sent',
    'email_delivered': 'Inquiry messages delivered',
    'notification_delivered': 'Inquiry messages delivered',
    'delivered': 'Inquiry messages delivered',
    'email_bounced': 'Inquiry messages not delivered',
    'notification_bounced': 'Inquiry messages not delivered',
    'bounced': 'Inquiry messages not delivered',
    'email_failed': 'Inquiry messages not delivered',
    'notification_failed': 'Inquiry messages not delivered',
    'failed': 'Inquiry messages not delivered',
}


def _delivery_field(model: Any, name: str, default: Any = None) -> Any:
    if isinstance(model, Mapping):
        return model.get(name, default)
    return getattr(model, name, default)


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or value < 0:
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _format_delivery_number(value: Any, *, percentage: bool = False) -> str | None:
    number = _safe_number(value)
    if number is None:
        return None
    if percentage:
        return f'{float(number):,.1f}%'
    if isinstance(number, int) or float(number).is_integer():
        return f'{int(number):,}'
    return f'{float(number):,.1f}'


def _delivery_status(model: Any) -> str:
    value = _delivery_field(model, 'status', '')
    return str(getattr(value, 'value', value) or '').strip().lower()


def report_delivery_metric_rows(delivery: Any) -> list[tuple[str, str]]:
    """Return only safe, client-facing report-delivery aggregate rows."""
    # A failed or unconfigured source must never turn placeholder zeroes into
    # client-facing claims.  Only provider-backed available/partial results
    # may contribute numeric rows; other states are rendered as unavailable.
    if _delivery_status(delivery) not in _CLIENT_RENDERABLE_METRIC_STATUSES:
        return []
    metrics = _delivery_field(delivery, 'metrics', {}) or {}
    if not isinstance(metrics, Mapping):
        return []

    rows: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for key, label, percentage in _REPORT_DELIVERY_METRIC_SPECS:
        # Prefer unique opens/clicks because the corresponding rates use the
        # unique aggregates. The non-unique names are compatibility fallbacks.
        if label in {'Estimated opens', 'Estimated clicks'} and label in {row[0] for row in rows}:
            continue
        if key in seen_keys:
            continue
        formatted = _format_delivery_number(metrics.get(key), percentage=percentage)
        if formatted is None:
            continue
        rows.append((label, formatted))
        seen_keys.add(key)
    return rows


def has_report_delivery_data(delivery: Any) -> bool:
    """Whether a configured report-delivery result should be shown safely."""
    return delivery is not None and _delivery_status(delivery) in _CLIENT_RENDERABLE_DELIVERY_STATUSES


def _safe_inquiry_event_key(value: Any) -> str:
    return re.sub(r'[^a-z0-9_]+', '_', str(value or '').strip().lower()).strip('_')


def _inquiry_event_label(value: Any, used_labels: set[str]) -> str:
    normalized = _safe_inquiry_event_key(value)
    label = _INQUIRY_EVENT_LABELS.get(normalized, 'Website inquiry event')
    if label in used_labels:
        label = f'{label} (additional)'
    used_labels.add(label)
    return label


def _event_values(model: Any, field_name: str) -> Mapping[Any, Any]:
    values = _delivery_field(model, field_name, {}) or {}
    return values if isinstance(values, Mapping) else {}


def website_inquiry_metric_rows(delivery: Any) -> list[tuple[str, str, str]]:
    """Return safe current/prior rows for website inquiry delivery health."""
    if _delivery_status(delivery) not in _CLIENT_RENDERABLE_METRIC_STATUSES:
        return []
    rows: list[tuple[str, str, str]] = []
    current = _format_delivery_number(_delivery_field(delivery, 'current_inquiries'))
    prior = _format_delivery_number(_delivery_field(delivery, 'prior_inquiries'))
    if current is not None or prior is not None:
        rows.append(('Inquiry records', current or 'Not available', prior or 'Not available'))

    current_events = _event_values(delivery, 'inquiry_events')
    prior_events = _event_values(delivery, 'prior_inquiry_events')
    event_keys = list(current_events)
    event_keys.extend(key for key in prior_events if key not in current_events)
    used_labels: set[str] = set()
    for event_key in event_keys:
        event_current = _format_delivery_number(current_events.get(event_key))
        event_prior = _format_delivery_number(prior_events.get(event_key))
        if event_current is None and event_prior is None:
            continue
        rows.append((
            _inquiry_event_label(event_key, used_labels),
            event_current or 'Not available',
            event_prior or 'Not available',
        ))
    return rows


def has_website_inquiry_data(delivery: Any) -> bool:
    """Whether a configured website-inquiry result should be shown safely."""
    return delivery is not None and _delivery_status(delivery) in _CLIENT_RENDERABLE_DELIVERY_STATUSES


def _client_delivery_note(status: str, *, website: bool = False) -> str:
    if status == SourceAvailability.PARTIAL.value:
        return (
            "Some tracked activity was unavailable for this window; displayed figures are partial."
            if not website
            else "Some inquiry-notification activity was unavailable for this window; displayed figures are partial."
        )
    if status == SourceAvailability.EMPTY.value:
        return (
            "No tracked delivery activity was available for this window."
            if not website
            else "No website inquiry-notification activity was available for this window."
        )
    if status in {SourceAvailability.UNAVAILABLE.value, SourceAvailability.ERROR.value}:
        return (
            "Delivery metrics are not available for this window."
            if not website
            else "Website inquiry delivery metrics are not available for this window."
        )
    return (
        "Open and click figures are estimated tracking signals, not inbox confirmation."
        if not website
        else "These figures describe notification delivery, not appointments or confirmed leads."
    )


def _render_delivery_rows(rows: Sequence[tuple[str, str]], *, primary_color: str) -> str:
    return ''.join(
        f'<tr style="border-bottom: 1px solid rgba(198, 198, 205, 0.3);">'
        f'<td style="padding: 9px 10px; color: #191C1E; font-family: {FONT_FAMILY_MAIN}; font-size: 13px;">{html.escape(label)}</td>'
        f'<td style="padding: 9px 10px; text-align: right; color: {primary_color}; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; font-weight: 600;">{html.escape(value)}</td>'
        '</tr>'
        for label, value in rows
    )


def render_report_delivery_block(
    delivery: Any,
    primary_color: str = '#000000',
    accent_color: str = '#C6A15B',
) -> str:
    """Render the client-safe analytics report email-delivery section."""
    if not has_report_delivery_data(delivery):
        return ''
    rows = report_delivery_metric_rows(delivery)
    status = _delivery_status(delivery)
    rows_html = _render_delivery_rows(rows, primary_color=primary_color)
    if not rows_html:
        rows_html = (
            '<tr><td colspan="2" style="padding: 10px; color: #515F74; '
            f'font-family: {FONT_FAMILY_MAIN}; font-size: 13px;">Not available</td></tr>'
        )
    status_note = _client_delivery_note(status)
    return f'''
    <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-left: 4px solid {accent_color}; border-radius: 2px; padding: 16px 18px;">
      <h2 style="margin: 0 0 6px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 19px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Analytics Report Delivery</h2>
      <p style="margin: 0 0 12px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: #515F74; line-height: 1.5;">Email delivery health for this analytics report. {html.escape(status_note)}</p>
      <table role="presentation" style="width: 100%; border-collapse: collapse; border: 1px solid #E0E3E5;">
        {rows_html}
      </table>
      <p style="margin: 12px 0 0 0; font-family: {FONT_FAMILY_MAIN}; font-size: 12px; color: #515F74; line-height: 1.5;">Open and click figures are estimated tracking signals, not inbox confirmation.</p>
    </div>
    '''


def render_website_inquiry_delivery_block(
    delivery: Any,
    primary_color: str = '#000000',
    accent_color: str = '#C6A15B',
) -> str:
    """Render technical website-inquiry delivery health without lead claims."""
    if not has_website_inquiry_data(delivery):
        return ''
    rows = website_inquiry_metric_rows(delivery)
    status = _delivery_status(delivery)
    rows_html = ''.join(
        f'<tr style="border-bottom: 1px solid rgba(198, 198, 205, 0.3);">'
        f'<td style="padding: 9px 10px; color: #191C1E; font-family: {FONT_FAMILY_MAIN}; font-size: 13px;">{html.escape(label)}</td>'
        f'<td style="padding: 9px 10px; text-align: right; color: {primary_color}; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; font-weight: 600;">{html.escape(current)}</td>'
        f'<td style="padding: 9px 10px; text-align: right; color: #515F74; font-family: {FONT_FAMILY_MAIN}; font-size: 13px;">{html.escape(prior)}</td>'
        '</tr>'
        for label, current, prior in rows
    )
    if not rows_html:
        rows_html = (
            '<tr><td colspan="3" style="padding: 10px; color: #515F74; '
            f'font-family: {FONT_FAMILY_MAIN}; font-size: 13px;">Not available</td></tr>'
        )
    status_note = _client_delivery_note(status, website=True)
    return f'''
    <div style="background: #FFFFFF; border: 1px solid #E0E3E5; border-left: 4px solid {accent_color}; border-radius: 2px; padding: 16px 18px;">
      <h2 style="margin: 0 0 6px 0; font-family: {FONT_FAMILY_SERIF}; font-size: 19px; font-weight: 600; color: {primary_color}; line-height: 1.3;">Website Inquiry Delivery</h2>
      <p style="margin: 0 0 12px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: #515F74; line-height: 1.5;">Technical email-delivery health for website inquiry notifications. {html.escape(status_note)}</p>
      <table role="presentation" style="width: 100%; border-collapse: collapse; border: 1px solid #E0E3E5;">
        <tr style="background: #F7F9FB; border-bottom: 1px solid #E0E3E5;">
          <th style="padding: 8px 10px; text-align: left; color: #515F74; font-family: {FONT_FAMILY_MAIN}; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;">Metric</th>
          <th style="padding: 8px 10px; text-align: right; color: #515F74; font-family: {FONT_FAMILY_MAIN}; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;">This period</th>
          <th style="padding: 8px 10px; text-align: right; color: #515F74; font-family: {FONT_FAMILY_MAIN}; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;">Prior period</th>
        </tr>
        {rows_html}
      </table>
    </div>
    '''

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
