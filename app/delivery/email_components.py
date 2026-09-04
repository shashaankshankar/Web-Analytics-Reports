from __future__ import annotations
import html
import math
import re
from collections.abc import Mapping
from typing import Any, List, Optional, Sequence
from app.analytics.contracts import ActionItem, ConversionEventSummary, MetricDelta, SourceAvailability, StrikingDistanceKeyword

# "Ledger cards" design tokens: a white page, a dark branded KPI strip, numbered
# hairline section labels, and cream cards. Client branding supplies primary,
# secondary, and accent; everything below is the neutral scaffolding.
COLOR_PAGE_BG = "#EDEBE6"
COLOR_SURFACE = "#FFFFFF"
COLOR_HAIRLINE = "#E6E2DA"
COLOR_TAG_BORDER = "#C9C5BC"
COLOR_ON_SURFACE = "#1A1A1A"
COLOR_BODY = "#3D3D42"
COLOR_MUTED = "#6B6B70"
COLOR_ZERO = "#9A968E"
COLOR_WIN = "#1E7F4F"
COLOR_WATCH = "#B3261E"
# Used when a client's configured secondary colour is too dark to sit behind
# dark body text; cards must stay readable whatever the brand palette is.
COLOR_CARD_FALLBACK = "#F7F4EE"

# Email-safe system stacks only: no webfont request survives every client.
FONT_FAMILY_MAIN = "'Helvetica Neue', Helvetica, Arial, sans-serif"
FONT_FAMILY_SERIF = "Georgia, 'Times New Roman', serif"

SECTION_LABEL_REPORT_DELIVERY = "Analytics Report Delivery"
SECTION_LABEL_WEBSITE_INQUIRY = "Website Inquiry Delivery"

_TABLE = 'role="presentation" cellpadding="0" cellspacing="0" border="0"'


def is_light_color(hex_str: str) -> bool:
    h = hex_str.lstrip('#')
    if len(h) == 3:
        h = ''.join([c * 2 for c in h])
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return ((0.299 * r + 0.587 * g + 0.114 * b) / 255.0) > 0.65
    except Exception:
        return False


def card_surface(secondary_color: str) -> str:
    """Card background derived from client branding, kept light enough to read."""
    return secondary_color if is_light_color(secondary_color) else COLOR_CARD_FALLBACK


def header_text_colors(primary_color: str) -> tuple[str, str]:
    """Return (text, muted) colours legible on the branded KPI strip."""
    if is_light_color(primary_color):
        return COLOR_ON_SURFACE, COLOR_MUTED
    return "#FFFFFF", "#B8B3A8"


def copyright_year(period_end: str) -> str:
    """Footer year, taken from the reported period rather than the clock."""
    period_end = period_end or ""
    return period_end[:4] if len(period_end) >= 4 and period_end[:4].isdigit() else "2026"


def format_metric_value(metric: MetricDelta) -> str:
    if metric.current_value is None:
        return 'Not available'
    if metric.unit == 'percentage':
        return f'{metric.current_value:.1f}%'
    if metric.unit == 'currency':
        return f'${metric.current_value:,.2f}'
    return f'{int(metric.current_value):,}'


def _delta_parts(metric: MetricDelta) -> tuple[str, str]:
    """Return (arrow, label) for a metric's change, without inventing a comparison."""
    if metric.prior_value is None:
        return '', 'baseline'
    if metric.is_percentage_rate and metric.percentage_points_change is not None:
        magnitude = f'{abs(metric.percentage_points_change):.1f} pts'
    elif metric.percentage_change is not None:
        magnitude = f'{abs(metric.percentage_change):.1f}%'
    else:
        return '&rarr;', 'stable'
    if metric.direction == 'up':
        return '&#9650;', magnitude
    if metric.direction == 'down':
        return '&#9660;', magnitude
    return '&rarr;', magnitude


def _delta_chip_colors(direction: str, has_prior: bool, on_light: bool) -> tuple[str, str]:
    if not has_prior or direction not in {'up', 'down'}:
        return ('#E6E2DA', COLOR_MUTED) if on_light else ('#2A2A2C', '#B8B3A8')
    if direction == 'up':
        return ('#DCF0E4', '#14603A') if on_light else ('#1C3A2A', '#7FD6A3')
    return ('#FBE2DF', '#93221B') if on_light else ('#3F1F1D', '#F2A19A')


def render_delta_chip(metric: MetricDelta, primary_color: str) -> str:
    """A small filled chip carrying the period-over-period change."""
    on_light = is_light_color(primary_color)
    arrow, label = _delta_parts(metric)
    bg, fg = _delta_chip_colors(metric.direction, metric.prior_value is not None, on_light)
    text = f'{arrow} {label}'.strip()
    return (
        f'<table {_TABLE} style="margin-top: 10px;"><tr>'
        f'<td bgcolor="{bg}" style="background-color: {bg}; padding: 3px 8px; font-family: {FONT_FAMILY_MAIN}; '
        f'font-size: 11px; font-weight: 600; color: {fg};">{text}</td>'
        '</tr></table>'
    )


def render_kpi_cells(
    metrics: Sequence[MetricDelta],
    primary_color: str = '#0A0A0B',
    *,
    value_size: int = 36,
    value_class: str = 'kpi-val',
) -> str:
    """Render the KPI cells for the dark branded strip, one column per metric."""
    if not metrics:
        return ''
    header_text, header_muted = header_text_colors(primary_color)
    width = max(1, int(100 / len(metrics)))
    cells = ''
    for metric in metrics:
        cells += (
            f'<td class="kpi" width="{width}%" style="vertical-align: top; padding-right: 14px;">'
            f'<div class="kpi-label" style="font-family: {FONT_FAMILY_MAIN}; font-size: 10px; letter-spacing: 0.12em; '
            f'text-transform: uppercase; color: {header_muted}; padding-bottom: 8px;">{html.escape(metric.display_name)}</div>'
            f'<div class="{value_class}" style="font-family: {FONT_FAMILY_MAIN}; font-size: {value_size}px; '
            f'line-height: {value_size}px; font-weight: 600; letter-spacing: -1px; color: {header_text};">{format_metric_value(metric)}</div>'
            f'{render_delta_chip(metric, primary_color)}'
            '</td>'
        )
    return cells


def render_section_label(number: int, label: str, accent_color: str, tag: Optional[str] = None) -> str:
    """A numbered hairline section marker; the rule fills the remaining width."""
    tag_cell = ''
    if tag:
        tag_cell = (
            '<td style="padding-left: 10px; white-space: nowrap;">'
            f'<span style="display: inline-block; border: 1px solid {COLOR_TAG_BORDER}; padding: 3px 8px; '
            f'font-family: {FONT_FAMILY_MAIN}; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; '
            f'color: {COLOR_MUTED};">{html.escape(tag)}</span></td>'
        )
    return (
        f'<table {_TABLE} width="100%"><tr>'
        f'<td width="22" style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; font-weight: 700; '
        f'color: {accent_color}; white-space: nowrap; padding-right: 10px;">{number:02d}</td>'
        f'<td style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; font-weight: 600; letter-spacing: 0.12em; '
        f'text-transform: uppercase; color: {COLOR_MUTED}; white-space: nowrap; padding-right: 10px;">{label}</td>'
        '<td width="100%" style="vertical-align: middle;">'
        f'<div style="border-top: 1px solid {COLOR_HAIRLINE}; font-size: 0; line-height: 0;">&nbsp;</div></td>'
        f'{tag_cell}'
        '</tr></table>'
    )


_STATUS_COLORS = {
    'win': (COLOR_WIN, COLOR_WIN),
    'watch': (COLOR_WATCH, COLOR_WATCH),
}


def render_finding_card(
    label: str,
    body: str,
    secondary_color: str,
    *,
    status: str = 'neutral',
    accent_color: str = '#C6A15B',
    spaced: bool = True,
) -> str:
    """One cream card: a status dot, a plain-English label, and the narrative body."""
    dot_color, label_color = _STATUS_COLORS.get(status, (accent_color, COLOR_MUTED))
    surface = card_surface(secondary_color)
    margin = 'margin-bottom: 10px;' if spaced else ''
    return (
        f'<table {_TABLE} width="100%" bgcolor="{surface}" style="background-color: {surface}; {margin}">'
        '<tr><td style="padding: 18px 20px;">'
        f'<table {_TABLE}><tr>'
        '<td width="8" style="vertical-align: middle;">'
        f'<div style="width: 8px; height: 8px; border-radius: 4px; background-color: {dot_color}; font-size: 0; line-height: 0;">&nbsp;</div></td>'
        f'<td style="padding-left: 8px; font-family: {FONT_FAMILY_MAIN}; font-size: 11px; font-weight: 700; '
        f'letter-spacing: 0.1em; text-transform: uppercase; color: {label_color};">{label}</td>'
        '</tr></table>'
        f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; line-height: 21px; color: {COLOR_BODY}; '
        f'padding-top: 10px;">{html.escape(body)}</div>'
        '</td></tr></table>'
    )


def render_goal_pills(goals: Sequence[str]) -> str:
    """Render configured client goals as outlined pills without rewriting them."""
    cleaned = [goal.strip() for goal in goals if isinstance(goal, str) and goal.strip()]
    if not cleaned:
        return (
            f'<span style="display: inline-block; border: 1px solid {COLOR_HAIRLINE}; padding: 6px 12px; '
            f'margin: 0 6px 6px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: {COLOR_MUTED};">'
            'No specific client goals are configured.</span>'
        )
    return ''.join(
        f'<span style="display: inline-block; border: 1px solid {COLOR_HAIRLINE}; padding: 6px 12px; '
        f'margin: 0 6px 6px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; color: {COLOR_ON_SURFACE};">'
        f'{html.escape(goal)}</span>'
        for goal in cleaned
    )


def render_bar_rows(
    rows: Sequence[tuple[str, Optional[int]]],
    bar_color: str,
    secondary_color: str,
) -> str:
    """Horizontal bars scaled to the largest observed value in the set."""
    usable = [(label, value) for label, value in rows if value is not None]
    if not usable:
        return ''
    peak = max(value for _, value in usable)
    track = card_surface(secondary_color)
    out = ''
    for label, value in rows:
        has_value = value is not None and value > 0
        text_color = COLOR_ON_SURFACE if has_value else COLOR_ZERO
        percent = int(round(value / peak * 100)) if has_value and peak > 0 else 0
        if percent > 0:
            fill = (
                f'<table {_TABLE} width="100%" bgcolor="{track}" style="background-color: {track};"><tr>'
                f'<td width="{percent}%" bgcolor="{bar_color}" style="background-color: {bar_color}; height: 10px; font-size: 0; line-height: 0;">&nbsp;</td>'
                '<td style="height: 10px; font-size: 0; line-height: 0;">&nbsp;</td>'
                '</tr></table>'
            )
        else:
            fill = (
                f'<div style="background-color: {track}; height: 10px; font-size: 0; line-height: 0;">&nbsp;</div>'
            )
        display_value = f'{value:,}' if value is not None else 'n/a'
        out += (
            '<tr>'
            f'<td width="90" style="padding: 0 10px 8px 0; font-family: {FONT_FAMILY_MAIN}; font-size: 12.5px; color: {text_color};">{html.escape(label)}</td>'
            f'<td style="padding: 0 0 8px 0;">{fill}</td>'
            f'<td width="38" align="right" style="padding: 0 0 8px 10px; font-family: {FONT_FAMILY_MAIN}; '
            f'font-size: 12.5px; font-weight: 600; color: {text_color};">{display_value}</td>'
            '</tr>'
        )
    return out


def render_bar_group(
    heading: str,
    rows: Sequence[tuple[str, Optional[int]]],
    bar_color: str,
    secondary_color: str,
) -> str:
    body = render_bar_rows(rows, bar_color, secondary_color)
    if not body:
        return ''
    return (
        f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; letter-spacing: 0.1em; '
        f'text-transform: uppercase; color: {COLOR_MUTED}; padding-bottom: 10px;">{heading}</div>'
        f'<table {_TABLE} width="100%">{body}</table>'
    )


def render_stat_tiles(events: Sequence[ConversionEventSummary], secondary_color: str) -> str:
    """One tile per recorded conversion event, with its change when a prior exists."""
    if not events:
        return ''
    surface = card_surface(secondary_color)
    width = max(1, int(100 / len(events)))
    out = ''
    for event in events:
        value = f'{event.current_count:,}' if event.current_count is not None else 'Not available'
        if event.prior_count is None:
            delta_html = (
                f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; font-weight: 600; '
                f'color: {COLOR_MUTED}; padding-top: 6px;">baseline</div>'
            )
        elif event.percentage_change is not None:
            if event.direction == 'up':
                arrow, color = '&#9650;', COLOR_WIN
            elif event.direction == 'down':
                arrow, color = '&#9660;', COLOR_WATCH
            else:
                arrow, color = '&rarr;', COLOR_MUTED
            delta_html = (
                f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; font-weight: 600; '
                f'color: {color}; padding-top: 6px;">{arrow} {abs(event.percentage_change):.1f}% vs prior</div>'
            )
        else:
            delta_html = (
                f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11px; font-weight: 600; '
                f'color: {COLOR_MUTED}; padding-top: 6px;">{event.prior_count:,} prior</div>'
            )
        out += (
            f'<td class="tile" width="{width}%" style="vertical-align: top; padding: 0 5px 10px 0;">'
            f'<table {_TABLE} width="100%" bgcolor="{surface}" style="background-color: {surface};">'
            '<tr><td style="padding: 14px 16px;">'
            f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 24px; line-height: 24px; font-weight: 600; '
            f'letter-spacing: -0.5px; color: {COLOR_ON_SURFACE};">{value}</div>'
            f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11.5px; line-height: 16px; color: {COLOR_MUTED}; '
            f'padding-top: 8px;">{html.escape(event.display_name)}</div>'
            f'{delta_html}'
            '</td></tr></table></td>'
        )
    return out


def render_ledger_table(headers: Sequence[tuple[str, str, str]], rows: Sequence[Sequence[tuple[str, str, str]]]) -> str:
    """A ruled table. Each cell is (text, align, extra_style); text must be pre-escaped."""
    head = ''.join(
        f'<th align="{align}" style="padding: 8px 0; border-bottom: 1px solid {COLOR_ON_SURFACE}; '
        f'font-family: {FONT_FAMILY_MAIN}; font-size: 10.5px; font-weight: 600; letter-spacing: 0.1em; '
        f'text-transform: uppercase; {extra}">{text}</th>'
        for text, align, extra in headers
    )
    body = ''
    for row in rows:
        body += '<tr>' + ''.join(
            f'<td align="{align}" style="padding: 11px 0; border-bottom: 1px solid {COLOR_HAIRLINE}; '
            f'font-family: {FONT_FAMILY_MAIN}; font-size: 13px; {extra}">{text}</td>'
            for text, align, extra in row
        ) + '</tr>'
    return f'<table {_TABLE} width="100%" style="margin-top: 16px;"><tr>{head}</tr>{body}</table>'


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


def render_action_row(action: ActionItem, is_last: bool = False) -> str:
    """A checkbox, the action copy, and an outlined priority tag on one ruled row."""
    raw_priority = (action.priority or '').strip().lower()
    if raw_priority == 'high':
        tag_label, tag_color = 'Top Priority', COLOR_WATCH
    elif raw_priority == 'medium':
        tag_label, tag_color = 'Recommended Next Step', COLOR_MUTED
    else:
        tag_label, tag_color = 'Standard Optimization', COLOR_MUTED
    tag_border = tag_color if raw_priority == 'high' else COLOR_TAG_BORDER
    rule = '' if is_last else f'border-bottom: 1px solid {COLOR_HAIRLINE};'
    return (
        '<tr>'
        f'<td width="22" style="vertical-align: top; padding: 18px 0 16px 0; {rule}">'
        f'<div style="width: 14px; height: 14px; border: 1.5px solid {COLOR_ON_SURFACE}; border-radius: 2px; font-size: 0; line-height: 0;">&nbsp;</div></td>'
        f'<td style="vertical-align: top; padding: 16px 14px; {rule}">'
        f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 14px; line-height: 20px; font-weight: 600; '
        f'color: {COLOR_ON_SURFACE}; padding-bottom: 3px;">{html.escape(action.title)}</div>'
        f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13px; line-height: 20px; color: {COLOR_MUTED};">{html.escape(action.description)}</div></td>'
        f'<td width="132" align="right" style="vertical-align: top; padding: 16px 0; {rule} white-space: nowrap;">'
        f'<span style="display: inline-block; border: 1px solid {tag_border}; padding: 3px 8px; '
        f'font-family: {FONT_FAMILY_MAIN}; font-size: 10px; font-weight: 700; letter-spacing: 0.08em; '
        f'text-transform: uppercase; color: {tag_color};">{tag_label}</span></td>'
        '</tr>'
    )


def render_note_band(text: str, accent_color: str, label: str = 'Note') -> str:
    """A full-width band for report-mode caveats, in place of a boxed callout.

    The caller owns the band's background so it can match the surrounding row.
    """
    return (
        f'<table {_TABLE} width="100%"><tr>'
        f'<td width="40" style="vertical-align: top; padding-top: 2px; font-family: {FONT_FAMILY_MAIN}; font-size: 11px; '
        f'font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: {accent_color};">{label}</td>'
        f'<td style="padding-left: 14px; font-family: {FONT_FAMILY_MAIN}; font-size: 13px; line-height: 20px; '
        f'color: {COLOR_BODY};">{text}</td>'
        '</tr></table>'
    )


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


def _commentary(text: str, *, top: int = 12, bottom: int = 0) -> str:
    return (
        f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 13.5px; line-height: 22px; color: {COLOR_BODY}; '
        f'padding: {top}px 0 {bottom}px 0;">{text}</div>'
    )


def render_report_delivery_block(delivery: Any, primary_color: str = '#0A0A0B', accent_color: str = '#C6A15B') -> str:
    """Client-safe report email-delivery body. The caller supplies the section label."""
    if not has_report_delivery_data(delivery):
        return ''
    rows = report_delivery_metric_rows(delivery)
    status_note = _client_delivery_note(_delivery_status(delivery))
    intro = _commentary(f'Email delivery health for this analytics report. {html.escape(status_note)}')
    if not rows:
        return intro + _commentary('Not available.', top=0, bottom=0)
    table = render_ledger_table(
        [('Metric', 'left', f'color: {COLOR_MUTED};'), ('Value', 'right', f'color: {COLOR_MUTED};')],
        [
            [
                (html.escape(label), 'left', f'color: {COLOR_ON_SURFACE}; font-weight: 600;'),
                (html.escape(value), 'right', f'color: {COLOR_ON_SURFACE};'),
            ]
            for label, value in rows
        ],
    )
    footnote = (
        f'<div style="font-family: {FONT_FAMILY_MAIN}; font-size: 11.5px; line-height: 18px; color: {COLOR_MUTED}; '
        'padding-top: 12px;">Open and click figures are estimated tracking signals, not inbox confirmation.</div>'
    )
    return intro + table + footnote


def render_website_inquiry_delivery_block(delivery: Any, primary_color: str = '#0A0A0B', accent_color: str = '#C6A15B') -> str:
    """Technical website-inquiry delivery health, without lead claims."""
    if not has_website_inquiry_data(delivery):
        return ''
    rows = website_inquiry_metric_rows(delivery)
    status_note = _client_delivery_note(_delivery_status(delivery), website=True)
    intro = _commentary(
        f'Technical email-delivery health for website inquiry notifications. {html.escape(status_note)}'
    )
    if not rows:
        return intro + _commentary('Not available.', top=0, bottom=0)
    table = render_ledger_table(
        [
            ('Metric', 'left', f'color: {COLOR_MUTED};'),
            ('This period', 'right', f'color: {COLOR_MUTED};'),
            ('Prior period', 'right', f'color: {COLOR_MUTED};'),
        ],
        [
            [
                (html.escape(label), 'left', f'color: {COLOR_ON_SURFACE}; font-weight: 600;'),
                (html.escape(current), 'right', f'color: {COLOR_ON_SURFACE};'),
                (html.escape(prior), 'right', f'color: {COLOR_MUTED};'),
            ]
            for label, current, prior in rows
        ],
    )
    return intro + table


def render_keyword_table(keywords: Sequence[StrikingDistanceKeyword], accent_color: str, scrub) -> str:
    """Striking-distance search terms, ranked by the precomputed opportunity score."""
    if not keywords:
        return ''
    return render_ledger_table(
        [
            ('Search term', 'left', f'color: {COLOR_MUTED};'),
            ('Search views', 'right', f'color: {COLOR_MUTED};'),
            ('Google rank', 'right', f'color: {COLOR_MUTED};'),
            ('Opportunity', 'right', f'color: {accent_color};'),
        ],
        [
            [
                (html.escape(scrub(keyword.query)), 'left', f'color: {COLOR_ON_SURFACE}; font-weight: 600;'),
                (f'{keyword.impressions:,}', 'right', f'color: {COLOR_MUTED};'),
                (f'{keyword.position:.1f}', 'right', f'color: {COLOR_ON_SURFACE}; font-weight: 600;'),
                (f'{keyword.opportunity_score:.0f}', 'right', f'color: {accent_color}; font-weight: 700;'),
            ]
            for keyword in keywords
        ],
    )
