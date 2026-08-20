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

from app.analytics.contracts import FullGrowthBriefing, ReportType


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

    report_title = 'Monthly Intelligence Briefing' if briefing.report_type == ReportType.PERFORMANCE_28D else 'Weekly Growth Digest'

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
        canvas.drawString(doc.leftMargin, 0.26 * inch, f'© {datetime.now().year} Executive Insights • {briefing.company_name}')
        canvas.drawRightString(LETTER[0] - doc.rightMargin, 0.26 * inch, f'Page {doc.page}')
        canvas.restoreState()

    story = []

    # 1. Editorial Header Section with Solid Brand Accent Rule
    story.append(HRFlowable(width='100%', thickness=3, color=ACCENT, spaceBefore=0, spaceAfter=8))
    story.append(Paragraph(escape(briefing.company_name).upper(), styles['CompanyEyebrow']))
    story.append(Paragraph(escape(report_title), styles['MainDisplayTitle']))
    story.append(Paragraph(escape(briefing.period_label), styles['PeriodSubtitle']))
    story.append(HRFlowable(width='100%', thickness=1, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=7))

    # 2. Executive Snapshot Section
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

    # 3. Core Growth Metrics Section
    head_left = Paragraph('Core Growth Metrics', styles['HeadlineSm'])
    head_right = Paragraph('vs. Previous Period', styles['ThStat'])
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
            val_s = f'{int(m.current_value):,}' if m.unit == 'count' else f'{m.current_value:.1f}%'
            if m.unit == 'currency':
                val_s = f'${m.current_value:,.2f}'

            if m.is_percentage_rate and m.percentage_points_change is not None:
                pct_s = f'{m.percentage_points_change:+.1f}% pts'
            elif m.percentage_change is not None:
                pct_s = f'{m.percentage_change:+.1f}%'
            else:
                pct_s = '0.0%'

            if m.direction == 'up':
                arrow = '↑ '
                delta_style = styles['MetricDeltaGreen']
            elif m.direction == 'down':
                arrow = '↓ '
                delta_style = styles['MetricDeltaRed']
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

    # 4. Key Actions & Engagement Table
    if briefing.analytics.conversion_events:
        conv_rows = [[
            Paragraph('Action / Goal', styles['ThAction']),
            Paragraph('This Period', styles['ThStat']),
            Paragraph('Prior Period', styles['ThStat']),
            Paragraph('Change', styles['ThStat']),
        ]]
        for ce in briefing.analytics.conversion_events[:5]:
            pct_str = f'{ce.percentage_change:+.1f}%' if ce.percentage_change is not None else '-'
            if ce.direction == 'up':
                dir_style = styles['TdStatGreen']
                dir_icon = '↑ '
            elif ce.direction == 'down':
                dir_style = styles['TdStatRed']
                dir_icon = '↓ '
            else:
                dir_style = styles['TdStat']
                dir_icon = '→ '

            conv_rows.append([
                Paragraph(escape(ce.display_name), styles['TdAction']),
                Paragraph(f'{ce.current_count:,}', styles['TdStat']),
                Paragraph(f'{ce.prior_count:,}', styles['TdStat']),
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

    # 5. Traffic & Visitor Inflow Insights Narrative
    story.append(Paragraph('VISITOR INFLOW &amp; POPULAR PAGES', styles['SectionHeaderCaps']))
    story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
    story.append(Paragraph(escape(briefing.insights.traffic_and_inflow_insights), styles['BodyMd']))
    story.append(Spacer(1, 5))

    # 6. High-Impact Discoveries & Search Growth
    discoveries = briefing.insights.deep_discoveries
    story.append(Paragraph('Key Opportunities &amp; Discoveries', styles['HeadlineSm']))
    story.append(Spacer(1, 3))

    disc_items = []
    if discoveries:
        for disc in discoveries:
            disc_items.append((disc.title, disc.insight, disc.recommended_action))
    else:
        if briefing.analytics.striking_distance_keywords:
            top_k = briefing.analytics.striking_distance_keywords[0]
            disc_items.append(('High-Potential Search Terms', f'Google search activity highlights strong interest for "{top_k.query}", where your website currently ranks on page 2 with {top_k.impressions:,} search impressions.', 'Add helpful customer FAQs and service details to move this search into page 1 rankings.'))
        if briefing.insights.seo_and_content_opportunities:
            disc_items.append(('Search & Content Growth', briefing.insights.seo_and_content_opportunities, 'Publish dedicated customer answers and service overviews.'))
        if briefing.insights.local_seo_insights:
            disc_items.append(('Local Reputation & Maps', briefing.insights.local_seo_insights, 'Continue collecting 5-star Google reviews.'))

    for d_title, d_insight, d_rec in disc_items[:4]:
        rec_part = f'<br/><font color="{PRIMARY.hexval()}"><b>Next Step:</b></font> {escape(d_rec)}' if d_rec else ''
        disc_box = [
            Paragraph(f'<b>{escape(d_title)}</b>', styles['DiscoveryTitle']),
            Spacer(1, 2),
            Paragraph(f'{escape(d_insight)}{rec_part}', styles['DiscoveryDesc']),
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
        story.append(KeepTogether([t_disc, Spacer(1, 4)]))
    story.append(Spacer(1, 4))

    # 7. Practice Growth Action Plan
    story.append(Paragraph('RECOMMENDED NEXT ACTIONS', styles['SectionHeaderCaps']))
    story.append(HRFlowable(width='100%', thickness=0.75, color=BORDER_TERTIARY_20, spaceBefore=0, spaceAfter=4))
    for item in briefing.insights.agency_action_plan:
        ev_text = f'<br/><font color="{EYEBROW_COLOR.hexval()}"><b>Context:</b> {escape(item.evidence)}</font>' if item.evidence else ''
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
            Paragraph(f'{escape(item.description)}{ev_text}', styles['ActionPlanDesc']),
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

