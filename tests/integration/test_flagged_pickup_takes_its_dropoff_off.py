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
from app.api.driver_routes import arrive_at_stop, complete_stop, flag_stop_issue, scan_parcel
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.delivery.resolution import REDELIVER, resolve_failed_order
from app.models.order import Order, OrderStatus
from app.models.parcel import Parcel
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.orders.cancellation import STOPS_REMOVED
from app.orders.requeue import return_stranded_assignments
from app.orders.status_service import advance_orders
from app.schemas.driver_app import CompleteStopBody, FlagStopBody, ScanParcelBody, StopFailureReason
from tests.integration.test_route_lock_order import _cancel, _route_with_a_second_order

pytestmark = pytest.mark.integration

POD_PHOTO = "local-capture://pod/test/photo.jpg"


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


# ---------------------------------------------------------------------------
# The #226 review: a shared pickup sheds the leaving order's parcels
# ---------------------------------------------------------------------------


async def _a_shared_pickup(db_session):
    """The second order's pickup also carries order B, with two parcels to A's
    one - a commingled pickup counted at three."""
    authed, route_id, _current, order_a, pickup_id, dropoff_id, _ = await _route_with_a_second_order(
        db_session, current_done=True, trailing=True
    )
    a = await db_session.get(Order, order_a)
    now = datetime.now(timezone.utc)
    b = Order(
        hub_id=a.hub_id, client_id=a.client_id, shop_id=a.shop_id,
        external_order_ref=f"ORD-SHARED-{uuid.uuid4().hex[:6]}", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now, weight_units=1, status=OrderStatus.en_route_pickup, requested_at=now,
        delivery_address="5 Other Rd", delivery_lat=34.053, delivery_lng=-118.253,
    )
    db_session.add(b)
    await db_session.flush()
    tag = uuid.uuid4().hex[:6]
    db_session.add_all(
        [
            StopOrder(stop_id=pickup_id, order_id=b.id),
            Parcel(hub_id=a.hub_id, order_id=a.id, barcode=f"A1-{tag}"),
            Parcel(hub_id=a.hub_id, order_id=b.id, barcode=f"B1-{tag}"),
            Parcel(hub_id=a.hub_id, order_id=b.id, barcode=f"B2-{tag}"),
        ]
    )
    (await db_session.get(Stop, pickup_id)).parcel_count = 3
    await db_session.commit()
    return authed, order_a, pickup_id, dropoff_id, tag


async def _collect(db_session, authed, pickup_id, barcodes):
    for barcode in barcodes:
        await scan_parcel(str(pickup_id), ScanParcelBody(barcode=barcode), driver=authed, session=db_session)
    return await complete_stop(
        str(pickup_id), CompleteStopBody(method="photo", photo_url=POD_PHOTO), driver=authed, session=db_session
    )


async def test_a_cancelled_orders_parcels_leave_a_shared_pickup(db_session, real_redis_client, monkeypatch):
    authed, order_a, pickup_id, _dropoff_id, tag = await _a_shared_pickup(db_session)

    assert await _cancel(order_a) == STOPS_REMOVED

    db_session.expire_all()
    pickup = await db_session.get(Stop, pickup_id)
    # Still on the route for B - now the stop being driven to - at B's count.
    assert pickup.status != "cancelled"
    assert pickup.parcel_count == 2
    monkeypatch.setattr(driver_routes, "get_client", lambda: _Published())
    await arrive_at_stop(str(pickup_id), None, driver=authed, session=db_session)
    done = await _collect(db_session, authed, pickup_id, [f"B1-{tag}", f"B2-{tag}"])
    assert done.status == "completed"


async def test_a_scan_of_the_leaving_order_comes_off_the_count(db_session, real_redis_client, monkeypatch):
    """At the counter the driver scans A's box and one of B's, then flags A's
    delivery: A's scan must not stand in for B's second box."""
    authed, _order_a, pickup_id, dropoff_id, tag = await _a_shared_pickup(db_session)
    monkeypatch.setattr(driver_routes, "get_client", lambda: _Published())
    await arrive_at_stop(str(pickup_id), None, driver=authed, session=db_session)
    for barcode in (f"A1-{tag}", f"B1-{tag}"):
        await scan_parcel(str(pickup_id), ScanParcelBody(barcode=barcode), driver=authed, session=db_session)

    await _flag(dropoff_id, authed, db_session)

    db_session.expire_all()
    pickup = await db_session.get(Stop, pickup_id)
    assert (pickup.parcel_count, pickup.scanned_count) == (2, 1)
    with pytest.raises(HTTPException) as refused:
        await complete_stop(
            str(pickup_id), CompleteStopBody(method="photo", photo_url=POD_PHOTO), driver=authed, session=db_session
        )
    assert refused.value.status_code == 409
    done = await _collect(db_session, authed, pickup_id, [f"B2-{tag}"])
    assert done.status == "completed"


async def test_a_flags_publish_failing_still_frees_the_driver(db_session, real_redis_client, monkeypatch):
    """The publish after the commit used to raise out of the flag, skipping the
    driver's release - and the app's retry took the replay path, so for good."""
    authed, route_id, _current, _order_id, pickup_id, _dropoff_id, _ = await _route_with_a_second_order(
        db_session, current_done=True
    )

    class _Down:
        async def publish(self, channel, message):
            raise ConnectionError("redis is away")

    freed: list[str] = []

    async def _free(session, driver):
        freed.append(driver.driver_id)

    monkeypatch.setattr(driver_routes, "get_client", lambda: _Down())
    monkeypatch.setattr(driver_routes, "_free_driver_after_route", _free)

    await _flag(pickup_id, authed, db_session)

    db_session.expire_all()
    assert (await db_session.get(Route, route_id)).status == "completed"
    assert freed == [authed.driver_id]
