"""A flagged pickup takes its order's drop-off off the route (`CON-5`).

Flagging a pickup failed that stop and moved its orders to `delivery_failed`,
and left each order's drop-off on the same route `pending` for good - a
delivery of parts nobody collected. After the order was sent out again, that
orphan:

  - hid it from the stranded sweep, which counted the drop-off as the order
    being on a route;
  - made dispatch's cancel refuse with "already has the parts";
  - and let the first driver complete or flag it, moving the redelivered order
    to delivered or failed.

Flagging now takes the flagged orders' other stops still to do off the route,
and the cancel counts parts on the van only when the open drop-off's own
pickup was collected.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import driver_routes
from app.api.driver_routes import arrive_at_stop, flag_stop_issue
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.delivery.resolution import REDELIVER, resolve_failed_order
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.orders.cancellation import STOPS_REMOVED
from app.orders.requeue import return_stranded_assignments
from app.orders.status_service import advance_orders
from app.schemas.driver_app import FlagStopBody, StopFailureReason
from tests.integration.test_route_lock_order import _cancel, _route_with_a_second_order

pytestmark = pytest.mark.integration


class _Published:
    """Stands in for the Redis client's publish, to see what the app is told."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def publish(self, channel, message):
        self.sent.append((channel, message))


async def _flag(stop_id, authed, db_session):
    return await flag_stop_issue(
        str(stop_id), FlagStopBody(reason=StopFailureReason.SHOP_CLOSED), driver=authed, session=db_session
    )


async def test_flagging_a_pickup_takes_its_drop_off_off(db_session, real_redis_client, monkeypatch):
    authed, route_id, _current, order_id, pickup_id, dropoff_id, later_id = await _route_with_a_second_order(
        db_session, trailing=True
    )
    published = _Published()
    monkeypatch.setattr(driver_routes, "get_client", lambda: published)
    before = (await db_session.get(Route, route_id)).plan_version

    await _flag(pickup_id, authed, db_session)

    db_session.expire_all()
    assert (await db_session.get(Stop, pickup_id)).status == "failed"
    dropoff = await db_session.get(Stop, dropoff_id)
    assert dropoff.status == "cancelled"
    assert dropoff.eta is None
    assert (await db_session.get(Order, order_id)).status == OrderStatus.delivery_failed
    # The rest of the route is untouched, and the plan moved on once.
    assert (await db_session.get(Stop, later_id)).status != "cancelled"
    route = await db_session.get(Route, route_id)
    assert route.status == "active"
    assert route.plan_version == before + 1
    # The app hears it the way it hears a dispatch cancel.
    [(channel, message)] = published.sent
    assert channel == f"driver_route_events:{authed.driver_id}"
    assert '"stop_removed"' in message and str(dropoff_id) in message


async def test_a_shared_drop_off_stays_for_its_other_order(db_session, real_redis_client, monkeypatch):
    """A drop-off stop carrying another order too stays on the route; only the
    flagged order's link to it goes."""
    authed, route_id, _current, order_id, pickup_id, dropoff_id, _ = await _route_with_a_second_order(db_session)
    monkeypatch.setattr(driver_routes, "get_client", lambda: _Published())
    first = await db_session.get(Order, order_id)
    now = datetime.now(timezone.utc)
    other = Order(
        hub_id=first.hub_id, client_id=first.client_id, shop_id=first.shop_id,
        external_order_ref=f"ORD-SHARED-{uuid.uuid4().hex[:6]}", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now, weight_units=1, status=OrderStatus.en_route_drop, requested_at=now,
        delivery_address="3 Later Rd", delivery_lat=34.052, delivery_lng=-118.252,
    )
    db_session.add(other)
    await db_session.flush()
    other_id = other.id
    db_session.add(StopOrder(stop_id=dropoff_id, order_id=other_id))
    await db_session.commit()

    await _flag(pickup_id, authed, db_session)

    db_session.expire_all()
    assert (await db_session.get(Stop, dropoff_id)).status == "pending"
    carried = set((await db_session.scalars(select(StopOrder.order_id).where(StopOrder.stop_id == dropoff_id))).all())
    assert carried == {other_id}


async def test_the_first_driver_cannot_deliver_what_they_never_collected(db_session, real_redis_client, monkeypatch):
    authed, _route_id, _current, _order_id, pickup_id, dropoff_id, _ = await _route_with_a_second_order(
        db_session, trailing=True
    )
    monkeypatch.setattr(driver_routes, "get_client", lambda: _Published())
    await _flag(pickup_id, authed, db_session)

    with pytest.raises(HTTPException) as refused:
        await arrive_at_stop(str(dropoff_id), None, driver=authed, session=db_session)
    assert refused.value.status_code == 409


async def _flagged_redelivered_and_stranded(db_session, monkeypatch):
    """The review's scenario: the pickup is flagged, ops sends the order out
    again, and a cycle moves it to `assigned` but its offers never commit."""
    authed, _route_id, _current, order_id, pickup_id, dropoff_id, _ = await _route_with_a_second_order(
        db_session, trailing=True
    )
    monkeypatch.setattr(driver_routes, "get_client", lambda: _Published())
    await _flag(pickup_id, authed, db_session)
    db_session.expire_all()
    order = await db_session.get(Order, order_id)
    await resolve_failed_order(db_session, HoldQueueStore(), order, REDELIVER)
    await advance_orders(db_session, [order_id], OrderStatus.assigned)
    await db_session.commit()
    await HoldQueueStore().remove(str(order.hub_id), str(order_id))
    return order.hub_id, order_id, dropoff_id


async def test_the_sweep_returns_a_redelivery_whose_pickup_was_flagged(db_session, real_redis_client, monkeypatch):
    hub_id, order_id, _dropoff_id = await _flagged_redelivered_and_stranded(db_session, monkeypatch)
    later = datetime.now(timezone.utc) + timedelta(seconds=settings.stranded_assignment_grace_seconds + 5)

    assert await return_stranded_assignments(db_session, str(hub_id), now=later) == 1
    await db_session.commit()
    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.held


async def test_dispatch_cancels_a_redelivery_whose_pickup_was_flagged(db_session, real_redis_client, monkeypatch):
    _hub_id, order_id, dropoff_id = await _flagged_redelivered_and_stranded(db_session, monkeypatch)

    assert await _cancel(order_id) == STOPS_REMOVED

    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled
    assert (await db_session.get(Stop, dropoff_id)).status == "cancelled"


async def test_an_orphan_left_by_older_code_does_not_hold_the_cancel(db_session, real_redis_client):
    """The defensive half: a drop-off still pending after its pickup failed -
    as flagging left it before this - is no sign of parts on the van."""
    _authed, _route_id, _current, order_id, pickup_id, dropoff_id, _ = await _route_with_a_second_order(
        db_session, trailing=True
    )
    pickup = await db_session.get(Stop, pickup_id)
    pickup.status = "failed"
    await db_session.commit()

    assert await _cancel(order_id) == STOPS_REMOVED

    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled
    assert (await db_session.get(Stop, dropoff_id)).status == "cancelled"
    assert (await db_session.get(Stop, pickup_id)).status == "failed"
