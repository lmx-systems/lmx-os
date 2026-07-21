"""
Invoice PDF rendering (roadmap item C3) - turns a Statement into a
simple, clean one-page PDF via reportlab. Deliberately minimal: LMX
header, client + period, a line-item table, total, and an explicit
unbilled-orders warning when rates were missing. No payment stub or
remittance details yet - that's the payment-collection half of C3,
gated on a processor decision.
"""
from __future__ import annotations

import calendar
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

from app.billing.statements import Statement

NAVY = colors.HexColor("#1F3A5F")
SLATE = colors.HexColor("#5B6472")
LIGHT = colors.HexColor("#EAF0F6")
AMBER = colors.HexColor("#A15C07")


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _tier_label(tier: str) -> str:
    return "Hot Shot" if tier == "HOT_SHOT" else tier


def render_invoice_pdf(statement: Statement) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=f"LMX Invoice - {statement.client_name} - {statement.year}-{statement.month:02d}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("InvoiceH1", parent=styles["Title"], textColor=NAVY, fontSize=20, spaceAfter=2)
    meta = ParagraphStyle("InvoiceMeta", parent=styles["Normal"], textColor=SLATE, fontSize=10)
    note = ParagraphStyle("InvoiceNote", parent=styles["Normal"], textColor=AMBER, fontSize=9)

    month_name = calendar.month_name[statement.month]
    elements = [
        Paragraph("LMX — Delivery Invoice", h1),
        Paragraph(
            f"Client: {statement.client_name} &nbsp;·&nbsp; "
            f"Billing period: {month_name} {statement.year}",
            meta,
        ),
        Spacer(1, 0.3 * inch),
    ]

    rows = [["Service tier", "Rate per drop", "Deliveries", "Subtotal"]]
    for line in statement.lines:
        rows.append(
            [
                _tier_label(line.sla_tier),
                _dollars(line.rate_per_drop_cents),
                str(line.order_count),
                _dollars(line.subtotal_cents),
            ]
        )
    rows.append(["", "", "Total", _dollars(statement.total_cents)])

    table = Table(rows, colWidths=[2.2 * inch, 1.6 * inch, 1.4 * inch, 1.8 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, LIGHT]),
                ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, NAVY),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 0.25 * inch))

    if statement.unbilled_order_count:
        elements.append(
            Paragraph(
                f"Note: {statement.unbilled_order_count} delivered order(s) in this period have "
                "no billing rate configured and are NOT included in the total above. "
                "LMX will follow up with a corrected invoice once rates are confirmed.",
                note,
            )
        )

    doc.build(elements)
    return buffer.getvalue()
