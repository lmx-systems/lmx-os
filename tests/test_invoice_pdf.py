"""An invoice's PDF shows the credits it takes off, so its lines add up.

The PDF printed the net total, after service-level credits, and nothing else
about them: its lines summed to the gross, the total read less, and the credits
that made up the difference were nowhere on the page. Read from the rendered
PDF, the way a client reads it.
"""
from datetime import date
from io import BytesIO

from pypdf import PdfReader

from app.billing.invoice_pdf import render_invoice_pdf
from app.schemas.billing import InvoiceCreditLine, InvoiceDetailView, InvoiceLineItem


def _invoice(*, credited: bool) -> InvoiceDetailView:
    lines = [
        InvoiceLineItem(
            order_id="o1",
            external_order_ref="SO-1001",
            shop_name="Riverside Motors",
            sla_tier="T2",
            delivered_at="2026-10-01T15:00:00+00:00",
            fee_cents=1_200,
        ),
        InvoiceLineItem(
            order_id="o2",
            external_order_ref="SO-1002",
            shop_name="Riverside Motors",
            sla_tier="T2",
            delivered_at="2026-10-01T16:00:00+00:00",
            fee_cents=1_000,
        ),
    ]
    credits = (
        [
            InvoiceCreditLine(
                order_id="o2",
                sla_tier="T2",
                amount_cents=100,
                reason="T2 delivered 42 min late (10% credit)",
                promised_by="2026-10-01T15:18:00+00:00",
                delivered_at="2026-10-01T16:00:00+00:00",
                minutes_late=42,
            )
        ]
        if credited
        else []
    )
    credit_cents = sum(credit.amount_cents for credit in credits)
    return InvoiceDetailView(
        invoice_id="i1",
        invoice_number=7,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 11, 1),
        generated_at="2026-11-01T08:00:00+00:00",
        gross_cents=2_200,
        credit_cents=credit_cents,
        total_cents=2_200 - credit_cents,
        order_count=2,
        line_items=lines,
        credits=credits,
    )


def _text(invoice: InvoiceDetailView) -> str:
    pdf = render_invoice_pdf(invoice, client_name="Design Partner")
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)


def test_a_credited_invoice_shows_what_its_lines_come_to_and_what_came_off():
    text = _text(_invoice(credited=True))

    # $12.00 + $10.00 in the lines, $1.00 credited, $21.00 owed.
    assert "$22.00" in text
    assert "Service credits" in text
    assert "-$1.00" in text
    assert "$21.00" in text


def test_each_credit_names_its_order_and_why():
    text = _text(_invoice(credited=True))

    assert "SO-1002" in text
    assert "T2 delivered 42 min late (10% credit)" in text


def test_an_invoice_without_credits_reads_as_before():
    text = _text(_invoice(credited=False))

    assert "Service credits" not in text
    assert "$22.00" in text
