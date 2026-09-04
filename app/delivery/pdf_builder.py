from __future__ import annotations

import os
from io import BytesIO
from typing import Optional, Sequence
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from app.ai.privacy import scrub_gsc_query

from app.analytics.contracts import FullGrowthBriefing, REPORT_SPECS, ReportMode, ReportType
from app.delivery.discovery_copy import build_client_discovery_copies
from app.delivery.email_components import (
    copyright_year,
    has_report_delivery_data,
    has_website_inquiry_data,
    is_light_color,
    report_delivery_metric_rows,
    select_strongest_actions,
    website_inquiry_metric_rows,
)
from app.delivery.gbp_reporting import calls_rows, keyword_rows as gbp_keyword_rows, performance_rows, profile_rows, review_rows

AGENCY_NAME = 'Vector Studios'


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

# Ledger-cards palette, matching the HTML emails.
INK = colors.HexColor('#1A1A1A')
BODY = colors.HexColor('#3D3D42')
MUTED = colors.HexColor('#6B6B70')
FAINT = colors.HexColor('#9A968E')
HAIRLINE = colors.HexColor('#E6E2DA')
TAG_OUTLINE = colors.HexColor('#C9C5BC')
WIN = colors.HexColor('#1E7F4F')
WATCH = colors.HexColor('#B3261E')
CARD_FALLBACK = colors.HexColor('#F7F4EE')

PAGE_W, PAGE_H = LETTER
SIDE_MARGIN = 48
CONTENT_W = PAGE_W - 2 * SIDE_MARGIN
FOOTER_BASELINE = 34
FOOTER_RULE_Y = 50
FRAME_BOTTOM = 64
FIRST_FRAME_TOP = PAGE_H - 36
LATER_FRAME_TOP = PAGE_H - 78

# Section labels are drawn in caps; these are the source strings.
SECTION_EXECUTIVE = 'Executive overview'
SECTION_INQUIRIES = 'Customer inquiries & key actions'
SECTION_TRAFFIC = 'Where visitors came from & what they viewed'
SECTION_LOCAL = 'Local reputation & maps'
SECTION_DISCOVERIES = "Where we're focusing next"
SECTION_REPORT_DELIVERY = 'Analytics report delivery'
SECTION_WEBSITE_INQUIRY = 'Website inquiry delivery'
SECTION_ACTIONS = 'Recommended next actions'
SECTION_GOALS = 'Current goals'

_GBP_CONNECTED_STATUSES = {'available', 'partial'}


def hex_to_reportlab_color(hex_str: str, default: str = '#0A0A0B') -> colors.HexColor:
    try:
        return colors.HexColor(hex_str)
    except Exception:
        return colors.HexColor(default)


def card_surface_color(secondary_hex: str) -> colors.HexColor:
    """Cards must stay light enough for dark body text, whatever the brand palette is."""
    return hex_to_reportlab_color(secondary_hex, '#F7F4EE') if is_light_color(secondary_hex) else CARD_FALLBACK


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


def _tracked_width(text: str, font: str, size: float, tracking: float) -> float:
    return pdfmetrics.stringWidth(text, font, size) + tracking * len(text)


def _draw_tracked(canvas, x: float, y: float, text: str, font: str, size: float,
                  color: colors.Color, tracking: float) -> None:
    """Draw letter-spaced text. Tracking lives on the text object, not the canvas."""
    text_object = canvas.beginText(x, y)
    text_object.setFont(font, size)
    text_object.setFillColor(color)
    text_object.setCharSpace(tracking)
    text_object.textOut(text)
    canvas.drawText(text_object)


def _flat(cell_padding: int = 0) -> list:
    return [
        ('LEFTPADDING', (0, 0), (-1, -1), cell_padding),
        ('RIGHTPADDING', (0, 0), (-1, -1), cell_padding),
        ('TOPPADDING', (0, 0), (-1, -1), cell_padding),
        ('BOTTOMPADDING', (0, 0), (-1, -1), cell_padding),
    ]


class _Dot(Flowable):
    """An 8pt status dot."""

    def __init__(self, color: colors.Color, diameter: float = 8):
        super().__init__()
        self.color = color
        self.diameter = diameter

    def wrap(self, availWidth, availHeight):
        return self.diameter, self.diameter

    def draw(self):
        self.canv.setFillColor(self.color)
        radius = self.diameter / 2
        self.canv.circle(radius, radius, radius, stroke=0, fill=1)


class _Checkbox(Flowable):
    """A 14pt outlined square used to mark each recommended action."""

    def wrap(self, availWidth, availHeight):
        return 14, 16

    def draw(self):
        self.canv.setStrokeColor(INK)
        self.canv.setLineWidth(1.5)
        self.canv.rect(0, 2, 14, 14, stroke=1, fill=0)


class _BleedPanel(Flowable):
    """A band whose background paints to the page edges, past the frame margins.

    Flowables are not clipped to their frame, so drawing from -bleed to
    width + bleed gives the full-bleed dark strip the design calls for while
    the inner content still respects the 48pt side margin.
    """

    def __init__(self, content, *, background, bleed=SIDE_MARGIN, pad_top=0, pad_bottom=0,
                 rule_color=None, rule_height=0):
        super().__init__()
        self.content = content
        self.background = background
        self.bleed = bleed
        self.pad_top = pad_top
        self.pad_bottom = pad_bottom
        self.rule_color = rule_color
        self.rule_height = rule_height

    def wrap(self, availWidth, availHeight):
        self._sizes = [flowable.wrap(availWidth, availHeight) for flowable in self.content]
        self.width = availWidth
        self.height = self.pad_top + self.pad_bottom + self.rule_height + sum(h for _, h in self._sizes)
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(self.background)
        c.rect(-self.bleed, 0, self.width + 2 * self.bleed, self.height, stroke=0, fill=1)
        if self.rule_height and self.rule_color is not None:
            c.setFillColor(self.rule_color)
            c.rect(-self.bleed, 0, self.width + 2 * self.bleed, self.rule_height, stroke=0, fill=1)
        c.restoreState()
        y = self.height - self.pad_top
        for flowable, (_, height) in zip(self.content, self._sizes):
            y -= height
            flowable.drawOn(c, 0, y)


class _SectionLabel(Flowable):
    """Number, caps title, hairline to the right edge, and an optional status tag."""

    NUMBER_SIZE = 10.5
    TITLE_SIZE = 10.5
    TRACKING = 1.3
    TAG_SIZE = 9.5
    GAP = 10

    def __init__(self, number: int, title: str, accent: colors.Color, tag: Optional[str] = None):
        super().__init__()
        self.number = f'{number:02d}'
        self.title = title.upper()
        self.accent = accent
        self.tag = tag.upper() if tag else None

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        self.height = 14
        return self.width, self.height

    def draw(self):
        c = self.canv
        baseline = 3
        c.setFillColor(self.accent)
        c.setFont(FONT_SANS_BOLD, self.NUMBER_SIZE)
        c.drawString(0, baseline, self.number)

        x = pdfmetrics.stringWidth(self.number, FONT_SANS_BOLD, self.NUMBER_SIZE) + self.GAP
        _draw_tracked(c, x, baseline, self.title, FONT_SANS_BOLD, self.TITLE_SIZE, MUTED, self.TRACKING)

        rule_start = x + _tracked_width(self.title, FONT_SANS_BOLD, self.TITLE_SIZE, self.TRACKING) + self.GAP
        rule_end = self.width
        if self.tag:
            tag_w = _tracked_width(self.tag, FONT_SANS_BOLD, self.TAG_SIZE, self.TRACKING) + 16
            tag_x = self.width - tag_w
            rule_end = tag_x - self.GAP
            c.setStrokeColor(TAG_OUTLINE)
            c.setLineWidth(1)
            c.rect(tag_x, baseline - 4, tag_w, 15, stroke=1, fill=0)
            _draw_tracked(c, tag_x + 8, baseline, self.tag, FONT_SANS_BOLD, self.TAG_SIZE, MUTED, self.TRACKING)

        if rule_end > rule_start:
            c.setStrokeColor(HAIRLINE)
            c.setLineWidth(1)
            c.line(rule_start, baseline + 3, rule_end, baseline + 3)


def _numbered_canvas(company_name: str, year: str):
    """Canvas that defers page output so the footer can say 'Page n of N'."""

    class NumberedCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_states = []

        def showPage(self):
            self._saved_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_states)
            for number, state in enumerate(self._saved_states, start=1):
                self.__dict__.update(state)
                self._draw_footer(number, total)
                super().showPage()
            super().save()

        def _draw_footer(self, number: int, total: int):
            self.saveState()
            self.setStrokeColor(HAIRLINE)
            self.setLineWidth(1)
            self.line(SIDE_MARGIN, FOOTER_RULE_Y, PAGE_W - SIDE_MARGIN, FOOTER_RULE_Y)
            self.setFont(FONT_SANS, 10)
            self.setFillColor(MUTED)
            # Helvetica's built-in encoding can turn Unicode punctuation into
            # control characters in extracted PDFs; keep the footer ASCII.
            self.drawString(SIDE_MARGIN, FOOTER_BASELINE, f'Prepared by {AGENCY_NAME} - Confidential')
            self.drawRightString(
                PAGE_W - SIDE_MARGIN,
                FOOTER_BASELINE,
                f'(c) {year} {company_name} - Page {number} of {total}',
            )
            self.restoreState()

    return NumberedCanvas


def build_executive_pdf(briefing: FullGrowthBriefing) -> bytes:
    """Generate the Letter-size executive PDF in the ledger-cards design."""
    buffer = BytesIO()
    branding = briefing.branding
    primary_hex = branding.get('primary_color', '#0A0A0B') or '#0A0A0B'
    secondary_hex = branding.get('secondary_color', '#F7F4EE') or '#F7F4EE'
    accent_hex = branding.get('accent_color', '#C6A15B') or '#C6A15B'

    PRIMARY = hex_to_reportlab_color(primary_hex, '#0A0A0B')
    ACCENT = hex_to_reportlab_color(accent_hex, '#C6A15B')
    SURFACE = card_surface_color(secondary_hex)
    primary_is_light = is_light_color(primary_hex)
    STRIP_TEXT = INK if primary_is_light else colors.white
    STRIP_MUTED = MUTED if primary_is_light else colors.HexColor('#B8B3A8')

    analytics = briefing.analytics
    insights = briefing.insights
    is_baseline = briefing.report_mode == ReportMode.INITIAL_BASELINE
    company_name = briefing.company_name
    report_title = (
        'Initial Measurement Baseline'
        if is_baseline
        else '28-Day Performance Report'
        if briefing.report_type == ReportType.PERFORMANCE_28D
        else 'Weekly Growth Digest'
    )

    # --- Styles ---------------------------------------------------------------
    def style(name, **kwargs):
        kwargs.setdefault('fontName', FONT_SANS)
        return ParagraphStyle(name=name, **kwargs)

    S_WORDMARK = style('Wordmark', fontName=FONT_SERIF, fontSize=20, leading=24, textColor=INK)
    S_KICKER = style('Kicker', fontSize=10.5, leading=13, textColor=MUTED, alignment=TA_RIGHT)
    S_PERIOD = style('Period', fontSize=11.5, leading=15, textColor=ACCENT)
    S_TITLE = style('ReportTitle', fontName=FONT_SANS_BOLD, fontSize=32, leading=35, textColor=STRIP_TEXT)
    S_DEK = style('Dek', fontSize=13.5, leading=20, textColor=STRIP_MUTED)
    S_KPI_LABEL = style('KpiLabel', fontSize=9.5, leading=12, textColor=STRIP_MUTED)
    S_KPI_VALUE = style('KpiValue', fontName=FONT_SANS_BOLD, fontSize=34, leading=36, textColor=STRIP_TEXT)
    S_STRIP_NOTE = style('StripNote', fontSize=10.5, leading=14, textColor=STRIP_MUTED)
    S_NOTE_LABEL = style('NoteLabel', fontName=FONT_SANS_BOLD, fontSize=10, leading=14, textColor=ACCENT)
    S_NOTE_BODY = style('NoteBody', fontSize=12, leading=18, textColor=BODY)
    S_BODY = style('Body', fontSize=12.5, leading=20, textColor=BODY)
    S_EXEC_INDEX = style('ExecIndex', fontName=FONT_SANS_BOLD, fontSize=12.5, leading=19, textColor=INK)
    S_EXEC_TEXT = style('ExecText', fontSize=12.5, leading=19, textColor=BODY)
    S_CARD_TITLE = style('CardTitle', fontName=FONT_SANS_BOLD, fontSize=14, leading=19, textColor=INK)
    S_CARD_BODY = style('CardBody', fontSize=12, leading=18, textColor=BODY)
    S_TILE_VALUE = style('TileValue', fontName=FONT_SANS_BOLD, fontSize=24, leading=26, textColor=INK)
    S_TILE_LABEL = style('TileLabel', fontSize=11, leading=15, textColor=MUTED)
    S_SUBHEAD = style('Subhead', fontName=FONT_SANS_BOLD, fontSize=10, leading=14, textColor=MUTED)
    S_TH = style('Th', fontName=FONT_SANS_BOLD, fontSize=10, leading=13, textColor=MUTED, alignment=TA_LEFT)
    S_TH_R = style('ThR', fontName=FONT_SANS_BOLD, fontSize=10, leading=13, textColor=MUTED, alignment=TA_RIGHT)
    S_TD = style('Td', fontSize=11.5, leading=16, textColor=INK, alignment=TA_LEFT)
    S_TD_MUTED = style('TdMuted', fontSize=11.5, leading=16, textColor=MUTED, alignment=TA_LEFT)
    S_TD_R = style('TdR', fontSize=11.5, leading=16, textColor=INK, alignment=TA_RIGHT)
    S_TD_R_MUTED = style('TdRMuted', fontSize=11.5, leading=16, textColor=MUTED, alignment=TA_RIGHT)
    S_TD_R_ACCENT = style('TdRAccent', fontName=FONT_SANS_BOLD, fontSize=11.5, leading=16, textColor=ACCENT, alignment=TA_RIGHT)
    S_ACTION_TITLE = style('ActionTitle', fontName=FONT_SANS_BOLD, fontSize=13, leading=18, textColor=INK)
    S_ACTION_DESC = style('ActionDesc', fontSize=12, leading=18, textColor=MUTED)
    S_BAR_LABEL = style('BarLabel', fontSize=12, leading=15, textColor=INK)
    S_BAR_LABEL_ZERO = style('BarLabelZero', fontSize=12, leading=15, textColor=FAINT)
    S_BAR_VALUE = style('BarValue', fontName=FONT_SANS_BOLD, fontSize=12, leading=15, textColor=INK, alignment=TA_RIGHT)
    S_BAR_VALUE_ZERO = style('BarValueZero', fontSize=12, leading=15, textColor=FAINT, alignment=TA_RIGHT)
    S_DISCOVERY_TITLE = style('DiscoveryTitle', fontName=FONT_SANS_BOLD, fontSize=10.5, leading=14, textColor=ACCENT)
    S_PILL = style('Pill', fontSize=12, leading=16, textColor=INK)

    # --- Small builders -------------------------------------------------------
    def chip(text: str, background: colors.Color, foreground: colors.Color, size: float = 11) -> Table:
        chip_style = ParagraphStyle(
            name='Chip', fontName=FONT_SANS_BOLD, fontSize=size, leading=size + 3, textColor=foreground
        )
        table = Table([[Paragraph(text, chip_style)]], colWidths=[pdfmetrics.stringWidth(text, FONT_SANS_BOLD, size) + 18])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), background),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        return table

    def outlined_tag(text: str, color: colors.Color, border: colors.Color, size: float = 9.5) -> Table:
        tag_style = ParagraphStyle(
            name='Tag', fontName=FONT_SANS_BOLD, fontSize=size, leading=size + 3, textColor=color, alignment=TA_LEFT
        )
        table = Table([[Paragraph(text.upper(), tag_style)]],
                      colWidths=[pdfmetrics.stringWidth(text.upper(), FONT_SANS_BOLD, size) + 18])
        table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1, border),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        return table

    def card(dot_color: colors.Color, label: str, label_color: colors.Color,
             title: Optional[str], body: str, width: float) -> Table:
        """A cream card: status dot, caps label, optional title, narrative body."""
        head = Table(
            [[_Dot(dot_color), Paragraph(escape(label).upper(),
                                         ParagraphStyle(name='CardLabel', fontName=FONT_SANS_BOLD, fontSize=10,
                                                        leading=13, textColor=label_color))]],
            colWidths=[14, None],
        )
        head.setStyle(TableStyle(_flat() + [('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        inner = [head, Spacer(1, 8)]
        if title:
            inner.extend([Paragraph(escape(title), S_CARD_TITLE), Spacer(1, 6)])
        inner.append(Paragraph(escape(body), S_CARD_BODY))
        table = Table([[inner]], colWidths=[width])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), SURFACE),
            ('LEFTPADDING', (0, 0), (-1, -1), 20),
            ('RIGHTPADDING', (0, 0), (-1, -1), 20),
            ('TOPPADDING', (0, 0), (-1, -1), 14),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        return table

    def ledger_table(headers: Sequence[tuple[str, ParagraphStyle]],
                     rows: Sequence[Sequence[tuple[str, ParagraphStyle]]],
                     col_widths: Sequence[float]) -> Table:
        """A ruled table: heavy rule under the header, hairlines between rows."""
        data = [[Paragraph(text, cell_style) for text, cell_style in headers]]
        data.extend([[Paragraph(text, cell_style) for text, cell_style in row] for row in rows])
        table = Table(data, colWidths=list(col_widths), repeatRows=1)
        table.setStyle(TableStyle([
            ('LINEBELOW', (0, 0), (-1, 0), 1, INK),
            ('LINEBELOW', (0, 1), (-1, -1), 1, HAIRLINE),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        return table

    def bar_group(heading: str, rows: Sequence[tuple[str, Optional[int]]],
                  fill_color: colors.Color, width: float) -> list:
        """Horizontal bars scaled to the largest observed value in the set."""
        usable = [value for _, value in rows if value is not None]
        if not usable:
            return []
        peak = max(usable)
        label_w, value_w, gap = 96, 34, 10
        track_w = width - label_w - value_w - 2 * gap
        out = [Paragraph(escape(heading).upper(), S_SUBHEAD), Spacer(1, 10)]
        for label, value in rows:
            has_value = value is not None and value > 0
            fraction = (value / peak) if has_value and peak > 0 else 0.0
            fill_w = max(0.0, min(1.0, fraction)) * track_w
            if fill_w > 0:
                track = Table([['', '']], colWidths=[fill_w, max(0.01, track_w - fill_w)], rowHeights=[10])
                track.setStyle(TableStyle(_flat() + [
                    ('BACKGROUND', (0, 0), (0, 0), fill_color),
                    ('BACKGROUND', (1, 0), (1, 0), SURFACE),
                ]))
            else:
                track = Table([['']], colWidths=[track_w], rowHeights=[10])
                track.setStyle(TableStyle(_flat() + [('BACKGROUND', (0, 0), (-1, -1), SURFACE)]))
            row = Table(
                [[
                    Paragraph(escape(label), S_BAR_LABEL if has_value else S_BAR_LABEL_ZERO),
                    track,
                    Paragraph(f'{value:,}' if value is not None else 'n/a',
                              S_BAR_VALUE if has_value else S_BAR_VALUE_ZERO),
                ]],
                colWidths=[label_w, track_w + 2 * gap, value_w],
            )
            row.setStyle(TableStyle(_flat() + [
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (1, 0), (1, 0), gap),
                ('RIGHTPADDING', (1, 0), (1, 0), gap),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
            ]))
            out.append(row)
        return out

    def pill_rows(labels: Sequence[str], width: float) -> list:
        """Pack outlined pills across as many rows as they need."""
        pad_x, gap, size = 12, 8, 12
        packed: list[list[str]] = []
        row: list[str] = []
        used = 0.0
        for label in labels:
            pill_w = pdfmetrics.stringWidth(label, FONT_SANS, size) + 2 * pad_x + 2
            if row and used + gap + pill_w > width:
                packed.append(row)
                row, used = [], 0.0
            row.append(label)
            used += (gap if used else 0) + pill_w
        if row:
            packed.append(row)

        out = []
        for line in packed:
            cells, widths, styles = [], [], []
            for index, label in enumerate(line):
                if index:
                    cells.append('')
                    widths.append(gap)
                styles.append(('BOX', (len(cells), 0), (len(cells), 0), 1, HAIRLINE))
                cells.append(Paragraph(escape(label), S_PILL))
                widths.append(pdfmetrics.stringWidth(label, FONT_SANS, size) + 2 * pad_x + 2)
            cells.append('')
            widths.append(max(0.01, width - sum(widths)))
            table = Table([cells], colWidths=widths)
            table.setStyle(TableStyle(_flat() + styles + [
                ('LEFTPADDING', (0, 0), (-1, -1), pad_x),
                ('RIGHTPADDING', (0, 0), (-1, -1), pad_x),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            out.extend([table, Spacer(1, 8)])
        return out

    # --- Header strip ---------------------------------------------------------
    brand_row = Table(
        [[Paragraph(escape(company_name), S_WORDMARK), Paragraph('PERFORMANCE REPORT', S_KICKER)]],
        colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4],
    )
    brand_row.setStyle(TableStyle(_flat() + [('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                             ('BOTTOMPADDING', (0, 0), (-1, -1), 18)]))

    def kpi_value_markup(metric) -> str:
        if metric.current_value is None:
            return '<font size="20">Not available</font>'
        if metric.unit == 'percentage':
            return f'{metric.current_value:.1f}<font size="18">%</font>'
        if metric.unit == 'currency':
            return f'<font size="20">${metric.current_value:,.2f}</font>'
        return f'{int(metric.current_value):,}'

    def delta_chip(metric):
        """Change chip; a metric with no prior period is labelled a baseline."""
        if is_baseline or briefing.comparison_suppressed or metric.prior_value is None or metric.direction == 'unavailable':
            return chip('baseline', colors.HexColor('#2A2A2C') if not primary_is_light else HAIRLINE,
                        STRIP_MUTED)
        if metric.is_percentage_rate and metric.percentage_points_change is not None:
            magnitude = f'{abs(metric.percentage_points_change):.1f} pts'
        elif metric.percentage_change is not None:
            magnitude = f'{abs(metric.percentage_change):.1f}%'
        else:
            return chip('stable', colors.HexColor('#2A2A2C') if not primary_is_light else HAIRLINE, STRIP_MUTED)
        if metric.direction == 'up':
            background = colors.HexColor('#DCF0E4') if primary_is_light else colors.HexColor('#1C3A2A')
            foreground = colors.HexColor('#14603A') if primary_is_light else colors.HexColor('#7FD6A3')
            return chip(f'+ {magnitude}', background, foreground)
        if metric.direction == 'down':
            background = colors.HexColor('#FBE2DF') if primary_is_light else colors.HexColor('#3F1F1D')
            foreground = colors.HexColor('#93221B') if primary_is_light else colors.HexColor('#F2A19A')
            return chip(f'- {magnitude}', background, foreground)
        return chip(magnitude, colors.HexColor('#2A2A2C') if not primary_is_light else HAIRLINE, STRIP_MUTED)

    kpi_metrics = analytics.core_metrics[:5]
    strip_content = [
        Paragraph(escape(briefing.period_label), S_PERIOD),
        Spacer(1, 10),
        Paragraph(escape(report_title), S_TITLE),
        Spacer(1, 10),
        Paragraph(
            escape(
                f'A first look at website visitors, customer inquiries, and growth opportunities for '
                f'{company_name}. No prior period is available yet, so figures are shown without change comparisons.'
                if is_baseline
                else f'A clear summary of website visitors, customer inquiries, and growth opportunities for {company_name}.'
            ),
            S_DEK,
        ),
        Spacer(1, 14),
    ]
    if kpi_metrics:
        column_w = CONTENT_W / len(kpi_metrics)
        # One row per band rather than one cell per metric: a label that wraps
        # then pushes every column's value down together, so the row stays level.
        kpi_table = Table(
            [
                [Paragraph(escape(m.display_name).upper(), S_KPI_LABEL) for m in kpi_metrics],
                [Paragraph(kpi_value_markup(m), S_KPI_VALUE) for m in kpi_metrics],
                [delta_chip(m) for m in kpi_metrics],
            ],
            colWidths=[column_w] * len(kpi_metrics),
        )
        kpi_table.setStyle(TableStyle(_flat() + [
            ('VALIGN', (0, 0), (-1, 0), 'BOTTOM'),
            ('VALIGN', (0, 1), (-1, -1), 'TOP'),
            ('RIGHTPADDING', (0, 0), (-2, -1), 20),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
        ]))
        strip_content.extend([kpi_table, Spacer(1, 10)])
    strip_content.append(Paragraph(
        escape(
            'Baseline period. Change comparisons begin with the next report.'
            if is_baseline or briefing.comparison_suppressed
            else f'Compared with the prior {analytics.period_days} days, '
                  f'{analytics.comparison_start} to {analytics.comparison_end}.'
        ),
        S_STRIP_NOTE,
    ))

    header_strip = _BleedPanel(
        strip_content,
        background=PRIMARY,
        pad_top=24,
        pad_bottom=22,
        rule_color=ACCENT,
        rule_height=3,
    )

    story: list = [NextPageTemplate('later'), brand_row, header_strip]

    if is_baseline:
        baseline_note = (
            'Initial Measurement Baseline: observed data from '
            f'{briefing.observation_window_start or analytics.period_start} through '
            f'{briefing.observation_window_end or analytics.period_end}. '
            'The earlier comparison window is suppressed because it does not represent a full trustworthy measurement period. '
            'Current values are shown without prior-period values or growth deltas.'
        )
        note_row = Table(
            [[Paragraph('NOTE', S_NOTE_LABEL), Paragraph(escape(baseline_note), S_NOTE_BODY)]],
            colWidths=[46, CONTENT_W - 46],
        )
        note_row.setStyle(TableStyle(_flat() + [('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        story.append(_BleedPanel([note_row], background=SURFACE, pad_top=10, pad_bottom=10))

    # Sections are numbered in render order so an absent data source never
    # leaves a gap in the sequence.
    section_number = 0

    def section(title: str, body: Sequence, *, tag: Optional[str] = None, space_before: int = 22) -> list:
        nonlocal section_number
        if not body:
            return []
        section_number += 1
        first_elements = body[0]._content if isinstance(body[0], KeepTogether) else [body[0]]
        header_flowable = KeepTogether([
            Spacer(1, space_before),
            _SectionLabel(section_number, title, ACCENT, tag),
            Spacer(1, 12),
            *first_elements,
        ])
        return [header_flowable, *body[1:]]

    # --- 01 Executive overview -------------------------------------------------
    overview: list = []
    if insights.executive_summary:
        exec_rows = [
            [Paragraph(str(index), S_EXEC_INDEX), Paragraph(escape(item), S_EXEC_TEXT)]
            for index, item in enumerate(insights.executive_summary, 1)
        ]
        exec_table = Table(exec_rows, colWidths=[26, CONTENT_W - 26])
        exec_table.setStyle(TableStyle(_flat() + [
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        overview.append(exec_table)

    card_w = (CONTENT_W - 12) / 2
    win_card = card(WIN, 'Biggest win', WIN, None, insights.biggest_win, card_w) if insights.biggest_win else None
    watch_card = card(WATCH, 'Area to improve', WATCH, None, insights.watch_item, card_w) if insights.watch_item else None
    if win_card and watch_card:
        pair = Table([[win_card, watch_card]], colWidths=[card_w + 6, card_w + 6])
        pair.setStyle(TableStyle(_flat() + [
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('RIGHTPADDING', (0, 0), (0, 0), 12),
        ]))
        # The pair moves as a unit; a card split across pages reads as two cards.
        overview.append(KeepTogether([Spacer(1, 12), pair]))
    elif win_card or watch_card:
        overview.append(KeepTogether([Spacer(1, 12), win_card or watch_card]))
    story.extend(section(SECTION_EXECUTIVE, overview, space_before=16))

    # --- 02 Customer inquiries & key actions -----------------------------------
    conversion_events = analytics.conversion_events[:4]
    inquiries: list = []
    tile_table = None
    if conversion_events:
        tile_w = (CONTENT_W - 10 * (len(conversion_events) - 1)) / len(conversion_events)
        tiles = []
        for event in conversion_events:
            value = f'{event.current_count:,}' if event.current_count is not None else 'Not available'
            if event.prior_count is None:
                trailer = 'baseline'
            elif event.percentage_change is not None:
                sign = '+' if event.direction == 'up' else '-' if event.direction == 'down' else ''
                trailer = f'{sign}{abs(event.percentage_change):.1f}% vs prior'
            else:
                trailer = f'{event.prior_count:,} prior'
            tiles.append([
                Paragraph(value, S_TILE_VALUE),
                Spacer(1, 8),
                Paragraph(escape(event.display_name), S_TILE_LABEL),
                Spacer(1, 4),
                Paragraph(escape(trailer), S_TILE_LABEL),
            ])
        # Tiles sit in alternating content/gutter columns so the cream
        # background stops at each tile edge instead of running together.
        cells, widths, tile_style = [], [], []
        for tile in tiles:
            if cells:
                cells.append('')
                widths.append(10)
            index = len(cells)
            tile_style.extend([
                ('BACKGROUND', (index, 0), (index, 0), SURFACE),
                ('LEFTPADDING', (index, 0), (index, 0), 16),
                ('RIGHTPADDING', (index, 0), (index, 0), 16),
                ('TOPPADDING', (index, 0), (index, 0), 14),
                ('BOTTOMPADDING', (index, 0), (index, 0), 14),
            ])
            cells.append(tile)
            widths.append(tile_w)
        tile_table = Table([cells], colWidths=widths)
        # Gutter columns keep zero padding so the row stays exactly CONTENT_W wide.
        tile_table.setStyle(TableStyle(_flat() + tile_style + [('VALIGN', (0, 0), (-1, -1), 'TOP')]))

    conv_text = (insights.conversion_insights or "").strip()
    if conv_text and tile_table is not None:
        inquiries.append(KeepTogether([
            Paragraph(escape(conv_text), S_BODY),
            Spacer(1, 14),
            tile_table,
        ]))
    elif conv_text:
        inquiries.append(Paragraph(escape(conv_text), S_BODY))
    elif tile_table is not None:
        inquiries.append(tile_table)
    story.extend(section(SECTION_INQUIRIES, inquiries, space_before=20))

    # --- 03 Traffic ------------------------------------------------------------
    traffic: list = [Paragraph(escape(insights.traffic_and_inflow_insights), S_BODY)]
    column_w = (CONTENT_W - 36) / 2
    channel_bars = bar_group(
        'Channels - sessions',
        [(channel.channel, channel.sessions) for channel in analytics.top_channels[:4]],
        PRIMARY,
        column_w,
    )
    page_bars = bar_group(
        'Top pages - sessions',
        [(page.page_path, page.sessions) for page in analytics.top_pages[:5]],
        ACCENT,
        column_w,
    )
    if channel_bars and page_bars:
        columns = Table([[channel_bars, page_bars]], colWidths=[column_w + 18, column_w + 18])
        columns.setStyle(TableStyle(_flat() + [
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('RIGHTPADDING', (0, 0), (0, 0), 36),
        ]))
        traffic.extend([Spacer(1, 18), columns])
    elif channel_bars or page_bars:
        traffic.extend([Spacer(1, 18), *(channel_bars or page_bars)])
    story.extend(section(SECTION_TRAFFIC, traffic, space_before=20))

    # --- 04 Search -------------------------------------------------------------
    keywords = analytics.striking_distance_keywords[:5]
    search: list = []
    if (insights.seo_and_content_opportunities or "").strip():
        search.append(Paragraph(escape(insights.seo_and_content_opportunities), S_BODY))
    if keywords:
        if search:
            search.append(Spacer(1, 16))
        search.append(ledger_table(
            [('SEARCH TERM', S_TH), ('SEARCH VIEWS', S_TH_R), ('GOOGLE RANK', S_TH_R), ('OPPORTUNITY', S_TH_R)],
            [
                [
                    (escape(scrub_gsc_query(keyword.query)), S_TD),
                    (f'{keyword.impressions:,}', S_TD_R_MUTED),
                    (f'{keyword.position:.1f}', S_TD_R),
                    (f'{keyword.opportunity_score:.0f}', S_TD_R_ACCENT),
                ]
                for keyword in keywords
            ],
            [CONTENT_W - 300, 100, 100, 100],
        ))
    search_title = 'High-opportunity Google searches' if keywords else 'Search & content topics to validate'
    story.extend(section(search_title, search, space_before=20))

    # --- 05 Local reputation & maps --------------------------------------------
    local = analytics.local_seo
    local_connected = any(
        str(getattr(local, field, '') or '').strip().lower() in _GBP_CONNECTED_STATUSES
        for field in ('profile_status', 'performance_status', 'search_keywords_status',
                      'reviews_status', 'business_calls_status')
    )
    local_body: list = []
    if (insights.local_seo_insights or "").strip():
        local_body.append(Paragraph(escape(insights.local_seo_insights), S_BODY))

    def subsection(heading: str, table: Table, note: Optional[str] = None) -> list:
        items = [Spacer(1, 16), Paragraph(escape(heading).upper(), S_SUBHEAD), Spacer(1, 8)]
        if note:
            items.extend([Paragraph(escape(note), S_TD_MUTED), Spacer(1, 8)])
        items.append(table)
        return [KeepTogether(items)]

    gbp_profile = profile_rows(local)
    if gbp_profile:
        local_body.extend(subsection('Profile details', ledger_table(
            [('FIELD', S_TH), ('VALUE', S_TH)],
            [[(escape(label), S_TD_MUTED), (escape(value), S_TD)] for label, value in gbp_profile],
            [150, CONTENT_W - 150],
        )))

    gbp_performance = performance_rows(local)
    if gbp_performance:
        local_body.extend(subsection('GBP performance metrics', ledger_table(
            [('METRIC', S_TH), ('THIS PERIOD', S_TH_R), ('PRIOR', S_TH_R), ('CHANGE', S_TH_R)],
            [
                [
                    (escape(row['label']), S_TD),
                    (_gbp_pdf_number(row['current']), S_TD_R),
                    (_gbp_pdf_number(row['prior']), S_TD_R_MUTED),
                    (
                        'baseline' if row['prior'] is None
                        else (f"{row['change']:+.1f}%" if row['change'] is not None else 'No % baseline'),
                        S_TD_R_MUTED,
                    ),
                ]
                for row in gbp_performance
            ],
            [CONTENT_W - 300, 100, 100, 100],
        )))

    gbp_keywords = gbp_keyword_rows(local)
    if gbp_keywords:
        local_body.extend(subsection('Monthly GBP search keywords', ledger_table(
            [('KEYWORD', S_TH), ('REPORTED VALUE', S_TH_R)],
            [[(escape(row['keyword']), S_TD), (escape(row['value']), S_TD_R_MUTED)] for row in gbp_keywords],
            [CONTENT_W - 160, 160],
        )))

    gbp_reviews = review_rows(local)
    if gbp_reviews:
        review_summary = local.review_response_summary or {}
        coverage = review_summary.get('reply_coverage_percent')
        summary = (
            f"{review_summary.get('review_count', len(local.reviews))} reviews; "
            f"{review_summary.get('unreplied_count', 0)} not replied; "
            f'{coverage:.1f}% reply coverage' if coverage is not None
            else f"{review_summary.get('review_count', len(local.reviews))} reviews; reply coverage not available"
        )
        local_body.extend(subsection('Managed reviews and reply status', ledger_table(
            [('RATING', S_TH), ('REPLY', S_TH), ('UPDATED', S_TH), ('RECENT COMMENT', S_TH)],
            [
                [
                    (escape(row['rating']), S_TD),
                    (escape(row['reply_status']), S_TD),
                    (escape(row['updated']), S_TD_MUTED),
                    (escape(row['comment']), S_TD_MUTED),
                ]
                for row in gbp_reviews
            ],
            [60, 100, 90, CONTENT_W - 250],
        ), note=summary))

    gbp_calls = calls_rows(local)
    if gbp_calls:
        local_body.append(KeepTogether([
            Spacer(1, 16),
            Paragraph('BUSINESS CALLS INSIGHTS', S_SUBHEAD),
            Spacer(1, 8),
            Paragraph(escape(' | '.join(f'{label}: {value}' for label, value in gbp_calls)), S_BODY),
        ]))
    story.extend(section(SECTION_LOCAL, local_body, tag=None if local_connected else 'Not connected', space_before=20))

    # --- Where we're focusing next ---------------------------------------------
    if briefing.exploration_audit and briefing.exploration_audit.enabled:
        discoveries: list = [
            Paragraph(
                'These are opportunities identified from the current reporting period. '
                'Each item includes a practical next step for the practice.',
                S_BODY,
            ),
            Spacer(1, 14),
        ]
        client_discoveries = build_client_discovery_copies(
            insights.deep_discoveries,
            audit=briefing.exploration_audit,
            client_id=analytics.client_id,
            period_start=analytics.period_start,
            period_end=analytics.period_end,
        )
        if client_discoveries:
            for client_copy in client_discoveries:
                inner = [
                    Paragraph(escape(client_copy.title.upper()), S_DISCOVERY_TITLE),
                    Spacer(1, 10),
                    Paragraph(f'<b>What we noticed:</b> {escape(client_copy.what_we_noticed)}', S_CARD_BODY),
                    Spacer(1, 8),
                    Paragraph(f'<b>Recommended next step:</b> {escape(client_copy.recommended_next_step)}', S_CARD_BODY),
                ]
                box = Table([[inner]], colWidths=[CONTENT_W])
                box.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), SURFACE),
                    ('LEFTPADDING', (0, 0), (-1, -1), 20),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 20),
                    ('TOPPADDING', (0, 0), (-1, -1), 18),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 18),
                ]))
                discoveries.extend([KeepTogether(box), Spacer(1, 10)])
        else:
            discoveries.append(Paragraph(
                'No additional opportunities were identified from this period, and no recommendations were added.',
                S_BODY,
            ))
        story.extend(section(SECTION_DISCOVERIES, discoveries, space_before=20))

    # --- Delivery health --------------------------------------------------------
    # These use only redacted aggregate rows; provider IDs, recipients,
    # credentials, and raw diagnostics never enter the client PDF.
    def delivery_status(model) -> str:
        value = getattr(model, 'status', '') if model is not None else ''
        return str(getattr(value, 'value', value) or '').strip().lower()

    def delivery_note(status: str, website: bool = False) -> str:
        if status == 'partial':
            return (
                'Some tracked activity was unavailable for this window; displayed figures are partial.'
                if not website
                else 'Some inquiry-notification activity was unavailable for this window; displayed figures are partial.'
            )
        if status == 'empty':
            return (
                'No tracked delivery activity was available for this window.'
                if not website
                else 'No website inquiry-notification activity was available for this window.'
            )
        if status in {'unavailable', 'error'}:
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
    if has_report_delivery_data(report_delivery):
        rows = report_delivery_metric_rows(report_delivery)
        delivery_table = ledger_table(
            [('METRIC', S_TH), ('VALUE', S_TH_R)],
            [[(escape(label), S_TD), (escape(value), S_TD_R)] for label, value in rows]
            or [[('Activity', S_TD), ('Not available', S_TD_R)]],
            [CONTENT_W - 160, 160],
        )
        body: list = [
            KeepTogether([
                Paragraph(
                    escape(f'Email delivery health for this analytics report. {delivery_note(delivery_status(report_delivery))}'),
                    S_BODY,
                ),
                Spacer(1, 14),
                delivery_table,
            ])
        ]
        story.extend(section(SECTION_REPORT_DELIVERY, body, space_before=20))

    website_delivery = analytics.website_inquiry_metrics
    if has_website_inquiry_data(website_delivery):
        rows = website_inquiry_metric_rows(website_delivery)
        inquiry_table = ledger_table(
            [('METRIC', S_TH), ('THIS PERIOD', S_TH_R), ('PRIOR PERIOD', S_TH_R)],
            [
                [(escape(label), S_TD), (escape(current), S_TD_R), (escape(prior), S_TD_R_MUTED)]
                for label, current, prior in rows
            ] or [[('Activity', S_TD), ('Not available', S_TD_R), ('Not available', S_TD_R_MUTED)]],
            [CONTENT_W - 240, 120, 120],
        )
        body = [
            KeepTogether([
                Paragraph(
                    escape(f'Technical notification delivery health. {delivery_note(delivery_status(website_delivery), website=True)}'),
                    S_BODY,
                ),
                Spacer(1, 14),
                inquiry_table,
            ])
        ]
        story.extend(section(SECTION_WEBSITE_INQUIRY, body, space_before=20))

    # --- Recommended next actions ------------------------------------------------
    selected_actions = select_strongest_actions(
        insights.agency_action_plan,
        REPORT_SPECS[briefing.report_type].max_actions,
    )
    actions: list = []
    for index, item in enumerate(selected_actions):
        is_last = index == len(selected_actions) - 1
        priority = (item.priority or '').strip().lower()
        tag_color = WATCH if priority == 'high' else MUTED
        tag_border = WATCH if priority == 'high' else TAG_OUTLINE
        row = Table(
            [[
                _Checkbox(),
                [
                    Paragraph(escape(item.title), S_ACTION_TITLE),
                    Spacer(1, 3),
                    Paragraph(escape(item.description), S_ACTION_DESC),
                ],
                outlined_tag(format_client_friendly_priority(item.priority), tag_color, tag_border),
            ]],
            colWidths=[22, CONTENT_W - 22 - 160, 160],
        )
        row.setStyle(TableStyle(_flat() + [
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
            ('LEFTPADDING', (1, 0), (1, 0), 14),
            ('RIGHTPADDING', (1, 0), (1, 0), 14),
            ('TOPPADDING', (0, 0), (-1, -1), 16),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 16),
        ] + ([] if is_last else [('LINEBELOW', (0, 0), (-1, -1), 1, HAIRLINE)])))
        actions.append(KeepTogether(row))
    story.extend(section(SECTION_ACTIONS, actions, space_before=20))

    # --- Current goals ------------------------------------------------------------
    configured_goals = [goal.strip() for goal in analytics.goals if isinstance(goal, str) and goal.strip()]
    goal_body = pill_rows(configured_goals or ['No specific client goals are configured.'], CONTENT_W)
    story.extend(section(SECTION_GOALS, goal_body, space_before=20))

    # --- Document -------------------------------------------------------------------
    def draw_later_header(canvas, doc):
        """Running header on every page after the first."""
        canvas.saveState()
        canvas.setFillColor(INK)
        canvas.setFont(FONT_SERIF, 16)
        canvas.drawString(SIDE_MARGIN, PAGE_H - 46, company_name)
        kicker = f'{report_title} - {briefing.period_label}'.upper()
        _draw_tracked(
            canvas,
            PAGE_W - SIDE_MARGIN - _tracked_width(kicker, FONT_SANS, 10.5, 1.3),
            PAGE_H - 46,
            kicker,
            FONT_SANS,
            10.5,
            MUTED,
            1.3,
        )
        canvas.setFillColor(PRIMARY)
        canvas.rect(SIDE_MARGIN, PAGE_H - 61, CONTENT_W, 3, stroke=0, fill=1)
        canvas.restoreState()

    document = BaseDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=SIDE_MARGIN,
        rightMargin=SIDE_MARGIN,
        topMargin=36,
        bottomMargin=FRAME_BOTTOM,
        title=f'{company_name} - {report_title}',
        author='Growth Intelligence Reporting',
    )
    frame_kwargs = dict(leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    document.addPageTemplates([
        PageTemplate(
            id='first',
            frames=[Frame(SIDE_MARGIN, FRAME_BOTTOM, CONTENT_W, FIRST_FRAME_TOP - FRAME_BOTTOM,
                          id='first', **frame_kwargs)],
        ),
        PageTemplate(
            id='later',
            frames=[Frame(SIDE_MARGIN, FRAME_BOTTOM, CONTENT_W, LATER_FRAME_TOP - FRAME_BOTTOM,
                          id='later', **frame_kwargs)],
            onPage=draw_later_header,
        ),
    ])
    document.build(story, canvasmaker=_numbered_canvas(company_name, copyright_year(analytics.period_end)))
    return buffer.getvalue()
