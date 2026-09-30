"""What an invoice's line items rely on `generate_invoice` to have guaranteed.

Only priced orders are ever given an invoice_id, and nothing reprices an order
after intake, so an invoiced order always has a fee. If that ever breaks, the
view should fail as exactly that - not with a validation error, and never by
showing a $0 line, which would under-bill without leaving a trace.
"""
import uuid

import pytest

from app.billing.service import _invoiced_fee
from app.models.order import Order


def test_an_invoiced_order_reports_its_fee():
    assert _invoiced_fee(Order(fee_cents=1_800)) == 1_800


def test_a_zero_fee_is_a_fee_not_a_missing_one():
    assert _invoiced_fee(Order(fee_cents=0)) == 0


def test_an_unpriced_order_on_an_invoice_fails_as_one():
    order = Order(id=uuid.uuid4(), invoice_id=uuid.uuid4(), fee_cents=None)
    with pytest.raises(RuntimeError, match="without a fee"):
        _invoiced_fee(order)
