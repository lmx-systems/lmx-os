"""
Live route-change push: DispatchOptimizerService._insert_unassigned_into_active_routes
is the first capability anywhere that mutates a Route already active for a
driver mid-shift. Tested directly against the method rather than through
the full run_cycle/hold-queue pipeline - run_hold_cycle's rule 3 ("no
available driver at all -> keep holding") means an order is never even
released from hold unless an *available* (idle) driver exists, so the
stub nearest-neighbor engine always has an idle driver to prefer over an
active-route insertion and has no real reason to leave a stop unassigned
when one exists. Testing the method directly avoids fighting that to
construct an artificial "released but unassigned" state.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.fleet_state.manager import FleetStateManager
from app.identity import resolve_location
from app.models.client import Client
from app.models.client_sla_term import ClientSlaTerm
from app.models.client_webhook import ClientWebhookEndpoint, WebhookDelivery, new_webhook_secret
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.receiver_profile import ReceiverProfile
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.optimizer.service import DispatchOptimizerService
from app.schemas.optimizer import StopCandidate
from app.schemas.fleet import DriverLocation, DriverState

pytestmark = pytest.mark.integration


# The new order's shop is at (34.06, -118.26). A route ending here is a tenth
# of a mile from it: a driver passing by. One ending at FAR_AWAY is twenty miles
# off, at the other end of the hub.
NEARBY = (34.061, -118.261)
FAR_AWAY = (34.3, -118.6)


async def _seed_active_route(db_session, *, load_units=1.0, ends_at=NEARBY, ends_eta=None, hub=None):
    """One driver already mid-shift: an active Route with one pickup stop
    already completed and a dropoff stop still in progress - the "current
    stop" that insertion must never touch. The dropoff is of an order delivered
    to `ends_at`, which is where the route ends and so where the driver will be
    when free; `ends_eta` is that stop's planned arrival. Also seeds the Redis
    DriverState that the capacity check reads - vehicle_capacity_units=5
    matches capacity_units=5 here, and load_units defaults to 1 to reflect the
    one already-completed pickup's weight already sitting in the vehicle."""
    shop_id, driver_id = uuid.uuid4(), uuid.uuid4()
    if hub is None:
        hub_id, client_id = uuid.uuid4(), uuid.uuid4()
        db_session.add(Hub(id=hub_id, name="Live Push Test Hub", lat=34.05, lng=-118.25))
        await db_session.commit()
        db_session.add(Client(id=client_id, hub_id=hub_id, name="Existing Client", pos_system="flat_file"))
        await db_session.commit()
    else:
        hub_id, client_id = hub
    db_session.add_all(
        [
            Shop(
                id=shop_id, client_id=client_id, name="Existing Shop", address="1 Existing Way",
                lat=34.05, lng=-118.25, external_ref=f"SHOP-EXISTING-{uuid.uuid4().hex[:6]}",
            ),
            Driver(
                id=driver_id, hub_id=hub_id, name="Already Driving D.",
                phone=f"+1555555{uuid.uuid4().int % 10000:04d}", vehicle_capacity_units=5,
            ),
        ]
    )
    await db_session.commit()
    now = datetime.now(timezone.utc)
    being_delivered = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref=f"ORD-EXISTING-{uuid.uuid4().hex[:6]}", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now, weight_units=1, status=OrderStatus.en_route_drop, requested_at=now,
        delivery_address="7 Existing Delivery Rd", delivery_lat=ends_at[0], delivery_lng=ends_at[1],
    )
    db_session.add(being_delivered)
    await db_session.commit()

    route = Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1)
    db_session.add(route)
    await db_session.flush()
    current_dropoff = Stop(
        route_id=route.id, shop_id=None, sequence=1, stop_type="dropoff", status="arrived",
        parcel_count=1, planned_eta=ends_eta,
    )
    db_session.add_all(
        [
            Stop(route_id=route.id, shop_id=shop_id, sequence=0, stop_type="pickup", status="completed", parcel_count=1),
            current_dropoff,
        ]
    )
    await db_session.flush()
    db_session.add(StopOrder(stop_id=current_dropoff.id, order_id=being_delivered.id))
    await db_session.commit()

    await FleetStateManager().upsert_driver_state(
        DriverState(
            driver_id=str(driver_id), hub_id=str(hub_id), status="en_route",
            capacity_units=5, load_units=load_units, current_route_id=str(route.id),
        )
    )
    return hub_id, client_id, shop_id, driver_id, route, current_dropoff


async def _seed_new_order(db_session, hub_id, client_id):
    now = datetime.now(timezone.utc)
    new_shop_id = uuid.uuid4()
    db_session.add(
        Shop(
            id=new_shop_id, client_id=client_id, name="New Shop", address="2 New Way",
            lat=34.06, lng=-118.26, external_ref=f"SHOP-NEW-{uuid.uuid4().hex[:6]}",
        )
    )
    await db_session.commit()

    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=new_shop_id,
        external_order_ref=f"ORD-LIVE-PUSH-{uuid.uuid4().hex[:6]}", source_system="flat_file", raw_payload={},
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


async def test_insert_skips_routes_without_enough_remaining_capacity(db_session, real_redis_client):
    # Driver is already loaded to capacity (5/5) - no room for another
    # order's weight, regardless of stop count.
    hub_id, client_id, shop_id, driver_id, route, _current_dropoff = await _seed_active_route(
        db_session, load_units=5.0
    )
    order, _new_shop_id = await _seed_new_order(db_session, hub_id, client_id)

    candidate = StopCandidate(stop_id=str(order.id), order_ids=[str(order.id)], lat=34.061, lng=-118.261, weight_units=1.0, sla_tier="T2")
    inserted = await DispatchOptimizerService()._insert_unassigned_into_active_routes(
        str(hub_id), [str(order.id)], {str(order.id): candidate}
    )
    assert inserted == set()  # no room on this route, and no other active route to try


async def test_insert_skips_routes_with_no_fleet_state_on_file(db_session, real_redis_client):
    # A route whose driver has no DriverState in Redis at all (never
    # upserted, or hub/driver id mismatch) is skipped defensively rather
    # than assumed to have unlimited room.
    hub_id, client_id, shop_id, driver_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="No Fleet State Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Existing Client", pos_system="flat_file"))
    await db_session.commit()
    db_session.add_all(
        [
            Shop(
                id=shop_id, client_id=client_id, name="Existing Shop", address="1 Existing Way",
                lat=34.05, lng=-118.25, external_ref="SHOP-EXISTING",
            ),
            Driver(id=driver_id, hub_id=hub_id, name="No Fleet State D.", phone="+15555550401", vehicle_capacity_units=5),
        ]
    )
    await db_session.commit()
    route = Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1)
    db_session.add(route)
    await db_session.flush()
    db_session.add(Stop(route_id=route.id, shop_id=shop_id, sequence=0, stop_type="dropoff", status="arrived", parcel_count=1))
    await db_session.commit()

    order, _new_shop_id = await _seed_new_order(db_session, hub_id, client_id)
    candidate = StopCandidate(stop_id=str(order.id), order_ids=[str(order.id)], lat=34.061, lng=-118.261, weight_units=1.0, sla_tier="T2")
    inserted = await DispatchOptimizerService()._insert_unassigned_into_active_routes(
        str(hub_id), [str(order.id)], {str(order.id): candidate}
    )
    assert inserted == set()


async def test_insert_returns_empty_when_no_active_routes_exist(db_session, real_redis_client):
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
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


def _candidate(order) -> StopCandidate:
    return StopCandidate(
        stop_id=str(order.id), order_ids=[str(order.id)], lat=34.061, lng=-118.261, weight_units=1.0, sla_tier="T2",
    )


async def _insert(hub_id, order):
    return await DispatchOptimizerService()._insert_unassigned_into_active_routes(
        str(hub_id), [str(order.id)], {str(order.id): _candidate(order)}
    )


async def test_insert_passes_over_a_driver_who_is_nowhere_near(db_session, real_redis_client):
    """§6's third question: a driver *already heading in this direction*. The
    first route with room took the order wherever its driver was going, which
    put a twenty-mile detour on the van at the far end of the hub."""
    hub_id, client_id, *_ = await _seed_active_route(db_session, ends_at=FAR_AWAY)
    order, _ = await _seed_new_order(db_session, hub_id, client_id)

    inserted = await _insert(hub_id, order)

    assert inserted == set()
    order_id = order.id
    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.queued


async def test_insert_picks_the_driver_passing_closest(db_session, real_redis_client):
    hub_id, client_id, _shop, _driver, farther, _ = await _seed_active_route(
        db_session, ends_at=(34.065, -118.265)
    )
    *_, nearer, _ = await _seed_active_route(db_session, hub=(hub_id, client_id), ends_at=NEARBY)
    order, _ = await _seed_new_order(db_session, hub_id, client_id)

    inserted = await _insert(hub_id, order)

    assert inserted == {str(order.id)}
    placed_on = {
        row.route_id
        for row in (
            await db_session.execute(
                select(Stop).join(StopOrder, StopOrder.stop_id == Stop.id).where(StopOrder.order_id == order.id)
            )
        ).scalars()
    }
    assert placed_on == {nearer.id}
    assert farther.id not in placed_on


async def test_insert_will_not_land_the_order_after_its_promise(db_session, real_redis_client):
    """"Adding it doesn't break their SLA": the new order's own promise was
    never looked at. A client promised delivery within the hour on an order
    placed two hours ago can't be served by any detour."""
    hub_id, client_id, *_ = await _seed_active_route(db_session)
    db_session.add(ClientSlaTerm(client_id=client_id, sla_tier="T2", delivery_target_minutes=60))
    await db_session.commit()
    order, _ = await _seed_new_order(db_session, hub_id, client_id)
    order.requested_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.commit()

    inserted = await _insert(hub_id, order)

    assert inserted == set()


async def test_insert_gives_the_new_stops_planned_arrivals(db_session, real_redis_client):
    """The stops it added carried no ETA, so the driver app showed none and
    ETA accuracy had nothing to measure against."""
    free_at = datetime.now(timezone.utc) + timedelta(minutes=20)
    hub_id, client_id, *_ = await _seed_active_route(db_session, ends_eta=free_at)
    order, _ = await _seed_new_order(db_session, hub_id, client_id)

    await _insert(hub_id, order)

    pickup, dropoff = (
        await db_session.execute(
            select(Stop).join(StopOrder, StopOrder.stop_id == Stop.id)
            .where(StopOrder.order_id == order.id).order_by(Stop.sequence)
        )
    ).scalars().all()
    assert pickup.planned_eta is not None and pickup.planned_eta >= free_at
    assert dropoff.planned_eta > pickup.planned_eta


async def test_insert_reads_the_drivers_live_position_when_the_route_has_no_address(
    db_session, real_redis_client
):
    hub_id, client_id, shop_id, driver_id, route, current_dropoff = await _seed_active_route(
        db_session, ends_at=FAR_AWAY
    )
    # The stop's order has no address to place the route's end by, but the
    # driver's phone says where they are: next door to the new shop.
    await db_session.execute(
        update(Order).where(Order.id == select(StopOrder.order_id).where(
            StopOrder.stop_id == current_dropoff.id
        ).scalar_subquery()).values(delivery_lat=None, delivery_lng=None)
    )
    await db_session.commit()
    await FleetStateManager().update_driver_location(
        DriverLocation(driver_id=str(driver_id), lat=NEARBY[0], lng=NEARBY[1],
                       recorded_at=datetime.now(timezone.utc).isoformat()),
        str(hub_id),
    )
    order, _ = await _seed_new_order(db_session, hub_id, client_id)

    inserted = await _insert(hub_id, order)

    assert inserted == {str(order.id)}


async def test_the_client_hears_an_inserted_order_was_assigned(db_session, real_redis_client):
    """The insertion marked the order assigned with a plain UPDATE, around the
    status sinks, so a client's webhook never heard it (#168 closed this for
    the cycle's own assignments)."""
    hub_id, client_id, *_ = await _seed_active_route(db_session)
    db_session.add(
        ClientWebhookEndpoint(client_id=client_id, url="https://consumer.example.com/lmx", secret=new_webhook_secret())
    )
    await db_session.commit()
    order, _ = await _seed_new_order(db_session, hub_id, client_id)

    await _insert(hub_id, order)

    sent = (await db_session.execute(select(WebhookDelivery).order_by(WebhookDelivery.sequence))).scalars()
    assert [(row.payload["previous_status"], row.payload["status"]) for row in sent] == [("HELD", "ASSIGNED")]


async def test_insert_writes_a_live_eta_on_the_new_stops_too(db_session, real_redis_client):
    """`planned_eta` alone left `eta` null until the driver's next tap, so the app
    showed nothing for the new stops and the portal fell back to a guess."""
    hub_id, client_id, *_ = await _seed_active_route(db_session)
    order, _ = await _seed_new_order(db_session, hub_id, client_id)

    await _insert(hub_id, order)

    pickup, dropoff = (
        await db_session.execute(
            select(Stop).join(StopOrder, StopOrder.stop_id == Stop.id)
            .where(StopOrder.order_id == order.id).order_by(Stop.sequence)
        )
    ).scalars().all()
    assert pickup.eta is not None and dropoff.eta is not None
    assert dropoff.eta > pickup.eta
    assert pickup.planned_eta is not None


async def test_insert_judges_the_route_free_at_its_live_eta_not_its_plan(db_session, real_redis_client):
    """A route running late was still judged free at the time it was planned to be,
    so the new stops were promised too early."""
    planned = datetime.now(timezone.utc) + timedelta(minutes=20)
    running_late = planned + timedelta(minutes=40)
    hub_id, client_id, shop_id, driver_id, route, current_dropoff = await _seed_active_route(
        db_session, ends_eta=planned
    )
    current_dropoff.eta = running_late
    await db_session.commit()
    order, _ = await _seed_new_order(db_session, hub_id, client_id)

    await _insert(hub_id, order)

    pickup = (
        await db_session.execute(
            select(Stop).join(StopOrder, StopOrder.stop_id == Stop.id)
            .where(StopOrder.order_id == order.id, Stop.stop_type == "pickup")
        )
    ).scalar_one()
    assert pickup.planned_eta >= running_late


async def test_insert_plans_on_the_shops_own_dwell(db_session, real_redis_client):
    """The promise check and the ETA walk use the same figure: a counter measured at
    twenty minutes puts the drop twenty minutes after the pickup, not eight."""
    hub_id, client_id, *_ = await _seed_active_route(db_session)
    order, new_shop_id = await _seed_new_order(db_session, hub_id, client_id)
    dock = await resolve_location(db_session, address="400 Dock Rd, Los Angeles, CA")
    shop = await db_session.get(Shop, new_shop_id)
    shop.location_id = dock.id
    db_session.add(ReceiverProfile(location_id=dock.id, dwell_sample_count=12, dwell_p50_seconds=1200))
    await db_session.commit()

    await _insert(hub_id, order)

    pickup, dropoff = (
        await db_session.execute(
            select(Stop).join(StopOrder, StopOrder.stop_id == Stop.id)
            .where(StopOrder.order_id == order.id).order_by(Stop.sequence)
        )
    ).scalars().all()
    # The shop and the drop are a few hundred feet apart, so the gap is almost all dwell.
    assert (dropoff.planned_eta - pickup.planned_eta) >= timedelta(minutes=20)
    assert (dropoff.planned_eta - pickup.planned_eta) < timedelta(minutes=21)
