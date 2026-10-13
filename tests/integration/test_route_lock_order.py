"""One lock order on a live route, and the stranded sweep's two gaps (`CON-5`).

An independent review of the cycle's write-safety found four more, all older
than it:

  - a dispatch cancel held its own stops and waited for the route, while a
    driver's flag or complete held the route and wrote those stops' ETAs - a
    deadlock Postgres broke by failing one of them;
  - an arrival, and the current-stop promotion after a completion, read their
    stop unlocked and wrote over a stop a cancel had just taken off the route;
  - the stranded sweep counted a redelivery's finished first attempt as "on a
    stop", and never sent it back;
  - the sweep's "wakes nothing" scheduled a wake-up at a deadline already past.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.api.driver_routes import arrive_at_stop, flag_stop_issue
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.db import session_scope
from app.delivery.en_route import mark_current_stop_en_route
from app.delivery.routes import lock_route, try_lock_route
from app.driver_auth.dependencies import AuthedDriver
from app.events.bus import WAKEUPS_KEY, wakeup_member
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.orders.cancellation import STOPS_REMOVED, OrderNotCancellable, cancel_live_order
from app.orders.requeue import return_stranded_assignments
from app.orders.status_service import advance_orders
from app.redis_client import get_client
from app.schemas.driver_app import FlagStopBody, StopFailureReason
from tests.integration import test_driver_app_integration as driver_app
from tests.integration import test_optimizer_live_route_push as live

pytestmark = pytest.mark.integration


async def _route_with_a_second_order(db_session, *, current_done: bool = False, trailing: bool = False):
    """A driver mid-route: a collected pickup, the current drop-off (arrived, or
    already completed), then a second order's pickup and drop-off still to do -
    the order dispatch cancels. `trailing` adds a third order's pickup after it,
    so the route has something left once the second order comes off."""
    hub_id, client_id, shop_id, driver_id, route, current = await live._seed_active_route(db_session)
    if current_done:
        current.status = "completed"
        current.completed_at = datetime.now(timezone.utc)

    async def an_order(status: OrderStatus) -> Order:
        now = datetime.now(timezone.utc)
        order = Order(
            hub_id=hub_id, client_id=client_id, shop_id=shop_id,
            external_order_ref=f"ORD-LOCKS-{uuid.uuid4().hex[:6]}", source_system="flat_file", raw_payload={},
            sla_tier="T2", hold_deadline=now, weight_units=1, status=status, requested_at=now,
            delivery_address="3 Later Rd", delivery_lat=34.052, delivery_lng=-118.252,
        )
        db_session.add(order)
        await db_session.flush()
        return order

    cancelled_order = await an_order(OrderStatus.en_route_pickup)
    pickup = Stop(route_id=route.id, shop_id=shop_id, sequence=2, stop_type="pickup", status="pending", parcel_count=1)
    dropoff = Stop(route_id=route.id, shop_id=None, sequence=3, stop_type="dropoff", status="pending", parcel_count=1)
    db_session.add_all([pickup, dropoff])
    await db_session.flush()
    db_session.add_all(
        [
            StopOrder(stop_id=pickup.id, order_id=cancelled_order.id),
            StopOrder(stop_id=dropoff.id, order_id=cancelled_order.id),
        ]
    )
    later = None
    if trailing:
        third = await an_order(OrderStatus.en_route_pickup)
        later = Stop(route_id=route.id, shop_id=shop_id, sequence=4, stop_type="pickup", status="pending", parcel_count=1)
        db_session.add(later)
        await db_session.flush()
        db_session.add(StopOrder(stop_id=later.id, order_id=third.id))
    await db_session.commit()
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    return (
        authed,
        route.id,
        current.id,
        cancelled_order.id,
        pickup.id,
        dropoff.id,
        later.id if later is not None else None,
    )


def _held_open(monkeypatch, module: str):
    """Pause `module`'s `mark_current_stop_en_route` - called with the route
    held - until released, and say when it got there."""
    import importlib

    target = importlib.import_module(module)
    real = target.mark_current_stop_en_route
    holding, go = asyncio.Event(), asyncio.Event()

    async def paused(session, route_id):
        holding.set()
        await go.wait()
        return await real(session, route_id)

    monkeypatch.setattr(target, "mark_current_stop_en_route", paused)
    return holding, go


async def _cancel(order_id):
    async with session_scope() as session:
        order = await session.get(Order, order_id)
        return await cancel_live_order(session, HoldQueueStore(), order)


# ---------------------------------------------------------------------------
# (5) The route, then its stops: a cancel and a driver's flag on one route
# ---------------------------------------------------------------------------


async def test_a_cancel_and_a_flag_on_one_route_do_not_deadlock(db_session, real_redis_client, monkeypatch):
    """The review's interleaving. The driver flags the current stop: it holds the
    route and is about to write the next stops' status and ETAs. Dispatch then
    cancels the order those stops belong to. The cancel used to take its stops
    and wait for the route, the flag waited for the stops, and Postgres failed
    one of them. Now the cancel waits for the route holding nothing, and both
    land."""
    authed, route_id, current_id, order_id, pickup_id, _dropoff_id, _ = await _route_with_a_second_order(db_session)
    holding, go = _held_open(monkeypatch, "app.api.driver_routes")

    async def the_flag():
        async with session_scope() as session:
            return await flag_stop_issue(
                str(current_id), FlagStopBody(reason=StopFailureReason.SHOP_CLOSED), driver=authed, session=session
            )

    flag = asyncio.create_task(the_flag())
    await asyncio.wait_for(holding.wait(), timeout=5)
    cancel = asyncio.create_task(_cancel(order_id))
    await asyncio.sleep(0.5)
    assert not cancel.done(), "the cancel should be waiting for the route the flag holds"
    go.set()

    flagged, how = await asyncio.wait_for(asyncio.gather(flag, cancel, return_exceptions=True), timeout=15)

    assert not isinstance(flagged, BaseException), flagged
    assert how == STOPS_REMOVED, how
    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled
    assert (await db_session.get(Stop, pickup_id)).status == "cancelled"
    assert (await db_session.get(Stop, current_id)).status == "failed"
    # Nothing left to do on it: closed once, by whichever finished last.
    assert (await db_session.get(Route, route_id)).status == "completed"


# ---------------------------------------------------------------------------
# (6) No driver write lands on a cancelled stop
# ---------------------------------------------------------------------------


async def test_an_arrival_waits_for_a_cancel_and_then_refuses(db_session, real_redis_client, monkeypatch):
    """The driver taps Arrived at the second order's pickup while dispatch's
    cancel of that order is mid-way. The arrival read the stop unlocked as not
    yet reached, queued its write behind the cancel, then wrote `arrived` over
    `cancelled`. It now waits for the route, reads the stop fresh and refuses."""
    authed, _route_id, _current, order_id, pickup_id, _dropoff_id, _ = await _route_with_a_second_order(db_session)
    holding, go = _held_open(monkeypatch, "app.orders.cancellation")

    cancel = asyncio.create_task(_cancel(order_id))
    await asyncio.wait_for(holding.wait(), timeout=5)

    async def the_arrival():
        async with session_scope() as session:
            return await arrive_at_stop(str(pickup_id), None, driver=authed, session=session)

    arrival = asyncio.create_task(the_arrival())
    await asyncio.sleep(0.5)
    go.set()

    how, arrived = await asyncio.wait_for(asyncio.gather(cancel, arrival, return_exceptions=True), timeout=15)

    assert how == STOPS_REMOVED, how
    assert isinstance(arrived, HTTPException) and arrived.status_code == 409, arrived
    db_session.expire_all()
    assert (await db_session.get(Stop, pickup_id)).status == "cancelled"


async def test_the_next_stop_is_not_promoted_over_a_cancel(db_session, real_redis_client, monkeypatch):
    """`complete_stop` promotes the next stop in a second transaction of its own.
    That read the next stop unlocked as `pending` while a cancel was taking it
    off, and wrote `en_route` over `cancelled` - the cancelled order's pickup back
    on the driver's route. It now takes the route first and promotes the stop
    after the cancelled ones."""
    _authed, route_id, _current, order_id, pickup_id, _dropoff_id, later_id = await _route_with_a_second_order(
        db_session, current_done=True, trailing=True
    )
    holding, go = _held_open(monkeypatch, "app.orders.cancellation")

    cancel = asyncio.create_task(_cancel(order_id))
    await asyncio.wait_for(holding.wait(), timeout=5)

    async def the_promotion():
        async with session_scope() as session:
            await mark_current_stop_en_route(session, route_id)
            await session.commit()

    promotion = asyncio.create_task(the_promotion())
    await asyncio.sleep(0.5)
    go.set()

    how, promoted = await asyncio.wait_for(asyncio.gather(cancel, promotion, return_exceptions=True), timeout=15)

    assert how == STOPS_REMOVED, how
    assert not isinstance(promoted, BaseException), promoted
    db_session.expire_all()
    assert (await db_session.get(Stop, pickup_id)).status == "cancelled"
    assert (await db_session.get(Stop, later_id)).status == "en_route"


# ---------------------------------------------------------------------------
# (7) and (8) The stranded sweep
# ---------------------------------------------------------------------------


def _past_the_grace() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=settings.stranded_assignment_grace_seconds + 5)


async def _a_stranded_redelivery(db_session):
    """An order whose first attempt failed at the door and was sent out again,
    then moved to `assigned` by a cycle whose offer never got made: its first
    attempt's stops are history, and nothing has it."""
    hub_id, _client_id, shop_id, driver_id, order = await driver_app._seed(db_session)
    await HoldQueueStore().remove(str(hub_id), str(order.id))
    route = Route(hub_id=hub_id, driver_id=driver_id, status="completed", plan_version=1)
    db_session.add(route)
    await db_session.flush()
    collected = Stop(route_id=route.id, shop_id=shop_id, sequence=0, stop_type="pickup", status="completed", parcel_count=1)
    refused = Stop(route_id=route.id, shop_id=None, sequence=1, stop_type="dropoff", status="failed", parcel_count=1)
    db_session.add_all([collected, refused])
    await db_session.flush()
    db_session.add_all(
        [StopOrder(stop_id=collected.id, order_id=order.id), StopOrder(stop_id=refused.id, order_id=order.id)]
    )
    order.status = OrderStatus.assigned
    await db_session.commit()
    return hub_id, driver_id, order.id


async def test_a_stranded_redelivery_goes_back(db_session, real_redis_client):
    hub_id, _driver_id, order_id = await _a_stranded_redelivery(db_session)

    returned = await return_stranded_assignments(db_session, str(hub_id), now=_past_the_grace())
    await db_session.commit()

    assert returned == 1
    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.held
    assert str(order_id) in {h.order_id for h in await HoldQueueStore().get_all(str(hub_id))}


async def test_an_order_on_a_stop_still_to_do_is_not_stranded(db_session, real_redis_client):
    """The other side of the same line: a stop not yet driven to is a driver
    having the order, and the sweep leaves it alone."""
    hub_id, driver_id, order_id = await _a_stranded_redelivery(db_session)
    route = Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1)
    db_session.add(route)
    await db_session.flush()
    again = Stop(route_id=route.id, shop_id=None, sequence=0, stop_type="dropoff", status="pending", parcel_count=1)
    db_session.add(again)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=again.id, order_id=order_id))
    await db_session.commit()

    assert await return_stranded_assignments(db_session, str(hub_id), now=_past_the_grace()) == 0


async def test_the_sweep_schedules_no_wake_up(db_session, real_redis_client):
    """The order goes back with its original hold deadline, long past; the hold
    queue's wake-up at that deadline fired within a second and started a second
    cycle beside the scheduled one. The sweep queues it without one."""
    hub_id, _driver_id, order_id = await _a_stranded_redelivery(db_session)
    order = await db_session.get(Order, order_id)
    order.hold_deadline = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db_session.commit()

    assert await return_stranded_assignments(db_session, str(hub_id), now=_past_the_grace()) == 1
    await db_session.commit()

    assert await get_client().zscore(WAKEUPS_KEY, wakeup_member(str(hub_id), str(order_id))) is None


async def test_an_order_returned_inside_its_hold_keeps_its_wake_up(db_session, real_redis_client):
    """A failed cycle may have released the order early - for a partner, or a
    driver passing - so its hold has not run out. The cycle removed its wake-up
    when it planned it; returned without one, a hold the next cycle renewed was
    not looked at again until the next sweep, five minutes on. It gets its
    wake-up back, at its deadline (`CON-5`, third review)."""
    hub_id, _driver_id, order_id = await _a_stranded_redelivery(db_session)
    deadline = datetime.now(timezone.utc) + timedelta(minutes=20)
    order = await db_session.get(Order, order_id)
    order.hold_deadline = deadline
    await db_session.commit()

    assert await return_stranded_assignments(db_session, str(hub_id), now=_past_the_grace()) == 1
    await db_session.commit()

    at = await get_client().zscore(WAKEUPS_KEY, wakeup_member(str(hub_id), str(order_id)))
    assert at == pytest.approx(deadline.timestamp(), abs=1)


# ---------------------------------------------------------------------------
# A dispatch cancel that loses the order to a driver says so
# ---------------------------------------------------------------------------


async def test_a_dispatch_cancel_racing_an_assignment_says_try_again(db_session, real_redis_client):
    """Dispatch's cancel read the order as waiting while a cycle was moving it to
    a driver. Its write correctly matched nothing, and it told the dispatcher to
    "call dispatch"."""
    _hub_id, _client_id, _shop_id, _driver_id, order = await driver_app._seed(db_session)
    order_id = order.id

    async with session_scope() as cycle:
        assert await advance_orders(cycle, [order_id], OrderStatus.assigned, only_from=(OrderStatus.held,))
        await cycle.flush()
        cancel = asyncio.create_task(_cancel(order_id))
        await asyncio.sleep(0.3)
        assert not cancel.done()
        await cycle.commit()

    with pytest.raises(OrderNotCancellable, match="A driver was given this order"):
        await asyncio.wait_for(cancel, timeout=10)


async def test_a_cancel_already_holding_its_order_does_not_wait_for_the_route(db_session, real_redis_client):
    """The one path that holds an order before a route - a cancel that found no
    offer and no stop, locked the order and looked again - takes the route
    without waiting, since a driver's complete holds the route and then waits
    for its stop's orders. A busy route is a "try again", not a lock wait."""
    _authed, route_id, *_ = await _route_with_a_second_order(db_session)

    async with session_scope() as driver, session_scope() as cancel:
        await lock_route(driver, route_id)
        assert await try_lock_route(cancel, route_id) is False
        await driver.rollback()
        assert await try_lock_route(cancel, route_id) is True


# ---------------------------------------------------------------------------
# (9) The insertion reads its route fresh under the lock (third review)
# ---------------------------------------------------------------------------


async def _an_insertion_waiting_on_the_route(db_session):
    """A passing driver's route and an order to put on it, with the route held
    by somebody else - a driver tapping through it - so the insertion has chosen
    the route from its unlocked read and is waiting for the lock."""
    hub_id, client_id, _shop_id, _driver_id, route, current = await live._seed_active_route(db_session)
    order, _new_shop_id = await live._seed_new_order(db_session, hub_id, client_id)
    route_id, current_id, order_id = route.id, current.id, order.id
    holder_cm = session_scope()
    holder = await holder_cm.__aenter__()
    await lock_route(holder, route_id)
    insertion = asyncio.create_task(live._insert(hub_id, order))
    await asyncio.sleep(0.5)
    assert not insertion.done(), "the insertion should be waiting for the route"
    return holder_cm, holder, insertion, route_id, current_id, order_id


async def test_no_stops_go_onto_a_route_finished_while_the_insertion_waited(db_session, real_redis_client):
    """The review's interleaving: the driver completes the route's last stop
    while the insertion waits for the route. The insertion held a copy read
    before the wait, appended a pickup and a drop-off to the completed route -
    which the app no longer shows - and left the order `assigned` on them for
    good. It now reads the route under the lock, sees it finished, and leaves
    the order waiting for the next cycle."""
    holder_cm, holder, insertion, route_id, current_id, order_id = await _an_insertion_waiting_on_the_route(
        db_session
    )
    try:
        (await holder.get(Stop, current_id)).status = "completed"
        (await holder.get(Route, route_id)).status = "completed"
        await holder.commit()
    finally:
        await holder_cm.__aexit__(None, None, None)

    inserted = await asyncio.wait_for(insertion, timeout=10)

    assert inserted == set()
    db_session.expire_all()
    stops = list(await db_session.scalars(select(Stop).where(Stop.route_id == route_id)))
    assert len(stops) == 2, "nothing appended to the finished route"
    assert (await db_session.get(Order, order_id)).status == OrderStatus.queued


async def test_the_insertion_builds_on_a_plan_version_bumped_while_it_waited(db_session, real_redis_client):
    """A dispatch cancel bumped the route's `plan_version` while the insertion
    waited; the insertion then wrote its stale copy plus one - the same number
    again - and the app's missed-event backstop, which compares that number,
    saw nothing new. It now increments the value it read under the lock."""
    holder_cm, holder, insertion, route_id, _current_id, order_id = await _an_insertion_waiting_on_the_route(
        db_session
    )
    try:
        await holder.execute(
            update(Route).where(Route.id == route_id).values(plan_version=Route.plan_version + 1)
        )
        await holder.commit()
    finally:
        await holder_cm.__aexit__(None, None, None)

    inserted = await asyncio.wait_for(insertion, timeout=10)

    assert inserted == {str(order_id)}
    db_session.expire_all()
    # Seeded at 1, the cancel's bump to 2, the insertion's to 3.
    assert (await db_session.get(Route, route_id)).plan_version == 3


# ---------------------------------------------------------------------------
# (10) Dispatch cancels a redelivered order (third review)
# ---------------------------------------------------------------------------


async def _a_redelivery_on_a_route(db_session, *, new_pickup_status: str = "pending"):
    """An order whose first attempt failed at the door - its pickup completed, its
    drop-off failed, both kept as history - placed again on the same driver's
    route, ahead of the failed attempt's stops by nothing but sequence."""
    hub_id, client_id, shop_id, _driver_id, route, _current = await live._seed_active_route(db_session)
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref=f"ORD-REDELIVER-{uuid.uuid4().hex[:6]}", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now, weight_units=1, status=OrderStatus.en_route_pickup, requested_at=now,
        delivery_address="3 Later Rd", delivery_lat=34.052, delivery_lng=-118.252,
    )
    db_session.add(order)
    await db_session.flush()
    old_pickup = Stop(route_id=route.id, shop_id=shop_id, sequence=2, stop_type="pickup", status="completed", parcel_count=1)
    old_drop = Stop(route_id=route.id, shop_id=None, sequence=3, stop_type="dropoff", status="failed", parcel_count=1)
    new_pickup = Stop(route_id=route.id, shop_id=shop_id, sequence=4, stop_type="pickup", status=new_pickup_status, parcel_count=1)
    new_drop = Stop(route_id=route.id, shop_id=None, sequence=5, stop_type="dropoff", status="pending", parcel_count=1)
    db_session.add_all([old_pickup, old_drop, new_pickup, new_drop])
    await db_session.flush()
    db_session.add_all([StopOrder(stop_id=s.id, order_id=order.id) for s in (old_pickup, old_drop, new_pickup, new_drop)])
    await db_session.commit()
    return order.id, old_pickup.id, old_drop.id, new_pickup.id, new_drop.id


async def test_dispatch_cancels_a_redelivered_order(db_session, real_redis_client):
    """The failed attempt's collected pickup came first by sequence, and the
    cancel refused: "the driver already has the parts". The pickup being driven
    to now is still pending; the cancel takes off the new attempt's stops and
    leaves the old attempt's history as it was."""
    order_id, old_pickup, old_drop, new_pickup, new_drop = await _a_redelivery_on_a_route(db_session)

    assert await _cancel(order_id) == STOPS_REMOVED

    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled
    assert (await db_session.get(Stop, new_pickup)).status == "cancelled"
    assert (await db_session.get(Stop, new_drop)).status == "cancelled"
    assert (await db_session.get(Stop, old_pickup)).status == "completed"
    assert (await db_session.get(Stop, old_drop)).status == "failed"


async def test_a_redelivery_whose_new_pickup_is_reached_still_refuses(db_session, real_redis_client):
    order_id, *_ = await _a_redelivery_on_a_route(db_session, new_pickup_status="arrived")

    with pytest.raises(OrderNotCancellable, match="already has the parts"):
        await _cancel(order_id)


async def test_a_pickup_collected_while_the_cancel_waited_still_refuses(db_session, real_redis_client):
    """Only the drop-off left, its pickup done: the parts are on the van. The
    cancel no longer counts a finished pickup, so this is what says so."""
    _authed, _route_id, _current, order_id, pickup_id, _dropoff_id, _ = await _route_with_a_second_order(db_session)
    await db_session.execute(update(Stop).where(Stop.id == pickup_id).values(status="completed"))
    await db_session.commit()

    with pytest.raises(OrderNotCancellable, match="already has the parts"):
        await _cancel(order_id)
