from __future__ import annotations

import os
from datetime import datetime
from io import BytesIO
from typing import List
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from app.ai.privacy import scrub_gsc_query

from app.analytics.contracts import FullGrowthBriefing, REPORT_SPECS, ReportMode, ReportType
from app.delivery.discovery_copy import build_client_discovery_copies
from app.delivery.email_components import (
    report_delivery_metric_rows,
    select_strongest_actions,
    website_inquiry_metric_rows,
)
from app.delivery.gbp_reporting import calls_rows, keyword_rows as gbp_keyword_rows, performance_rows, profile_rows, review_rows


def _register_fonts():
    """Register elegant TrueType fonts if available on the system."""
    georgia_paths = [
        ('/System/Library/Fonts/Supplemental/Georgia.ttf', 'Georgia'),
        ('/System/Library/Fonts/Supplemental/Georgia Bold.ttf', 'Georgia-Bold'),
        ('/System/Library/Fonts/Supplemental/Georgia Italic.ttf', 'Georgia-Italic'),
        ('/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf', 'Georgia-BoldItalic'),
    ]
    try:
        if all(os.path.exists(p) for p, _ in georgia_paths):
            for path, name in georgia_paths:
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, path))
            addMapping('Georgia', 0, 0, 'Georgia')
            addMapping('Georgia', 1, 0, 'Georgia-Bold')
            addMapping('Georgia', 0, 1, 'Georgia-Italic')
            addMapping('Georgia', 1, 1, 'Georgia-BoldItalic')
            return True
    except Exception:
        pass
    return False


_HAS_GEORGIA = _register_fonts()
FONT_SERIF = 'Georgia' if _HAS_GEORGIA else 'Times-Roman'
FONT_SERIF_BOLD = 'Georgia-Bold' if _HAS_GEORGIA else 'Times-Bold'
FONT_SANS = 'Helvetica'
FONT_SANS_BOLD = 'Helvetica-Bold'


def ensure_readable_text_color(hex_str: str, default: str = '#475569') -> colors.HexColor:
    """Ensure a brand color has sufficient contrast when printed as text on white backgrounds."""
    h = hex_str.lstrip('#')
    if len(h) == 3:
        h = ''.join([c * 2 for c in h])
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        if lum > 0.55:
            factor = 0.45 / lum
            r = min(255, max(0, int(r * factor)))
            g = min(255, max(0, int(g * factor)))
            b = min(255, max(0, int(b * factor)))
            return colors.HexColor(f'#{r:02X}{g:02X}{b:02X}')
        return colors.HexColor(f'#{r:02X}{g:02X}{b:02X}')
    except Exception:
        return colors.HexColor(default)


def hex_to_reportlab_color(hex_str: str, default: str = '#0A0A0B') -> colors.HexColor:
    try:
        return colors.HexColor(hex_str)
    except Exception:
        return colors.HexColor(default)


def format_client_friendly_priority(priority_str: str) -> str:
    """Convert technical priority strings into clear, approachable business terms."""
    p = (priority_str or '').strip().lower()
    if p == 'high':
        return 'Top Priority'
    elif p == 'medium':
        return 'Recommended Next Step'
    return 'Standard Optimization'


def _gbp_pdf_number(value) -> str:
    if value is None:
        return 'Not available'
    try:
        number = float(value)
        return f'{int(number):,}' if number.is_integer() else f'{number:,.1f}'
    except (TypeError, ValueError):
        return escape(str(value))


def build_executive_pdf(briefing: FullGrowthBriefing) -> bytes:
    """Generate a publication-grade, editorial executive PDF with simple, client-friendly language."""
    buffer = BytesIO()
    branding = briefing.branding
    primary_hex = branding.get('primary_color', '#0A0A0B') or '#0A0A0B'
    secondary_hex = branding.get('secondary_color', '#515F74') or '#515F74'
    accent_hex = branding.get('accent_color', '#C6A15B') or '#C6A15B'

    PRIMARY = hex_to_reportlab_color(primary_hex, '#0A0A0B')
    ACCENT = hex_to_reportlab_color(accent_hex, '#C6A15B')
    EYEBROW_COLOR = ensure_readable_text_color(secondary_hex, '#475569')
    NUM_STEP_COLOR = ensure_readable_text_color(accent_hex, '#8A6D3B')

    BG_SURFACE_LOW = colors.HexColor('#F2F4F6')
    BORDER_TERTIARY_20 = colors.HexColor('#94A3B8')
    BORDER_OUTLINE_30 = colors.HexColor('#CBD5E1')
    TEXT_ON_SURFACE = colors.HexColor('#0F172A')
    TEXT_SECONDARY = colors.HexColor('#334155')
    GREEN_TEXT = colors.HexColor('#15803D')
    RED_TEXT = colors.HexColor('#B91C1C')

    report_title = (
        'Initial Measurement Baseline'
        if briefing.report_mode == ReportMode.INITIAL_BASELINE
        else '28-Day Performance Report'
        if briefing.report_type == ReportType.PERFORMANCE_28D
        else 'Weekly Growth Digest'
    )

    styles = getSampleStyleSheet()

    # Editorial Typography Styles matching the high-density static design language
    styles.add(ParagraphStyle(name='CompanyEyebrow', fontName=FONT_SANS_BOLD, fontSize=9, leading=12, textColor=EYEBROW_COLOR, spaceAfter=2))
    styles.add(ParagraphStyle(name='MainDisplayTitle', fontName=FONT_SERIF_BOLD, fontSize=24, leading=28, textColor=PRIMARY, spaceAfter=3))
    styles.add(ParagraphStyle(name='PeriodSubtitle', fontName=FONT_SANS, fontSize=9.5, leading=13.5, textColor=TEXT_SECONDARY, spaceAfter=6))
    styles.add(ParagraphStyle(name='SectionHeaderCaps', fontName=FONT_SANS_BOLD, fontSize=9, leading=12, textColor=PRIMARY, spaceBefore=7, spaceAfter=4))
    styles.add(ParagraphStyle(name='HeadlineSm', fontName=FONT_SERIF_BOLD, fontSize=12.5, leading=16, textColor=PRIMARY))
    styles.add(ParagraphStyle(name='BodyMd', fontName=FONT_SANS, fontSize=8.5, leading=12.5, textColor=TEXT_ON_SURFACE, spaceAfter=2))
    styles.add(ParagraphStyle(name='TakeawayNum', fontName=FONT_SANS_BOLD, fontSize=9.5, leading=12, textColor=NUM_STEP_COLOR, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name='TakeawayBody', fontName=FONT_SANS, fontSize=8.5, leading=12.5, textColor=TEXT_ON_SURFACE))
    styles.add(ParagraphStyle(name='WinCalloutText', fontName=FONT_SANS, fontSize=8.5, leading=12.5, textColor=TEXT_ON_SURFACE))
    styles.add(ParagraphStyle(name='MetricLabelCaps', fontName=FONT_SANS_BOLD, fontSize=7.5, leading=9.5, textColor=TEXT_SECONDARY))
    styles.add(ParagraphStyle(name='MetricValMd', fontName=FONT_SERIF_BOLD, fontSize=15, leading=17, textColor=PRIMARY))
    styles.add(ParagraphStyle(name='MetricDeltaGreen', fontName=FONT_SANS_BOLD, fontSize=8, leading=10, textColor=GREEN_TEXT))
    styles.add(ParagraphStyle(name='MetricDeltaRed', fontName=FONT_SANS_BOLD, fontSize=8, leading=10, textColor=RED_TEXT))
    styles.add(ParagraphStyle(name='MetricDeltaNeutral', fontName=FONT_SANS_BOLD, fontSize=8, leading=10, textColor=TEXT_SECONDARY))
    styles.add(ParagraphStyle(name='ThAction', fontName=FONT_SANS_BOLD, fontSize=7.5, leading=9.5, textColor=TEXT_SECONDARY, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name='ThStat', fontName=FONT_SANS_BOLD, fontSize=7.5, leading=9.5, textColor=TEXT_SECONDARY, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='TdAction', fontName=FONT_SANS, fontSize=8, leading=11, textColor=TEXT_ON_SURFACE, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name='TdStat', fontName=FONT_SANS, fontSize=8, leading=11, textColor=TEXT_ON_SURFACE, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='TdStatGreen', fontName=FONT_SANS_BOLD, fontSize=8, leading=11, textColor=GREEN_TEXT, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='TdStatRed', fontName=FONT_SANS_BOLD, fontSize=8, leading=11, textColor=RED_TEXT, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='TdStatAccent', fontName=FONT_SANS_BOLD, fontSize=8, leading=11, textColor=NUM_STEP_COLOR, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='DiscoveryTitle', fontName=FONT_SANS_BOLD, fontSize=8.5, leading=11.5, textColor=PRIMARY))
    styles.add(ParagraphStyle(name='DiscoveryDesc', fontName=FONT_SANS, fontSize=8, leading=11.5, textColor=TEXT_ON_SURFACE))
    styles.add(ParagraphStyle(name='ActionPlanTitle', fontName=FONT_SANS_BOLD, fontSize=8.5, leading=11.5, textColor=TEXT_ON_SURFACE))
    styles.add(ParagraphStyle(name='ActionPlanDesc', fontName=FONT_SANS, fontSize=8, leading=11.5, textColor=TEXT_SECONDARY))
    styles.add(ParagraphStyle(name='ActionTag', fontName=FONT_SANS_BOLD, fontSize=7.5, leading=10, textColor=RED_TEXT, alignment=TA_RIGHT))

    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        rightMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        title=f'{briefing.company_name} - {report_title}',
        author='Growth Intelligence Reporting',
    )

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(BORDER_TERTIARY_20)
        canvas.setLineWidth(0.75)
        canvas.line(doc.leftMargin, 0.4 * inch, LETTER[0] - doc.rightMargin, 0.4 * inch)
        canvas.setFont(FONT_SANS, 7.5)
        canvas.setFillColor(TEXT_SECONDARY)
        # Helvetica's built-in encoding can turn Unicode punctuation into control
        # characters in extracted PDFs; keep the footer portable and readable.
        canvas.drawString(doc.leftMargin, 0.26 * inch, f'(c) {datetime.now().year} Executive Insights | {briefing.company_name}')
        canvas.drawRightString(LETTER[0] - doc.rightMargin, 0.26 * inch, f'Page {doc.page}')
        canvas.restoreState()

    story = []

    # 1. Editorial Header Section with Solid Brand Accent Rule
    story.append(HRFlowable(width='100%', thickness=3, color=ACCENT, spaceBefore=0, spaceAfter=8))
    story.append(Paragraph(escape(briefing.company_name).upper(), styles['CompanyEyebrow']))
    story.append(Paragraph(escape(report_title), styles['MainDisplayTitle']))
    story.append(Paragraph(escape(briefing.period_label), styles['PeriodSubtitle']))
    story.append(HRFlowable(width='100%', thickness=1, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=7))
    if briefing.report_mode == ReportMode.INITIAL_BASELINE:
        baseline_note = (
            f"Initial Measurement Baseline: observed data from "
            f"{briefing.observation_window_start or briefing.analytics.period_start} through "
            f"{briefing.observation_window_end or briefing.analytics.period_end}. "
            "The earlier comparison window is suppressed because it does not represent a full trustworthy measurement period. "
            "Current values are shown without prior-period values or growth deltas."
        )
        t_baseline = Table([[Paragraph(escape(baseline_note), styles['BodyMd'])]], colWidths=[7.6 * inch])
        t_baseline.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF8E7')),
            ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#E6C978')),
            ('LINEBEFORE', (0, 0), (0, -1), 3.5, ACCENT),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether([t_baseline, Spacer(1, 6)]))

    # 2. Configured client goals
    configured_goals = [goal.strip() for goal in briefing.analytics.goals if isinstance(goal, str) and goal.strip()]
    goals_text = '<br/>'.join(
        f'{index}. {escape(goal)}' for index, goal in enumerate(configured_goals, start=1)
    ) if configured_goals else 'No specific client goals are configured.'
    t_goals = Table([[
        Paragraph(f'<b>CURRENT GOALS</b><br/>{goals_text}', styles['BodyMd'])
    ]], colWidths=[7.6 * inch])
    t_goals.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BG_SURFACE_LOW),
        ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
        ('LINEBEFORE', (0, 0), (0, -1), 3.5, ACCENT),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(KeepTogether([t_goals, Spacer(1, 6)]))

    # 3. Executive Snapshot Section
    story.append(Paragraph('EXECUTIVE SNAPSHOT', styles['SectionHeaderCaps']))
    story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=5))

    # 3-Column Executive Summary Grid
    takeaways = briefing.insights.executive_summary
    cols_content = []
    for idx, takeaway in enumerate(takeaways[:3], 1):
        cell_p = [
            Paragraph(f'<b>0{idx}</b>', styles['TakeawayNum']),
            Spacer(1, 2),
            Paragraph(escape(takeaway), styles['TakeawayBody']),
        ]
        cols_content.append(cell_p)

    # Fill to 3 columns if fewer
    while len(cols_content) < 3:
        cols_content.append([Paragraph('', styles['TakeawayBody'])])

    t_exec = Table([cols_content], colWidths=[2.53 * inch, 2.53 * inch, 2.53 * inch])
    t_exec.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t_exec)
    story.append(Spacer(1, 4))

    # Biggest Win Callout Card
    if briefing.insights.biggest_win:
        win_p = Paragraph(f'<b>★ BIGGEST WIN:</b> {escape(briefing.insights.biggest_win)}', styles['WinCalloutText'])
        t_win = Table([[win_p]], colWidths=[7.6 * inch])
        t_win.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BG_SURFACE_LOW),
            ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether([t_win, Spacer(1, 6)]))

    if briefing.insights.watch_item:
        watch_p = Paragraph(f'<b>AREA TO IMPROVE:</b> {escape(briefing.insights.watch_item)}', styles['WinCalloutText'])
        t_watch = Table([[watch_p]], colWidths=[7.6 * inch])
        t_watch.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BG_SURFACE_LOW),
            ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
            ('LINEBEFORE', (0, 0), (0, -1), 3.5, RED_TEXT),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether([t_watch, Spacer(1, 6)]))

    # 4. Core Growth Metrics Section
    head_left = Paragraph('Core Growth Metrics', styles['HeadlineSm'])
    head_right = Paragraph(
        'vs. Previous Period' if briefing.report_mode == ReportMode.COMPARISON else 'Current Observation Only',
        styles['ThStat'],
    )
    t_metrics_head = Table([[head_left, head_right]], colWidths=[5.0 * inch, 2.6 * inch])
    t_metrics_head.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t_metrics_head)

    core_metrics = briefing.analytics.core_metrics
    if core_metrics:
        kpi_cells = []
        for m in core_metrics[:5]:
            if m.current_value is None:
                val_s = 'Not available'
            elif m.unit == 'count':
                val_s = f'{int(m.current_value):,}'
            else:
                val_s = f'{m.current_value:.1f}%'
            if m.unit == 'currency' and m.current_value is not None:
                val_s = f'${m.current_value:,.2f}'

            if m.is_percentage_rate and m.percentage_points_change is not None:
                pct_s = f'{m.percentage_points_change:+.1f}% pts'
            elif m.percentage_change is not None:
                pct_s = f'{m.percentage_change:+.1f}%'
            elif m.prior_value is None:
                pct_s = 'baseline'
            else:
                pct_s = 'stable'

            if m.direction == 'up':
                arrow = '↑ '
                delta_style = styles['MetricDeltaGreen']
            elif m.direction == 'down':
                arrow = '↓ '
                delta_style = styles['MetricDeltaRed']
            elif m.prior_value is None:
                arrow = ''
                delta_style = styles['MetricDeltaNeutral']
            else:
                arrow = '→ '
                delta_style = styles['MetricDeltaNeutral']

            card_content = [
                Paragraph(escape(m.display_name).upper(), styles['MetricLabelCaps']),
                Spacer(1, 1),
                Paragraph(val_s, styles['MetricValMd']),
                Spacer(1, 1),
                Paragraph(f'{arrow}{pct_s}', delta_style),
            ]
            kpi_cells.append(card_content)

        num_cards = len(kpi_cells)
        cell_width = (7.6 * inch) / num_cards
        t_bento = Table([kpi_cells], colWidths=[cell_width] * num_cards)
        t_bento.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_OUTLINE_30),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(KeepTogether([t_bento, Spacer(1, 6)]))

    # 5. Key Actions & Engagement Table
    if briefing.analytics.conversion_events:
        conv_rows = [[
            Paragraph('Action / Goal', styles['ThAction']),
            Paragraph('This Period', styles['ThStat']),
            Paragraph('Prior Period' if briefing.report_mode == ReportMode.COMPARISON else 'Comparison', styles['ThStat']),
            Paragraph('Change' if briefing.report_mode == ReportMode.COMPARISON else 'Status', styles['ThStat']),
        ]]
        for ce in briefing.analytics.conversion_events[:5]:
            pct_str = f'{ce.percentage_change:+.1f}%' if ce.percentage_change is not None else 'baseline'
            if ce.direction == 'up':
                dir_style = styles['TdStatGreen']
                dir_icon = '↑ '
            elif ce.direction == 'down':
                dir_style = styles['TdStatRed']
                dir_icon = '↓ '
            elif ce.prior_count is None:
                dir_style = styles['TdStat']
                dir_icon = ''
            else:
                dir_style = styles['TdStat']
                dir_icon = '→ '

            conv_rows.append([
                Paragraph(escape(ce.display_name), styles['TdAction']),
                Paragraph(f'{ce.current_count:,}' if ce.current_count is not None else 'Not available', styles['TdStat']),
                Paragraph(f'{ce.prior_count:,}' if ce.prior_count is not None else 'Not available', styles['TdStat']),
                Paragraph(f'{dir_icon}{pct_str}', dir_style),
            ])

        t_conv = Table(conv_rows, colWidths=[3.4 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch], repeatRows=1)
        t_conv.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BG_SURFACE_LOW),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, BORDER_OUTLINE_30),
            ('LINEBELOW', (0, 1), (-1, -1), 0.5, BORDER_OUTLINE_30),
            ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(Paragraph('KEY INQUIRY ACTIONS &amp; ENGAGEMENT', styles['SectionHeaderCaps']))
        story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
        story.append(KeepTogether([t_conv, Spacer(1, 6)]))
        if briefing.insights.conversion_insights.strip():
            story.append(Paragraph(escape(briefing.insights.conversion_insights), styles['BodyMd']))
            story.append(Spacer(1, 5))

    # 6. Delivery health sections. These use only redacted aggregate rows;
    # provider IDs, recipients, credentials, and raw diagnostics never enter
    # the client PDF.
    def _delivery_status(model) -> str:
        value = getattr(model, 'status', '') if model is not None else ''
        return str(getattr(value, 'value', value) or '').strip().lower()

    def _delivery_note(status: str, website: bool = False) -> str:
        if status == 'partial':
            return (
                'Some tracked activity was unavailable for this window; displayed figures are partial.'
                if not website
                else 'Some inquiry-notification activity was unavailable for this window; displayed figures are partial.'
            )
        if status in {'empty', 'unavailable', 'error'}:
            return (
                'Delivery metrics are not available for this window.'
                if not website
                else 'Website inquiry delivery metrics are not available for this window.'
            )
        return (
            'Open and click figures are estimated tracking signals, not inbox confirmation.'
            if not website
            else 'These figures describe notification delivery, not appointments or confirmed leads.'
        )

    report_delivery = briefing.report_delivery_metrics
    if report_delivery is not None and _delivery_status(report_delivery) != 'not_configured':
        rows = report_delivery_metric_rows(report_delivery)
        delivery_table_rows = [[Paragraph('Metric', styles['ThAction']), Paragraph('Value', styles['ThStat'])]]
        delivery_table_rows.extend([
            [Paragraph(escape(label), styles['TdAction']), Paragraph(escape(value), styles['TdStat'])]
            for label, value in rows
        ] or [[Paragraph('Activity', styles['TdAction']), Paragraph('Not available', styles['TdStat'])]])
        t_delivery = Table(delivery_table_rows, colWidths=[5.4 * inch, 2.2 * inch], repeatRows=1)
        t_delivery.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BG_SURFACE_LOW),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, BORDER_OUTLINE_30),
            ('LINEBELOW', (0, 1), (-1, -1), 0.5, BORDER_OUTLINE_30),
            ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(Paragraph('ANALYTICS REPORT DELIVERY', styles['SectionHeaderCaps']))
        story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
        story.append(Paragraph(
            f'Email delivery health for this analytics report. {_delivery_note(_delivery_status(report_delivery))}',
            styles['BodyMd'],
        ))
        story.append(KeepTogether([t_delivery, Spacer(1, 3)]))

    website_delivery = briefing.analytics.website_inquiry_metrics
    if website_delivery is not None and _delivery_status(website_delivery) != 'not_configured':
        rows = website_inquiry_metric_rows(website_delivery)
        inquiry_table_rows = [[
            Paragraph('Metric', styles['ThAction']),
            Paragraph('This Period', styles['ThStat']),
            Paragraph('Prior Period', styles['ThStat']),
        ]]
        inquiry_table_rows.extend([
            [Paragraph(escape(label), styles['TdAction']), Paragraph(escape(current), styles['TdStat']), Paragraph(escape(prior), styles['TdStat'])]
            for label, current, prior in rows
        ] or [[Paragraph('Activity', styles['TdAction']), Paragraph('Not available', styles['TdStat']), Paragraph('Not available', styles['TdStat'])]])
        t_inquiry_delivery = Table(inquiry_table_rows, colWidths=[3.6 * inch, 2.0 * inch, 2.0 * inch], repeatRows=1)
        t_inquiry_delivery.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BG_SURFACE_LOW),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, BORDER_OUTLINE_30),
            ('LINEBELOW', (0, 1), (-1, -1), 0.5, BORDER_OUTLINE_30),
            ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(Paragraph('WEBSITE INQUIRY DELIVERY', styles['SectionHeaderCaps']))
        story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
        story.append(Paragraph(
            f'Technical notification delivery health. {_delivery_note(_delivery_status(website_delivery), website=True)}',
            styles['BodyMd'],
        ))
        story.append(KeepTogether([t_inquiry_delivery, Spacer(1, 3)]))

    # 7. Traffic & Visitor Inflow Insights Narrative
    story.append(Paragraph('VISITOR INFLOW &amp; POPULAR PAGES', styles['SectionHeaderCaps']))
    story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
    story.append(Paragraph(escape(briefing.insights.traffic_and_inflow_insights), styles['BodyMd']))
    story.append(Spacer(1, 5))

    # 6. Search opportunity narrative and deterministic supporting rows
    seo_insights = briefing.insights.seo_and_content_opportunities.strip()
    keywords = briefing.analytics.striking_distance_keywords[:5]
    if seo_insights or keywords:
        search_heading = 'SEARCH OPPORTUNITIES' if keywords else 'SEARCH &amp; CONTENT TOPICS TO VALIDATE'
        story.append(Paragraph(search_heading, styles['SectionHeaderCaps']))
        story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
        if seo_insights:
            story.append(Paragraph(escape(seo_insights), styles['BodyMd']))
            story.append(Spacer(1, 4))
        if keywords:
            keyword_rows = [[
                Paragraph('Search Term', styles['ThAction']),
                Paragraph('Search Views', styles['ThStat']),
                Paragraph('Google Rank', styles['ThStat']),
                Paragraph('Opportunity', styles['ThStat']),
            ]]
            for keyword in keywords:
                keyword_rows.append([
                    Paragraph(escape(scrub_gsc_query(keyword.query)), styles['TdAction']),
                    Paragraph(f'{keyword.impressions:,}', styles['TdStat']),
                    Paragraph(f'{keyword.position:.1f}', styles['TdStat']),
                    Paragraph(f'{keyword.opportunity_score:.0f}', styles['TdStatAccent']),
                ])
            t_keywords = Table(keyword_rows, colWidths=[3.4 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch], repeatRows=1)
            t_keywords.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BG_SURFACE_LOW),
                ('LINEBELOW', (0, 0), (-1, 0), 0.75, BORDER_OUTLINE_30),
                ('LINEBELOW', (0, 1), (-1, -1), 0.5, BORDER_OUTLINE_30),
                ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ]))
            story.append(KeepTogether([t_keywords, Spacer(1, 5)]))

    # 7. Local discovery narrative
    local_insights = briefing.insights.local_seo_insights.strip()
    if local_insights:
        story.append(Paragraph('LOCAL REPUTATION &amp; MAPS', styles['SectionHeaderCaps']))
        story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
        story.append(Paragraph(escape(local_insights), styles['BodyMd']))
        story.append(Spacer(1, 5))

    # 8. Deterministic GBP evidence block. This keeps exact source values in
    # the client artifact even when the narrative wording changes.
    local = briefing.analytics.local_seo
    gbp_profile = profile_rows(local)
    gbp_performance = performance_rows(local)
    gbp_keywords = gbp_keyword_rows(local)
    gbp_reviews = review_rows(local)
    gbp_calls = calls_rows(local)
    if gbp_profile or gbp_performance or gbp_keywords or gbp_reviews or gbp_calls:
        story.append(Paragraph('GOOGLE BUSINESS PROFILE EVIDENCE', styles['SectionHeaderCaps']))
        story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
        if gbp_profile:
            profile_table = [[Paragraph(escape(label), styles['ThAction']), Paragraph(escape(value), styles['TdAction'])]
                             for label, value in gbp_profile]
            t_profile = Table(profile_table, colWidths=[1.5 * inch, 6.1 * inch])
            t_profile.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), BG_SURFACE_LOW),
                ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, BORDER_OUTLINE_30),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(Paragraph('PROFILE DETAILS', styles['HeadlineSm']))
            story.append(KeepTogether([t_profile, Spacer(1, 5)]))
        if gbp_performance:
            performance_table = [[
                Paragraph('Metric', styles['ThAction']),
                Paragraph('This Period', styles['ThStat']),
                Paragraph('Prior', styles['ThStat']),
                Paragraph('Change', styles['ThStat']),
            ]]
            for row in gbp_performance:
                change = (
                    'baseline'
                    if row['prior'] is None
                    else f"{row['change']:+.1f}%" if row['change'] is not None else 'No % baseline'
                )
                performance_table.append([
                    Paragraph(escape(row['label']), styles['TdAction']),
                    Paragraph(_gbp_pdf_number(row['current']), styles['TdStat']),
                    Paragraph(_gbp_pdf_number(row['prior']), styles['TdStat']),
                    Paragraph(escape(change), styles['TdStat']),
                ])
            t_performance = Table(performance_table, colWidths=[3.4 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch], repeatRows=1)
            t_performance.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BG_SURFACE_LOW),
                ('LINEBELOW', (0, 0), (-1, 0), 0.75, BORDER_OUTLINE_30),
                ('LINEBELOW', (0, 1), (-1, -1), 0.5, BORDER_OUTLINE_30),
                ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(Paragraph('PERFORMANCE METRICS', styles['HeadlineSm']))
            story.append(KeepTogether([t_performance, Spacer(1, 5)]))
        if gbp_keywords:
            keyword_table = [[Paragraph('Monthly Search Keyword', styles['ThAction']), Paragraph('Reported Value', styles['ThStat'])]]
            keyword_table.extend([
                [Paragraph(escape(row['keyword']), styles['TdAction']), Paragraph(escape(row['value']), styles['TdStat'])]
                for row in gbp_keywords
            ])
            t_keywords_gbp = Table(keyword_table, colWidths=[5.2 * inch, 2.4 * inch], repeatRows=1)
            t_keywords_gbp.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BG_SURFACE_LOW),
                ('LINEBELOW', (0, 0), (-1, 0), 0.75, BORDER_OUTLINE_30),
                ('LINEBELOW', (0, 1), (-1, -1), 0.5, BORDER_OUTLINE_30),
                ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(Paragraph('MONTHLY GBP SEARCH KEYWORDS', styles['HeadlineSm']))
            story.append(KeepTogether([t_keywords_gbp, Spacer(1, 5)]))
        if gbp_reviews:
            review_table = [[
                Paragraph('Rating', styles['ThAction']),
                Paragraph('Reply', styles['ThAction']),
                Paragraph('Updated', styles['ThAction']),
                Paragraph('Recent Comment', styles['ThAction']),
            ]]
            review_table.extend([
                [Paragraph(escape(row['rating']), styles['TdAction']),
                 Paragraph(escape(row['reply_status']), styles['TdAction']),
                 Paragraph(escape(row['updated']), styles['TdAction']),
                 Paragraph(escape(row['comment']), styles['TdAction'])]
                for row in gbp_reviews
            ])
            t_reviews = Table(review_table, colWidths=[0.7 * inch, 1.1 * inch, 1.0 * inch, 4.8 * inch], repeatRows=1)
            t_reviews.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BG_SURFACE_LOW),
                ('LINEBELOW', (0, 0), (-1, 0), 0.75, BORDER_OUTLINE_30),
                ('LINEBELOW', (0, 1), (-1, -1), 0.5, BORDER_OUTLINE_30),
                ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ]))
            review_summary = local.review_response_summary or {}
            coverage = review_summary.get('reply_coverage_percent')
            summary = (
                f"{review_summary.get('review_count', len(local.reviews))} reviews; "
                f"{review_summary.get('unreplied_count', 0)} not replied; "
                f"{coverage:.1f}% reply coverage" if coverage is not None
                else f"{review_summary.get('review_count', len(local.reviews))} reviews; reply coverage not available"
            )
            story.append(Paragraph('MANAGED REVIEWS &amp; REPLY STATUS', styles['HeadlineSm']))
            story.append(Paragraph(escape(summary), styles['BodyMd']))
            story.append(KeepTogether([t_reviews, Spacer(1, 5)]))
        if gbp_calls:
            calls_text = ' | '.join(f'{label}: {value}' for label, value in gbp_calls)
            story.append(Paragraph('BUSINESS CALLS INSIGHTS', styles['HeadlineSm']))
            story.append(Paragraph(escape(calls_text), styles['BodyMd']))
            story.append(Spacer(1, 5))

    # 9. Client-facing opportunities derived from accepted deterministic findings
    deep_insights_enabled = bool(briefing.exploration_audit and briefing.exploration_audit.enabled)
    if deep_insights_enabled:
        client_discoveries = build_client_discovery_copies(
            briefing.insights.deep_discoveries,
            audit=briefing.exploration_audit,
            client_id=briefing.analytics.client_id,
            period_start=briefing.analytics.period_start,
            period_end=briefing.analytics.period_end,
        )

        discovery_header = [
            Paragraph("WHERE WE'RE FOCUSING NEXT", styles['SectionHeaderCaps']),
            HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4),
            Paragraph(
                'These are opportunities identified from the current reporting period. Each item includes a practical next step for the practice.',
                styles['BodyMd'],
            ),
            Spacer(1, 3),
        ]
        if client_discoveries:
            for index, client_copy in enumerate(client_discoveries):
                disc_box = [
                    Paragraph(f'<b>{escape(client_copy.title.upper())}</b>', styles['DiscoveryTitle']),
                    Spacer(1, 2),
                    Paragraph(
                        f'<b>What we noticed:</b> {escape(client_copy.what_we_noticed)}<br/>'
                        f'<b>Recommended next step:</b> {escape(client_copy.recommended_next_step)}',
                        styles['DiscoveryDesc'],
                    ),
                ]
                t_disc = Table([[disc_box]], colWidths=[7.6 * inch])
                t_disc.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), colors.white),
                    ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
                    ('LINEBEFORE', (0, 0), (0, -1), 3.5, PRIMARY),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('LEFTPADDING', (0, 0), (-1, -1), 8),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ]))
                story.append(KeepTogether(
                    (discovery_header if index == 0 else []) + [t_disc, Spacer(1, 4)]
                ))
        else:
            note = Table([[Paragraph(
                'No additional opportunities were identified from this period, and no recommendations were added.',
                styles['DiscoveryDesc'],
            )]], colWidths=[7.6 * inch])
            note.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), BG_SURFACE_LOW),
                ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ]))
            story.append(KeepTogether(discovery_header + [note, Spacer(1, 4)]))

    # 10. Practice Growth Action Plan
    story.append(Paragraph('RECOMMENDED NEXT ACTIONS', styles['SectionHeaderCaps']))
    story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
    selected_actions = select_strongest_actions(
        briefing.insights.agency_action_plan,
        REPORT_SPECS[briefing.report_type].max_actions,
    )
    for item in selected_actions:
        badge_label = format_client_friendly_priority(item.priority)
        head_table = Table([[
            Paragraph(f'<b>{escape(item.title)}</b>', styles['ActionPlanTitle']),
            Paragraph(badge_label, styles['ActionTag']),
        ]], colWidths=[5.4 * inch, 2.0 * inch])
        head_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        action_box = [
            head_table,
            Paragraph(escape(item.description), styles['ActionPlanDesc']),
        ]
        t_act = Table([[action_box]], colWidths=[7.6 * inch])
        t_act.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('BOX', (0, 0), (-1, -1), 0.75, BORDER_OUTLINE_30),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether([t_act, Spacer(1, 3)]))

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
