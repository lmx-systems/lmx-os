"""
Billing statements (roadmap item C3) against real Postgres: statement
assembly counts only DELIVERED orders in the requested month for the
requested client, and both the client-portal and admin endpoints return
identical numbers.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.admin_routes import get_client_statement
from app.api.client_routes import get_my_invoice_pdf, get_my_statement
from app.client_auth.dependencies import AuthedClient
from app.models.client import Client
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop

pytestmark = pytest.mark.integration


async def _seed_client_with_orders(db_session):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Billing Hub", lat=34.0, lng=-118.0))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()
    db_session.add(Shop(id=shop_id, client_id=client_id, name="Billing Shop", address="1 Bill St",
                        lat=34.1, lng=-118.1, external_ref="BILL-1"))
    await db_session.commit()

    in_month = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)

    def order(status: OrderStatus, fee: int | None, tier: str = "T2", when: datetime = in_month):
        o = Order(
            hub_id=hub_id, client_id=client_id, shop_id=shop_id,
            external_order_ref=f"B-{uuid.uuid4().hex[:8]}", source_system="flat_file",
            raw_payload={}, sla_tier=tier, status=status, requested_at=when, fee_cents=fee,
        )
        o.created_at = when
        o.updated_at = when
        return o

    db_session.add_all([
        order(OrderStatus.delivered, 1_800),                       # bills
        order(OrderStatus.delivered, 1_800),                       # bills
        order(OrderStatus.delivered, 4_500, tier="HOT_SHOT"),      # bills
        order(OrderStatus.delivered, None, tier="T3"),             # unbilled - no rate
        order(OrderStatus.held, 1_800),                            # not delivered - excluded
        order(OrderStatus.delivered, 1_800, when=in_month + timedelta(days=40)),  # July - excluded
    ])
    await db_session.commit()
    return client_id


async def test_statement_counts_only_delivered_orders_in_month(db_session):
    client_id = await _seed_client_with_orders(db_session)

    view = await get_my_statement(
        2026, 6, client=AuthedClient(client_id=str(client_id)), session=db_session
    )
    assert view.delivered_order_count == 4  # 3 billable + 1 unbilled
    assert view.unbilled_order_count == 1
    assert view.total_cents == 2 * 1_800 + 4_500
    assert [line.sla_tier for line in view.lines] == ["HOT_SHOT", "T2"]


async def test_admin_and_client_views_agree(db_session):
    client_id = await _seed_client_with_orders(db_session)

    client_view = await get_my_statement(
        2026, 6, client=AuthedClient(client_id=str(client_id)), session=db_session
    )
    admin_view = await get_client_statement(str(client_id), 2026, 6, session=db_session)
    assert client_view == admin_view


async def test_invoice_pdf_endpoint_returns_pdf(db_session):
    client_id = await _seed_client_with_orders(db_session)

    response = await get_my_invoice_pdf(
        2026, 6, client=AuthedClient(client_id=str(client_id)), session=db_session
    )
    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")
    assert "lmx-invoice-2026-06.pdf" in response.headers["Content-Disposition"]


async def test_unknown_client_is_404_and_bad_period_is_422(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await get_client_statement(str(uuid.uuid4()), 2026, 6, session=db_session)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        await get_my_statement(
            2026, 13, client=AuthedClient(client_id=str(uuid.uuid4())), session=db_session
        )
    assert exc_info.value.status_code == 422
