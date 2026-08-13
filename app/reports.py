from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


GREEN = colors.HexColor("#174D3A")
GOLD = colors.HexColor("#C99B45")
INK = colors.HexColor("#17201C")
MUTED = colors.HexColor("#63706A")
LINE = colors.HexColor("#DFE4DF")
PAPER = colors.HexColor("#F3F0E8")


def _value(value) -> str:
    if value is None:
        return "Not available"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def build_client_pdf(site, period: str, overview: dict, acquisition: dict, annotations: list[dict], generated_at: datetime | None = None) -> bytes:
    generated_at = generated_at or datetime.now(timezone.utc)
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=GREEN, spaceAfter=5))
    styles.add(ParagraphStyle(name="ReportSubtitle", parent=styles["Normal"], fontSize=10, leading=14, textColor=MUTED, spaceAfter=18))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=GREEN, spaceBefore=14, spaceAfter=8))
    styles.add(ParagraphStyle(name="Footer", parent=styles["Normal"], fontSize=8, textColor=MUTED, alignment=TA_CENTER))
    document = SimpleDocTemplate(buffer, pagesize=LETTER, rightMargin=.65*inch, leftMargin=.65*inch, topMargin=.65*inch, bottomMargin=.65*inch, title=f"{site.company} Analytics Report", author="Measurement and Reporting Platform")

    def page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(doc.leftMargin, .45*inch, LETTER[0]-doc.rightMargin, .45*inch)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(LETTER[0]/2, .27*inch, f"Stored GA4 reporting - Page {doc.page}")
        canvas.restoreState()

    story = [
        Paragraph(escape(site.company), styles["ReportTitle"]),
        Paragraph(f"Performance report | {period.replace('_',' ')} | Generated {generated_at:%B %d, %Y at %H:%M UTC}", styles["ReportSubtitle"]),
        Paragraph("Business outcomes", styles["Section"]),
    ]
    metrics = overview.get("metrics", [])
    metric_rows = [["Metric", "Current", "Previous", "Source"]]
    for metric in metrics:
        metric_rows.append([
            metric.get("metric", "unknown").replace("_", " ").title(),
            _value(metric.get("value")),
            _value(metric.get("previousValue")),
            metric.get("source", "ga4_reporting_api"),
        ])
    if len(metric_rows) == 1: metric_rows.append(["No approved outcomes returned", "-", "-", "Stored snapshot"])
    metrics_table = Table(metric_rows, colWidths=[2.2*inch,1.15*inch,1.15*inch,2.05*inch], repeatRows=1)
    metrics_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),GREEN),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("GRID",(0,0),(-1,-1),.4,LINE),("BACKGROUND",(0,1),(-1,-1),colors.white),("TEXTCOLOR",(0,1),(-1,-1),INK),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("FONTSIZE",(0,0),(-1,-1),9),("LEADING",(0,0),(-1,-1),12),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,PAPER]),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story.extend([metrics_table, Paragraph("Acquisition drivers", styles["Section"])])
    acquisition_rows = [["Channel", "Sessions", "Active users", "Change"]]
    for row in acquisition.get("rows", [])[:12]:
        acquisition_rows.append([row.get("channel", "Unknown"),_value(row.get("sessions")),_value(row.get("activeUsers")),_value(row.get("sessionChange"))])
    if len(acquisition_rows) == 1: acquisition_rows.append(["No rows for this complete period", "0", "0", "0"])
    acquisition_table = Table(acquisition_rows, colWidths=[2.8*inch,1.2*inch,1.2*inch,1.3*inch], repeatRows=1)
    acquisition_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),GOLD),("TEXTCOLOR",(0,0),(-1,0),INK),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("GRID",(0,0),(-1,-1),.4,LINE),("FONTSIZE",(0,0),(-1,-1),9),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,PAPER]),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story.extend([acquisition_table, Paragraph("Context and annotations", styles["Section"])])
    if annotations:
        for annotation in annotations[:10]:
            heading = f"{annotation['date']} - {annotation['type'].replace('_',' ').title()}"
            story.append(KeepTogether([Paragraph(f"<b>{escape(heading)}</b>", styles["BodyText"]),Paragraph(escape(annotation["note"]), styles["BodyText"]),Spacer(1,6)]))
    else:
        story.append(Paragraph("No operator or client annotations were recorded for this report.", styles["BodyText"]))
    story.extend([Spacer(1,14),Paragraph("Definitions and limitations", styles["Section"]),Paragraph("This report is generated from the same versioned stored semantic layer used by the dashboard. It excludes the current incomplete local day. Website appointment requests are requests for office follow-up, not booked or confirmed appointments. GA4 and provider acceptance do not prove office inbox placement or patient outcomes.", styles["BodyText"])])
    document.build(story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()
