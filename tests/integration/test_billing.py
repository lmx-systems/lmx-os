"""
Statement generation (docs/ROADMAP.md C3, app/billing/service.py) against
real Postgres. Calls the route/service functions directly, same pattern as
tests/integration/test_client_portal_integration.py.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_routes import generate_client_invoice
from app.api.client_routes import get_my_invoice, list_my_invoices
from app.billing.service import NoBillableOrdersError, generate_invoice, invoice_detail_view
from app.client_auth.dependencies import AuthedClient
from app.models.client import Client
from app.models.hub import Hub
from app.models.invoice import Invoice
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.billing import InvoiceGenerateBody

pytestmark = pytest.mark.integration


async def _seed_client_with_shop(db_session, name: str = "Billing Test Client") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    hub = Hub(id=uuid.uuid4(), name="Billing Test Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()

    client = Client(hub_id=hub.id, name=name, pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()

    shop = Shop(client_id=client.id, name="Test Shop", address="1 Main St", lat=34.06, lng=-118.24)
    db_session.add(shop)
    await db_session.commit()
    return client.id, shop.id, hub.id


def _delivered_order(client_id, shop_id, hub_id, *, fee_cents, delivered_on: date, ref: str) -> Order:
    delivered_at = datetime.combine(delivered_on, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
    return Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref=ref, source_system="flat_file", raw_payload={},
        sla_tier="T2", status=OrderStatus.delivered, requested_at=delivered_at,
        fee_cents=fee_cents, updated_at=delivered_at,
    )


async def test_generate_invoice_sums_delivered_priced_orders_in_period(db_session):
    client_id, shop_id, hub_id = await _seed_client_with_shop(db_session)
    db_session.add_all([
        _delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 6, 5), ref="A"),
        _delivered_order(client_id, shop_id, hub_id, fee_cents=4_500, delivered_on=date(2026, 6, 10), ref="B"),
    ])
    await db_session.commit()

    invoice = await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))

    assert invoice.total_cents == 6_300
    assert invoice.invoice_number >= 1001  # sequence starts at a real-looking value, not 1
    assert invoice.period_start == date(2026, 6, 1)
    assert invoice.period_end == date(2026, 7, 1)


async def test_generate_invoice_excludes_orders_outside_the_period(db_session):
    client_id, shop_id, hub_id = await _seed_client_with_shop(db_session)
    db_session.add_all([
        _delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 5, 31), ref="BEFORE"),
        _delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 6, 15), ref="INSIDE"),
        _delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 7, 1), ref="AFTER"),
    ])
    await db_session.commit()

    invoice = await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))

    assert invoice.total_cents == 1_800  # only INSIDE - period_end is exclusive


async def test_generate_invoice_excludes_unpriced_orders(db_session):
    client_id, shop_id, hub_id = await _seed_client_with_shop(db_session)
    priced = _delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 6, 5), ref="PRICED")
    unpriced = _delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 6, 6), ref="UNPRICED")
    unpriced.fee_cents = None
    db_session.add_all([priced, unpriced])
    await db_session.commit()

    invoice = await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))

    assert invoice.total_cents == 1_800
    await db_session.refresh(unpriced)
    assert unpriced.invoice_id is None  # never billed, not billed as $0


async def test_generate_invoice_never_double_bills_an_order(db_session):
    client_id, shop_id, hub_id = await _seed_client_with_shop(db_session)
    order = _delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 6, 5), ref="ONCE")
    db_session.add(order)
    await db_session.commit()

    first = await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))
    assert first.total_cents == 1_800

    # A second, later-period call must not pick this order up again even
    # though it technically still falls inside a wide-enough window.
    with pytest.raises(NoBillableOrdersError):
        await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))


async def test_generate_invoice_raises_when_nothing_to_bill(db_session):
    client_id, _shop_id, _hub_id = await _seed_client_with_shop(db_session)
    with pytest.raises(NoBillableOrdersError):
        await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))


async def test_admin_generate_invoice_endpoint_returns_line_items(db_session):
    client_id, shop_id, hub_id = await _seed_client_with_shop(db_session)
    db_session.add(_delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 6, 5), ref="LINE-1"))
    await db_session.commit()

    result = await generate_client_invoice(
        str(client_id), InvoiceGenerateBody(period_start=date(2026, 6, 1), period_end=date(2026, 7, 1)),
        session=db_session,
    )

    assert result.total_cents == 1_800
    assert result.order_count == 1
    assert len(result.line_items) == 1
    assert result.line_items[0].external_order_ref == "LINE-1"
    assert result.line_items[0].shop_name == "Test Shop"


async def test_admin_generate_invoice_404s_for_unknown_client(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await generate_client_invoice(
            str(uuid.uuid4()),
            InvoiceGenerateBody(period_start=date(2026, 6, 1), period_end=date(2026, 7, 1)),
            session=db_session,
        )
    assert exc_info.value.status_code == 404


async def test_admin_generate_invoice_404s_when_nothing_to_bill(db_session):
    client_id, _shop_id, _hub_id = await _seed_client_with_shop(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await generate_client_invoice(
            str(client_id), InvoiceGenerateBody(period_start=date(2026, 6, 1), period_end=date(2026, 7, 1)),
            session=db_session,
        )
    assert exc_info.value.status_code == 404


async def test_client_can_list_and_view_only_their_own_invoices(db_session):
    client_a_id, shop_a_id, hub_a_id = await _seed_client_with_shop(db_session, name="Client A")
    client_b_id, shop_b_id, hub_b_id = await _seed_client_with_shop(db_session, name="Client B")
    db_session.add_all([
        _delivered_order(client_a_id, shop_a_id, hub_a_id, fee_cents=1_800, delivered_on=date(2026, 6, 5), ref="A-1"),
        _delivered_order(client_b_id, shop_b_id, hub_b_id, fee_cents=2_200, delivered_on=date(2026, 6, 5), ref="B-1"),
    ])
    await db_session.commit()

    invoice_a = await generate_invoice(db_session, client_a_id, date(2026, 6, 1), date(2026, 7, 1))
    await generate_invoice(db_session, client_b_id, date(2026, 6, 1), date(2026, 7, 1))

    authed_a = AuthedClient(client_id=str(client_a_id))
    invoices = await list_my_invoices(client=authed_a, session=db_session)
    assert len(invoices) == 1
    assert invoices[0].invoice_id == str(invoice_a.id)
    assert invoices[0].total_cents == 1_800

    detail = await get_my_invoice(str(invoice_a.id), client=authed_a, session=db_session)
    assert detail.line_items[0].external_order_ref == "A-1"


async def test_client_cannot_view_another_clients_invoice(db_session):
    client_a_id, shop_a_id, hub_a_id = await _seed_client_with_shop(db_session, name="Client A")
    client_b_id, shop_b_id, hub_b_id = await _seed_client_with_shop(db_session, name="Client B")
    db_session.add(_delivered_order(client_b_id, shop_b_id, hub_b_id, fee_cents=1_800, delivered_on=date(2026, 6, 5), ref="B-ONLY"))
    await db_session.commit()

    invoice_b = await generate_invoice(db_session, client_b_id, date(2026, 6, 1), date(2026, 7, 1))

    authed_a = AuthedClient(client_id=str(client_a_id))
    with pytest.raises(HTTPException) as exc_info:
        await get_my_invoice(str(invoice_b.id), client=authed_a, session=db_session)
    assert exc_info.value.status_code == 404


async def test_generate_invoice_preserves_each_orders_true_delivered_at(db_session):
    """Regression test: setting order.invoice_id used to silently bump
    Order.updated_at to "whenever this invoice was generated" via that
    column's onupdate=func.now() default, destroying the delivered-at
    proxy every other view relies on (app/api/client_routes.py's
    _order_summary_view, this module's own invoice_detail_view)."""
    client_id, shop_id, hub_id = await _seed_client_with_shop(db_session)
    delivered_on = date(2026, 6, 5)
    order = _delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=delivered_on, ref="DATED")
    db_session.add(order)
    await db_session.commit()
    original_updated_at = order.updated_at

    invoice = await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))

    await db_session.refresh(order)
    assert order.updated_at == original_updated_at

    detail = await invoice_detail_view(db_session, invoice)
    assert detail.line_items[0].delivered_at == original_updated_at.isoformat()


async def test_invoice_numbers_are_sequential_and_unique(db_session):
    client_id, shop_id, hub_id = await _seed_client_with_shop(db_session)
    db_session.add(_delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 6, 5), ref="P1"))
    db_session.add(_delivered_order(client_id, shop_id, hub_id, fee_cents=1_800, delivered_on=date(2026, 7, 5), ref="P2"))
    await db_session.commit()

    first = await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))
    second = await generate_invoice(db_session, client_id, date(2026, 7, 1), date(2026, 8, 1))

    assert second.invoice_number != first.invoice_number
