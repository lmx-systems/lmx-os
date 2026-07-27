"""
Invoice PDF rendering (docs/ROADMAP.md C3) - turns a generated Invoice
into a clean one-page PDF via reportlab.

This is the server-side PDF that C3 originally deferred in favour of the
client portal's browser "Print / Save as PDF". It renders directly from
the same `InvoiceDetailView` the portal and admin endpoints already
produce (app/billing/service.py's invoice_detail_view) - it does NOT
introduce a second billing data model. The per-order line items are
grouped by (tier, rate) here so the invoice reads as a short tier summary
("HOT_SHOT x 12 @ $45.00") rather than one row per parcel.

Deliberately minimal, matching the rest of C3: LMX header, client +
period, the tier-summary table, and the total. No payment stub or
remittance details - that's the payment-collection half of C3, still
gated on a processor decision.
"""
from __future__ import annotations

import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as PdfImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.schemas.billing import InvoiceDetailView

# Real LMX stamp (from docs/LMX branding via the asset pipeline - see
# docs/LMX_Brand_Asset_Inventory.docx). Falls back to a text-only header
# if the file is ever missing, rather than failing invoice generation.
_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "lmx-stamp.png")

NAVY = colors.HexColor("#1F3A5F")
SLATE = colors.HexColor("#5B6472")
LIGHT = colors.HexColor("#EAF0F6")

# Tier display order, kept in sync by hand with app.models.order.SLATier -
# same decoupled-string convention as app/api/admin_routes.py's
# VALID_SLA_TIERS (ClientRate.sla_tier is a plain string on purpose).
_TIER_ORDER = ["HOT_SHOT", "T1", "T2", "T3"]


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _tier_label(tier: str | None) -> str:
    if tier is None:
        return "Unclassified"
    return "Hot Shot" if tier == "HOT_SHOT" else tier


def _summarize_lines(invoice: InvoiceDetailView) -> list[tuple[str, int, int, int]]:
    """Group the per-order line items into (tier, rate_per_drop, count,
    subtotal) rows. Grouped by rate as well as tier so a mid-period rate
    change shows as two honest lines instead of one wrong average - same
    rule the (now superseded) planning-line statements module used."""
    grouped: dict[tuple[str | None, int], int] = {}
    for item in invoice.line_items:
        key = (item.sla_tier, item.fee_cents)
        grouped[key] = grouped.get(key, 0) + 1

    rows = [
        (tier, rate, count, rate * count)
        for (tier, rate), count in grouped.items()
    ]
    rows.sort(
        key=lambda r: (
            _TIER_ORDER.index(r[0]) if r[0] in _TIER_ORDER else len(_TIER_ORDER),
            -r[1],
        )
    )
    return rows


def render_invoice_pdf(invoice: InvoiceDetailView, client_name: str) -> bytes:
    buffer = BytesIO()
    period = f"{invoice.period_start.isoformat()} to {invoice.period_end.isoformat()}"
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=f"LMX Invoice #{invoice.invoice_number} - {client_name}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("InvoiceH1", parent=styles["Title"], textColor=NAVY, fontSize=20, spaceAfter=2, alignment=0)
    meta = ParagraphStyle("InvoiceMeta", parent=styles["Normal"], textColor=SLATE, fontSize=10, leading=15)

    have_logo = os.path.exists(_LOGO_PATH)
    elements = []
    if have_logo:
        # 317x128 source -> ~99x40pt, left-aligned above the title.
        logo = PdfImage(_LOGO_PATH, width=99, height=40)
        logo.hAlign = "LEFT"
        elements.extend([logo, Spacer(1, 0.12 * inch)])
    elements += [
        Paragraph("Delivery Invoice" if have_logo else "LMX — Delivery Invoice", h1),
        Paragraph(
            f"Invoice #{invoice.invoice_number} &nbsp;·&nbsp; "
            f"Client: {client_name} &nbsp;·&nbsp; Billing period: {period}",
            meta,
        ),
        Spacer(1, 0.3 * inch),
    ]

    rows = [["Service tier", "Rate per drop", "Deliveries", "Subtotal"]]
    for tier, rate, count, subtotal in _summarize_lines(invoice):
        rows.append([_tier_label(tier), _dollars(rate), str(count), _dollars(subtotal)])
    rows.append(["", "", "Total", _dollars(invoice.total_cents)])

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

    doc.build(elements)
    return buffer.getvalue()
