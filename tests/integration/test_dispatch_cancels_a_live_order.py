"""Dispatch cancels an order a client no longer can.

A client's cancel (#186) stops once a driver has the order and tells them to
call dispatch. Until this, dispatch could do nothing with the call: the only
ops cancel was resolving a *failed* delivery. An order a driver had been
offered, or was driving to collect, could not be taken back by anybody.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_routes import cancel_order_as_dispatch
from app.api.driver_routes import accept_offer, arrive_at_stop, get_my_route, list_my_offers
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.models.client_webhook import ClientWebhookEndpoint, WebhookDelivery, new_webhook_secret
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.stop import Stop
from app.ops_auth.dependencies import AuthedOpsUser
from app.optimizer.service import DispatchOptimizerService
from tests.integration import test_driver_app_integration as driver_app
from tests.integration.queue_helpers import let_the_hold_run_out

pytestmark = pytest.mark.integration

ADMIN = AuthedOpsUser(ops_user_id="ops-1", email="ops@lmxit.com", name="Dispatch", role="admin")


async def _offered(db_session):
    """The order out on an offer to the hub's one driver."""
    hub_id, client_id, shop_id, driver_id, order = await driver_app._seed(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await let_the_hold_run_out(hub_id)
    await DispatchOptimizerService().run_cycle(str(hub_id))
    [offer] = await list_my_offers(driver=authed, session=db_session)
    return hub_id, client_id, driver_id, order, offer, authed


async def _accepted(db_session):
    hub_id, client_id, driver_id, order, offer, authed = await _offered(db_session)
    route = await accept_offer(offer.offer_id, driver=authed, session=db_session)
    return hub_id, client_id, driver_id, order, route, authed


async def _events(db_session):
    rows = (await db_session.execute(select(WebhookDelivery).order_by(WebhookDelivery.sequence))).scalars()
    return [(row.payload["previous_status"], row.payload["status"]) for row in rows]


async def test_an_open_offer_is_withdrawn(db_session, real_redis_client):
    hub_id, client_id, driver_id, order, offer, authed = await _offered(db_session)
    db_session.add(ClientWebhookEndpoint(client_id=client_id, url="https://consumer.example.com/lmx", secret=new_webhook_secret()))
    await db_session.commit()

    result = await cancel_order_as_dispatch(str(order.id), session=db_session, _admin=ADMIN)

    assert (result.status, result.how) == ("cancelled", "offer_withdrawn")
    db_session.expire_all()
    assert (await db_session.get(RouteOffer, __import__("uuid").UUID(offer.offer_id))).status == "withdrawn"
    assert await list_my_offers(driver=authed, session=db_session) == []
    # The driver is offerable again, and the client heard.
    state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
    assert state.status == "available"
    assert await _events(db_session) == [("ASSIGNED", "CANCELLED")]


async def _second_order(db_session, hub_id, client_id, shop_id) -> Order:
    """Another order from the same shop, held like the first, so the cycle
    offers both to the one driver."""
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref="ORD-DRIVER-APP-2", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now + timedelta(minutes=30), weight_units=1,
        status=OrderStatus.held, requested_at=now,
        delivery_address="16 Oak Ave", delivery_lat=34.0535, delivery_lng=-118.2535,
    )
    db_session.add(order)
    await db_session.commit()
    await HoldQueueStore().add(
        str(hub_id),
        HeldOrder(
            order_id=str(order.id), shop_lat=34.051, shop_lng=-118.251, sla_tier="T2",
            hold_deadline=order.hold_deadline, held_since=now, shop_name="Midtown Auto Parts",
        ),
    )
    return order


async def test_the_offers_other_orders_go_back_to_the_queue(db_session, real_redis_client):
    """An offer can bundle several orders. Cancelling one must not lose the rest."""
    hub_id, client_id, shop_id, driver_id, first = await driver_app._seed(db_session)
    second = await _second_order(db_session, hub_id, client_id, shop_id)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await let_the_hold_run_out(hub_id)
    await DispatchOptimizerService().run_cycle(str(hub_id))
    offers = await list_my_offers(driver=authed, session=db_session)
    assert len(offers) == 1 and len(offers[0].stops) >= 2
    first_id, second_id = first.id, second.id

    await cancel_order_as_dispatch(str(first_id), session=db_session, _admin=ADMIN)

    held = {h.order_id for h in await HoldQueueStore().get_all(str(hub_id))}
    assert str(second_id) in held and str(first_id) not in held
    db_session.expire_all()
    assert (await db_session.get(Order, second_id)).status == OrderStatus.held


async def test_an_accepted_routes_untouched_stops_come_off(db_session, real_redis_client):
    hub_id, client_id, driver_id, order, route, authed = await _accepted(db_session)
    order_id = order.id

    result = await cancel_order_as_dispatch(str(order_id), session=db_session, _admin=ADMIN)

    assert (result.status, result.how) == ("cancelled", "stops_removed")
    db_session.expire_all()
    stops = (await db_session.execute(select(Stop).where(Stop.route_id == route.route_id))).scalars().all()
    # Kept as a record, not deleted: messages about them refer to the rows.
    assert {s.status for s in stops} == {"cancelled"}
    assert (await db_session.get(Route, route.route_id)).plan_version == 2
    assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled
    # The driver's route no longer shows them.
    view = await get_my_route(driver=authed, session=db_session)
    assert view is None or view.stops == []


async def test_once_the_driver_is_at_the_shop_it_is_a_return_not_a_cancel(db_session, real_redis_client):
    hub_id, client_id, driver_id, order, route, authed = await _accepted(db_session)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)

    order_id = order.id
    with pytest.raises(HTTPException) as refused:
        await cancel_order_as_dispatch(str(order_id), session=db_session, _admin=ADMIN)

    assert refused.value.status_code == 409
    assert "return" in refused.value.detail
    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status != OrderStatus.cancelled


async def test_a_held_order_is_cancelled_the_clients_way(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await driver_app._seed(db_session)

    result = await cancel_order_as_dispatch(str(order.id), session=db_session, _admin=ADMIN)

    assert (result.status, result.how) == ("cancelled", "before_collection")
    assert await HoldQueueStore().get_all(str(hub_id)) == []


async def test_a_delivered_order_is_refused(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await driver_app._seed(db_session)
    order.status = OrderStatus.delivered
    order.delivered_at = datetime.now(timezone.utc)
    await db_session.commit()

    with pytest.raises(HTTPException) as refused:
        await cancel_order_as_dispatch(str(order.id), session=db_session, _admin=ADMIN)
    assert refused.value.status_code == 409


async def test_an_unknown_order_is_a_404(db_session, real_redis_client):
    with pytest.raises(HTTPException) as refused:
        await cancel_order_as_dispatch(str(__import__("uuid").uuid4()), session=db_session, _admin=ADMIN)
    assert refused.value.status_code == 404
