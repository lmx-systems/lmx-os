"""
Live route-change push: DispatchOptimizerService._insert_unassigned_into_active_routes
is the first capability anywhere that mutates a Route already active for a
driver mid-shift. Tested directly against the method rather than through
the full run_cycle/hold-queue pipeline - run_hold_cycle's rule 3 ("no
available driver at all -> keep holding") means an order is never even
released from hold unless an *available* (idle) driver exists, and
DriverState.load_units is never actually incremented anywhere in this
codebase (see MAX_STOPS_PER_ACTIVE_ROUTE's comment in
app/optimizer/service.py), so an idle driver always looks like they have
full capacity and the stub nearest-neighbor engine has no real reason to
leave a stop unassigned when one exists. Testing the method directly
avoids fighting that to construct an artificial "released but unassigned"
state.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop
from app.optimizer.service import DispatchOptimizerService
from app.schemas.optimizer import StopCandidate

pytestmark = pytest.mark.integration


async def _seed_active_route(db_session):
    """One driver already mid-shift: an active Route with one pickup stop
    already completed and a dropoff stop still in progress - the "current
    stop" that insertion must never touch."""
    hub_id, client_id, shop_id, driver_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Live Push Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Existing Client", pos_system="flat_file"))
    await db_session.commit()
    db_session.add_all(
        [
            Shop(
                id=shop_id, client_id=client_id, name="Existing Shop", address="1 Existing Way",
                lat=34.05, lng=-118.25, external_ref="SHOP-EXISTING",
            ),
            Driver(id=driver_id, hub_id=hub_id, name="Already Driving D.", phone="+15555550400", vehicle_capacity_units=5),
        ]
    )
    await db_session.commit()

    route = Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1)
    db_session.add(route)
    await db_session.flush()
    current_dropoff = Stop(route_id=route.id, shop_id=None, sequence=1, stop_type="dropoff", status="arrived", parcel_count=1)
    db_session.add_all(
        [
            Stop(route_id=route.id, shop_id=shop_id, sequence=0, stop_type="pickup", status="completed", parcel_count=1),
            current_dropoff,
        ]
    )
    await db_session.commit()
    return hub_id, client_id, shop_id, driver_id, route, current_dropoff


async def _seed_new_order(db_session, hub_id, client_id):
    now = datetime.now(timezone.utc)
    new_shop_id = uuid.uuid4()
    db_session.add(
        Shop(
            id=new_shop_id, client_id=client_id, name="New Shop", address="2 New Way",
            lat=34.06, lng=-118.26, external_ref="SHOP-NEW",
        )
    )
    await db_session.commit()

    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=new_shop_id,
        external_order_ref="ORD-LIVE-PUSH-1", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now + timedelta(minutes=30), weight_units=1,
        status=OrderStatus.queued, requested_at=now,
        delivery_address="9 New Delivery Rd", delivery_lat=34.061, delivery_lng=-118.261,
    )
    db_session.add(order)
    await db_session.commit()
    return order, new_shop_id


async def test_insert_appends_after_existing_stops_without_touching_them(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, route, current_dropoff = await _seed_active_route(db_session)
    order, new_shop_id = await _seed_new_order(db_session, hub_id, client_id)

    candidate = StopCandidate(
        stop_id=str(order.id), order_ids=[str(order.id)], lat=34.061, lng=-118.261, weight_units=1.0, sla_tier="T2",
    )
    inserted = await DispatchOptimizerService()._insert_unassigned_into_active_routes(
        str(hub_id), [str(order.id)], {str(order.id): candidate}
    )
    assert inserted == {str(order.id)}

    stops_result = await db_session.execute(select(Stop).where(Stop.route_id == route.id).order_by(Stop.sequence))
    stops = stops_result.scalars().all()
    assert len(stops) == 4  # original pickup + dropoff, plus the new pickup + dropoff appended
    # The two original stops are untouched - still sequence 0/1, same status.
    assert stops[0].sequence == 0 and stops[0].status == "completed"
    assert stops[1].sequence == 1 and stops[1].status == "arrived"
    assert stops[1].id == current_dropoff.id
    # The new stops land strictly after, never renumbering what's already there.
    assert stops[2].sequence == 2 and stops[2].stop_type == "pickup" and stops[2].shop_id == new_shop_id
    assert stops[3].sequence == 3 and stops[3].stop_type == "dropoff"

    # Capture plain UUIDs before expire_all() - accessing an attribute on a
    # now-expired ORM instance would trigger a synchronous lazy-load
    # outside any async-aware call, raising MissingGreenlet.
    route_id, order_id = route.id, order.id
    db_session.expire_all()
    refreshed_route = await db_session.get(Route, route_id)
    assert refreshed_route.plan_version == 2  # bumped from the seeded 1

    refreshed_order = await db_session.get(Order, order_id)
    assert refreshed_order.status == OrderStatus.assigned


async def test_insert_skips_routes_at_the_stop_count_cap(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, route, _current_dropoff = await _seed_active_route(db_session)
    order, _new_shop_id = await _seed_new_order(db_session, hub_id, client_id)

    # Pad the route up to the cap with harmless extra completed stops.
    from app.optimizer.service import MAX_STOPS_PER_ACTIVE_ROUTE

    existing_count = 2
    for seq in range(existing_count, MAX_STOPS_PER_ACTIVE_ROUTE):
        db_session.add(Stop(route_id=route.id, shop_id=None, sequence=seq, stop_type="dropoff", status="completed", parcel_count=1))
    await db_session.commit()

    candidate = StopCandidate(stop_id=str(order.id), order_ids=[str(order.id)], lat=34.061, lng=-118.261, weight_units=1.0, sla_tier="T2")
    inserted = await DispatchOptimizerService()._insert_unassigned_into_active_routes(
        str(hub_id), [str(order.id)], {str(order.id): candidate}
    )
    assert inserted == set()  # no room on this route, and no other active route to try


async def test_insert_returns_empty_when_no_active_routes_exist(db_session, real_redis_client):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="No Routes Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="C", pos_system="flat_file"))
    await db_session.commit()
    order, _shop_id = await _seed_new_order(db_session, hub_id, client_id)

    candidate = StopCandidate(stop_id=str(order.id), order_ids=[str(order.id)], lat=34.061, lng=-118.261, weight_units=1.0, sla_tier="T2")
    inserted = await DispatchOptimizerService()._insert_unassigned_into_active_routes(
        str(hub_id), [str(order.id)], {str(order.id): candidate}
    )
    assert inserted == set()
