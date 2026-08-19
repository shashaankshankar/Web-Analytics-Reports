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

from app.analytics.contracts import FullGrowthBriefing


def hex_to_reportlab_color(hex_str: str, default: str = "#1E3A8A") -> colors.HexColor:
    try:
        return colors.HexColor(hex_str)
    except Exception:
        return colors.HexColor(default)


def build_executive_pdf(briefing: FullGrowthBriefing) -> bytes:
    """Generate a high-end, branded executive PDF growth briefing."""
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

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DocHeader", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=PRIMARY, spaceAfter=2))
    styles.add(ParagraphStyle(name="DocSubHeader", fontName="Helvetica", fontSize=10, leading=13, textColor=MUTED, spaceAfter=12))
    styles.add(ParagraphStyle(name="SectionTitle", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=PRIMARY, spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="BodyTextCustom", fontName="Helvetica", fontSize=9, leading=13, textColor=INK, spaceAfter=4))
    styles.add(ParagraphStyle(name="TakeawayBullet", fontName="Helvetica", fontSize=9, leading=13, textColor=INK, spaceBefore=2, spaceAfter=4, leftIndent=12))
    styles.add(ParagraphStyle(name="ActionTitle", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=INK))
    styles.add(ParagraphStyle(name="ActionDesc", fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=MUTED))

    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=f"{briefing.company_name} - Performance Report",
        author="Analytics Reporting",
    )

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(BORDER_COLOR)
        canvas.line(doc.leftMargin, 0.4 * inch, LETTER[0] - doc.rightMargin, 0.4 * inch)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 0.25 * inch, f"{briefing.company_name} | Performance Report")
        canvas.drawRightString(LETTER[0] - doc.rightMargin, 0.25 * inch, f"Page {doc.page}")
        canvas.restoreState()

    story = []

    # 1. Header
    story.append(Paragraph(escape(briefing.company_name), styles["DocHeader"]))
    story.append(Paragraph(f"Performance Report &bull; {briefing.period_label} &bull; Generated {briefing.generated_at}", styles["DocSubHeader"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceBefore=0, spaceAfter=10))

    # 2. Executive Snapshot
    story.append(Paragraph("EXECUTIVE SNAPSHOT", styles["SectionTitle"]))
    for takeaway in briefing.insights.executive_summary:
        story.append(Paragraph(f"&bull; {escape(takeaway)}", styles["TakeawayBullet"]))
    story.append(Spacer(1, 6))

    # 3. Core Metrics Summary Table
    story.append(Paragraph("PERFORMANCE METRICS (28-DAY CYCLE VS PRIOR)", styles["SectionTitle"]))
    metric_header = ["Metric", "Current", "Prior", "Change", "Trend"]
    metric_table_data = [metric_header]
    for m in briefing.analytics.core_metrics:
        curr_s = f"{int(m.current_value):,}" if m.unit == "count" else f"{m.current_value:.1f}%"
        prior_s = f"{int(m.prior_value):,}" if m.unit == "count" else f"{m.prior_value:.1f}%"
        pct_s = f"{m.percentage_change:+.1f}%" if m.percentage_change is not None else "-"
        trend_s = m.direction.upper()
        metric_table_data.append([m.display_name, curr_s, prior_s, pct_s, trend_s])

    col_widths = [2.2 * inch, 1.3 * inch, 1.3 * inch, 1.4 * inch, 1.3 * inch]
    t_metrics = Table(metric_table_data, colWidths=col_widths, repeatRows=1)
    t_metrics.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_metrics)
    story.append(Spacer(1, 8))

    # 4. Inflow & Channels Breakdown
    story.append(Paragraph("TRAFFIC & INFLOW INSIGHTS", styles["SectionTitle"]))
    story.append(Paragraph(escape(briefing.insights.traffic_and_inflow_insights), styles["BodyTextCustom"]))
    story.append(Spacer(1, 6))

    # 5. Striking Distance SEO Keywords Table
    story.append(Paragraph("STRIKING-DISTANCE SEARCH QUERIES (PAGE 2 OPPORTUNITIES)", styles["SectionTitle"]))
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
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_kw)
    story.append(Spacer(1, 6))

    # 6. SEO & Content Opportunities Narrative
    story.append(Paragraph(escape(briefing.insights.seo_and_content_opportunities), styles["BodyTextCustom"]))
    story.append(Spacer(1, 6))

    # 7. Local SEO & GBP Section (if applicable)
    if briefing.insights.local_seo_insights:
        story.append(Paragraph("LOCAL SEO & REPUTATION DYNAMICS", styles["SectionTitle"]))
        story.append(Paragraph(escape(briefing.insights.local_seo_insights), styles["BodyTextCustom"]))
        story.append(Spacer(1, 6))

    # 7b. Autonomous Deep Discoveries (if present)
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
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(KeepTogether([t_disc, Spacer(1, 4)]))
        story.append(Spacer(1, 6))

    # 8. Agency Action Plan (Justifying Retainer)
    story.append(Paragraph("AGENCY GROWTH ACTION PLAN (UPCOMING MONTH)", styles["SectionTitle"]))
    for item in briefing.insights.agency_action_plan:
        action_box = [
            Paragraph(f"<b>{escape(item.title)}</b> &bull; <font color='{primary_hex}'><b>[{item.impact_area} | {item.priority} Priority]</b></font>", styles["ActionTitle"]),
            Paragraph(escape(item.description), styles["ActionDesc"]),
        ]
        t_act = Table([[action_box]], colWidths=[7.5 * inch])
        t_act.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 1, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether([t_act, Spacer(1, 4)]))

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
