"""
Billing statement assembly (roadmap item C3) - the pure grouping logic
and the invoice PDF rendering. DB-facing assembly is covered in
tests/integration/test_billing_integration.py.
"""
from datetime import timezone
from types import SimpleNamespace

from app.billing.invoice_pdf import render_invoice_pdf
from app.billing.statements import Statement, StatementLine, build_lines, month_bounds


def _order(tier: str, fee_cents: int | None):
    return SimpleNamespace(sla_tier=tier, fee_cents=fee_cents)


def test_groups_by_tier_and_rate_with_totals():
    orders = [
        _order("T2", 1_800), _order("T2", 1_800), _order("T2", 1_800),
        _order("HOT_SHOT", 4_500),
        _order("T1", 2_500), _order("T1", 2_500),
    ]
    lines, total, unbilled = build_lines(orders)
    assert unbilled == 0
    assert total == 3 * 1_800 + 4_500 + 2 * 2_500
    # HOT_SHOT sorts first, then T1, T2 (display order).
    assert [line.sla_tier for line in lines] == ["HOT_SHOT", "T1", "T2"]
    t2 = lines[2]
    assert (t2.order_count, t2.subtotal_cents) == (3, 5_400)


def test_null_fee_is_unbilled_never_zero():
    orders = [_order("T2", 1_800), _order("T2", None), _order("T3", None)]
    lines, total, unbilled = build_lines(orders)
    assert unbilled == 2
    assert total == 1_800  # NULL fees contribute nothing, loudly
    assert len(lines) == 1


def test_mid_month_rate_change_produces_two_honest_lines():
    orders = [_order("T2", 1_800), _order("T2", 2_000), _order("T2", 2_000)]
    lines, total, _ = build_lines(orders)
    t2_lines = [line for line in lines if line.sla_tier == "T2"]
    assert len(t2_lines) == 2
    assert total == 1_800 + 2 * 2_000
    # Higher rate listed first within the tier.
    assert t2_lines[0].rate_per_drop_cents == 2_000


def test_month_bounds_handles_december_rollover():
    start, end = month_bounds(2026, 12)
    assert start.year == 2026 and start.month == 12
    assert end.year == 2027 and end.month == 1
    assert start.tzinfo == timezone.utc


def test_invoice_pdf_renders_valid_pdf_bytes():
    statement = Statement(
        client_id="c-1", client_name="Design Partner", year=2026, month=7,
        lines=[
            StatementLine(sla_tier="HOT_SHOT", rate_per_drop_cents=4_500, order_count=2, subtotal_cents=9_000),
            StatementLine(sla_tier="T2", rate_per_drop_cents=1_800, order_count=10, subtotal_cents=18_000),
        ],
        total_cents=27_000, delivered_order_count=13, unbilled_order_count=1,
    )
    pdf = render_invoice_pdf(statement)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1_000
