from __future__ import annotations

from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from typing import List

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
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


def hex_to_reportlab_color(hex_str: str, default: str = "#1E3A8A") -> colors.HexColor:
    try:
        return colors.HexColor(hex_str)
    except Exception:
        return colors.HexColor(default)


def is_light_hex(hex_str: str) -> bool:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return ((0.299 * r + 0.587 * g + 0.114 * b) / 255.0) > 0.65
    except Exception:
        return False


def build_executive_pdf(briefing: FullGrowthBriefing) -> bytes:
    """Generate a high-end, branded executive PDF performance briefing."""
    buffer = BytesIO()
    branding = briefing.branding
    primary_hex = branding.get("primary_color", "#1E3A8A")
    secondary_hex = branding.get("secondary_color", "#3B82F6")
    accent_hex = branding.get("accent_color", "#F59E0B")

    PRIMARY = hex_to_reportlab_color(primary_hex, "#1E3A8A")
    SECONDARY = hex_to_reportlab_color(secondary_hex, "#3B82F6")
    ACCENT = hex_to_reportlab_color(accent_hex, "#F59E0B")
    INK = colors.HexColor("#0F172A")
    MUTED = colors.HexColor("#64748B")
    LIGHT_BG = colors.HexColor("#F8FAFC")
    BORDER_COLOR = colors.HexColor("#E2E8F0")
    GREEN = colors.HexColor("#10B981")
    RED = colors.HexColor("#EF4444")

    primary_text_color = INK if is_light_hex(primary_hex) else colors.white
    secondary_text_color = INK if is_light_hex(secondary_hex) else colors.white

    report_title = "Weekly Growth Digest" if briefing.report_type == ReportType.WEEKLY else "Performance Report"

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DocHeader", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=PRIMARY, spaceAfter=2))
    styles.add(ParagraphStyle(name="DocSubHeader", fontName="Helvetica", fontSize=9.5, leading=12.5, textColor=MUTED, spaceAfter=10))
    styles.add(ParagraphStyle(name="SectionTitle", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=PRIMARY, spaceBefore=8, spaceAfter=5))
    styles.add(ParagraphStyle(name="BodyTextCustom", fontName="Helvetica", fontSize=8.5, leading=12, textColor=INK, spaceAfter=3))
    styles.add(ParagraphStyle(name="TakeawayBullet", fontName="Helvetica", fontSize=8.5, leading=12, textColor=INK, spaceBefore=1, spaceAfter=3, leftIndent=10))
    styles.add(ParagraphStyle(name="ActionTitle", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=INK))
    styles.add(ParagraphStyle(name="ActionDesc", fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED))

    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=f"{briefing.company_name} - {report_title}",
        author="Analytics Reporting",
    )

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(BORDER_COLOR)
        canvas.line(doc.leftMargin, 0.4 * inch, LETTER[0] - doc.rightMargin, 0.4 * inch)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 0.25 * inch, f"{briefing.company_name} | {report_title}")
        canvas.drawRightString(LETTER[0] - doc.rightMargin, 0.25 * inch, f"Page {doc.page}")
        canvas.restoreState()

    story = []

    # 1. Header
    story.append(Paragraph(escape(briefing.company_name), styles["DocHeader"]))
    story.append(Paragraph(f"{report_title} &bull; {briefing.period_label} &bull; Generated {briefing.generated_at}", styles["DocSubHeader"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceBefore=0, spaceAfter=8))

    # 2. Executive Snapshot
    story.append(Paragraph("EXECUTIVE SNAPSHOT", styles["SectionTitle"]))
    for takeaway in briefing.insights.executive_summary:
        story.append(Paragraph(f"&bull; {escape(takeaway)}", styles["TakeawayBullet"]))
    story.append(Spacer(1, 4))

    # 2b. Biggest Win & Watch Item Highlights
    if briefing.insights.biggest_win or briefing.insights.watch_item:
        highlight_boxes = []
        if briefing.insights.biggest_win:
            highlight_boxes.append([
                Paragraph("<b>BIGGEST WIN</b>", styles["ActionTitle"]),
                Paragraph(escape(briefing.insights.biggest_win), styles["BodyTextCustom"]),
            ])
        if briefing.insights.watch_item:
            highlight_boxes.append([
                Paragraph("<b>PRIMARY RISK / WATCH ITEM</b>", styles["ActionTitle"]),
                Paragraph(escape(briefing.insights.watch_item), styles["BodyTextCustom"]),
            ])
        t_high = Table(highlight_boxes, colWidths=[7.5 * inch])
        t_high.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 1, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(KeepTogether([t_high, Spacer(1, 6)]))

    # 3. Core Metrics Summary Table
    metric_header = ["Metric", "Current", "Prior", "Change", "Trend"]
    metric_table_data = [metric_header]
    for m in briefing.analytics.core_metrics:
        curr_s = f"{int(m.current_value):,}" if m.unit == "count" else f"{m.current_value:.1f}%"
        prior_s = f"{int(m.prior_value):,}" if m.unit == "count" else f"{m.prior_value:.1f}%"
        if m.is_percentage_rate and m.percentage_points_change is not None:
            pct_s = f"{m.percentage_points_change:+.1f}% pts"
        elif m.percentage_change is not None:
            pct_s = f"{m.percentage_change:+.1f}%"
        else:
            pct_s = "-"
        trend_s = m.direction.upper()
        metric_table_data.append([m.display_name, curr_s, prior_s, pct_s, trend_s])

    col_widths = [2.2 * inch, 1.3 * inch, 1.3 * inch, 1.4 * inch, 1.3 * inch]
    t_metrics = Table(metric_table_data, colWidths=col_widths, repeatRows=1)
    t_metrics.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), primary_text_color),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    period_metrics_label = f"PERFORMANCE METRICS ({briefing.analytics.period_days}-DAY CYCLE VS PRIOR)"
    story.append(KeepTogether([
        Paragraph(period_metrics_label, styles["SectionTitle"]),
        t_metrics,
        Spacer(1, 6),
    ]))

    # 4. Conversion Breakdown Table
    if briefing.analytics.conversion_events:
        conv_header = ["Conversion Action", "Current Count", "Prior Count", "Change", "Trend"]
        conv_table_data = [conv_header]
        for ce in briefing.analytics.conversion_events[:5]:
            pct_str = f"{ce.percentage_change:+.1f}%" if ce.percentage_change is not None else "-"
            conv_table_data.append([ce.display_name, f"{ce.current_count:,}", f"{ce.prior_count:,}", pct_str, ce.direction.upper()])
        t_conv = Table(conv_table_data, colWidths=[2.6 * inch, 1.2 * inch, 1.2 * inch, 1.2 * inch, 1.3 * inch], repeatRows=1)
        t_conv.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), SECONDARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), secondary_text_color),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(KeepTogether([
            Paragraph("KEY CONVERSION ACTIONS & ENGAGEMENT", styles["SectionTitle"]),
            t_conv,
            Spacer(1, 6),
        ]))

    # 5. Inflow & Channels Breakdown
    story.append(Paragraph("TRAFFIC & INFLOW INSIGHTS", styles["SectionTitle"]))
    story.append(Paragraph(escape(briefing.insights.traffic_and_inflow_insights), styles["BodyTextCustom"]))
    story.append(Spacer(1, 5))

    # 6. Striking Distance SEO Keywords Table
    kw_header = ["Target Search Query", "Impressions", "Clicks", "Avg Pos", "Opp Score"]
    kw_table_data = [kw_header]
    for kw in briefing.analytics.striking_distance_keywords[:6]:
        kw_table_data.append([
            kw.query[:38],
            f"{kw.impressions:,}",
            f"{kw.clicks:,}",
            f"{kw.position:.1f}",
            f"{kw.opportunity_score:.0f}",
        ])
    if len(kw_table_data) == 1:
        kw_table_data.append(["No striking distance queries found", "-", "-", "-", "-"])

    t_kw = Table(kw_table_data, colWidths=[2.8 * inch, 1.2 * inch, 1.1 * inch, 1.1 * inch, 1.3 * inch], repeatRows=1)
    t_kw.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SECONDARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), secondary_text_color),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(KeepTogether([
        Paragraph("STRIKING-DISTANCE SEARCH QUERIES (PAGE 2 OPPORTUNITIES)", styles["SectionTitle"]),
        t_kw,
        Spacer(1, 5),
    ]))

    # 7. SEO & Content Opportunities Narrative
    story.append(Paragraph(escape(briefing.insights.seo_and_content_opportunities), styles["BodyTextCustom"]))
    story.append(Spacer(1, 5))

    # 8. Local SEO & GBP Section (if applicable)
    if briefing.insights.local_seo_insights:
        story.append(Paragraph("LOCAL SEO & REPUTATION DYNAMICS", styles["SectionTitle"]))
        story.append(Paragraph(escape(briefing.insights.local_seo_insights), styles["BodyTextCustom"]))
        story.append(Spacer(1, 5))

    # 9. Autonomous Deep Discoveries (if present)
    if briefing.insights.deep_discoveries:
        story.append(Paragraph("AUTONOMOUS MULTI-SOURCE DEEP DISCOVERIES", styles["SectionTitle"]))
        for disc in briefing.insights.deep_discoveries:
            disc_box = [
                Paragraph(f"<b>{escape(disc.title)}</b> &bull; <font color='{secondary_hex}'><b>[{escape(disc.source)}]</b></font>", styles["ActionTitle"]),
                Paragraph(escape(disc.insight), styles["ActionDesc"]),
                Paragraph(f"<b>Action:</b> {escape(disc.recommended_action)}", styles["ActionDesc"]),
            ]
            t_disc = Table([[disc_box]], colWidths=[7.5 * inch])
            t_disc.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
                ("BOX", (0, 0), (-1, -1), 1, BORDER_COLOR),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(KeepTogether([t_disc, Spacer(1, 3)]))
        story.append(Spacer(1, 5))

    # 10. Agency Action Plan
    story.append(Paragraph("AGENCY GROWTH ACTION PLAN", styles["SectionTitle"]))
    for item in briefing.insights.agency_action_plan:
        ev_text = f"<br/><b>Evidence:</b> {escape(item.evidence)}" if item.evidence else ""
        action_box = [
            Paragraph(f"<b>{escape(item.title)}</b> &bull; <font color='{primary_hex}'><b>[{item.impact_area} | {item.priority} Priority]</b></font>", styles["ActionTitle"]),
            Paragraph(f"{escape(item.description)}{ev_text}", styles["ActionDesc"]),
        ]
        t_act = Table([[action_box]], colWidths=[7.5 * inch])
        t_act.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 1, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(KeepTogether([t_act, Spacer(1, 3)]))

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
