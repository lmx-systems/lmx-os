"""
Per-stop arrival times (app/delivery/eta.py), against real Postgres + Redis.

`Stop.eta` was declared, served to the driver app in every route payload, read in two
places in `driver_routes.py` - and written nowhere. Every stop's ETA was null, forever.
The driver app has a field for it; `app/models/stop.py` describes `arrived_at` vs `eta`
as I1's direct ETA-accuracy ground truth, with one side of the comparison structurally
absent.

The tests that carry the design:

  - **`test_etas_are_monotonic_along_the_route`.** The tempting implementation copies the
    optimizer's per-visit `startTime` onto each stop. It cannot be used: `accept_offer`
    re-sequences afterwards, so planned timestamps land on a route that will be driven in
    a different order and produce a list where stop 2 arrives before stop 1. Walking the
    accepted sequence makes monotonicity structural rather than lucky.
  - **`test_a_late_route_stops_claiming_its_original_times`.** The difference between an
    ETA and a plan. If it were written once at acceptance, a route running an hour behind
    would keep telling four recipients the original times.
  - **`test_planned_eta_survives_every_refresh`.** And the reason `eta` moving does not
    destroy the measurement it was supposed to provide.
  - **`test_an_unlocated_stop_ends_the_walk`.** You cannot know when a driver reaches
    stop 3 if you do not know where stop 2 is. Refusing beats interpolating.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.driver_routes import (
    accept_offer,
    arrive_at_stop,
    complete_stop,
    list_my_offers,
    scan_parcels,
)
from app.batch_queue.clustering import miles_between
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.delivery.eta import refresh_route_etas
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.identity import resolve_location
from app.identity.inherited_dwell import PLANNING_FLOOR_MINUTES
from app.models.client import Client
from app.models.driver import Driver
from app.models.driver_location_ping import DriverLocationPing
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.receiver_profile import ReceiverProfile
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.optimizer.service import DispatchOptimizerService
from app.schemas.driver_app import CompleteStopBody, ScanParcelsBody
from app.schemas.fleet import DriverLocation, DriverState
from app.travel import PLACEHOLDER_STOP_SERVICE_MINUTES, minutes_for_miles
from tests.integration.conftest import make_driver_compliant

pytestmark = pytest.mark.integration

POD_PHOTO = "local-capture://pod/test/photo.jpg"


async def _seed(
    db_session,
    *,
    drop_count: int = 3,
    locate_drops: bool = True,
    shop_dock: dict | None = None,
):
    """`shop_dock`, when given, is the ReceiverProfile fields for a dock attached to
    the shop."""
    hub_id, client_id, shop_id, driver_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file")
    )
    db_session.add(
        Driver(
            id=driver_id,
            hub_id=hub_id,
            name="Sam O.",
            phone=f"+1555{uuid.uuid4().int % 10**7:07d}",
            vehicle_capacity_units=10,
        )
    )
    await db_session.commit()
    shop_location_id = await _dock(db_session, shop_dock) if shop_dock is not None else None
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address="220 Harbor St",
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
            location_id=shop_location_id,
        )
    )
    await db_session.commit()
    await make_driver_compliant(db_session, driver_id)

    now = datetime.now(timezone.utc)
    fleet = FleetStateManager()
    await fleet.upsert_driver_state(
        DriverState(
            driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=10
        )
    )
    await fleet.update_driver_location(
        DriverLocation(
            driver_id=str(driver_id), lat=30.26, lng=-97.73, recorded_at=now.isoformat()
        ),
        hub_id=str(hub_id),
    )

    queue = HoldQueueStore()
    orders = []
    for index in range(drop_count):
        # Genuinely spread out, so the legs are long enough that a travel estimate is
        # distinguishable from zero and the ordering assertions mean something.
        order = Order(
            hub_id=hub_id,
            client_id=client_id,
            shop_id=shop_id,
            external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
            source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
            source_system="flat_file",
            raw_payload={},
            sla_tier="T2",
            hold_deadline=now - timedelta(minutes=1),
            weight_units=1,
            status=OrderStatus.held,
            requested_at=now,
            promised_at=now + timedelta(hours=3),
            delivery_address=f"{900 + index} Congress Ave, Austin TX",
            delivery_lat=(30.30 + index * 0.05) if locate_drops else None,
            delivery_lng=(-97.80 - index * 0.05) if locate_drops else None,
        )
        db_session.add(order)
        await db_session.commit()
        orders.append(order)

        await queue.add(
            str(hub_id),
            HeldOrder(
                order_id=str(order.id),
                shop_lat=30.264,
                shop_lng=-97.730,
                sla_tier="T2",
                hold_deadline=now - timedelta(minutes=1),
                held_since=now - timedelta(minutes=10),
                shop_name="Midtown Auto Parts",
                delivery_lat=float(order.delivery_lat) if locate_drops else None,
                delivery_lng=float(order.delivery_lng) if locate_drops else None,
            ),
        )

    return hub_id, driver_id, orders


async def _dock(db_session, profile_fields: dict) -> uuid.UUID:
    """A dock with the given ReceiverProfile fields, as the nightly refresh would
    have left it."""
    location = await resolve_location(
        db_session, address=f"{uuid.uuid4().int % 9000 + 100} Dock Rd, Austin, TX"
    )
    db_session.add(ReceiverProfile(location_id=location.id, **profile_fields))
    await db_session.commit()
    return location.id


def _leg_minutes(a: tuple[float, float], b: tuple[float, float]) -> float:
    return minutes_for_miles(miles_between(a[0], a[1], b[0], b[1]))


def _gap_minutes(earlier: Stop, later: Stop) -> float:
    return (later.eta - earlier.eta).total_seconds() / 60.0


SHOP = (30.264, -97.730)
FLAT = PLACEHOLDER_STOP_SERVICE_MINUTES


async def _coords(db_session, stop: Stop) -> tuple[float, float]:
    """Where a stop is. The optimizer's stub orders the drops as it likes, so a test
    that cares about distances reads them off the stop rather than assuming."""
    if stop.stop_type == "pickup":
        return SHOP
    order = await _order_at(db_session, stop)
    return float(order.delivery_lat), float(order.delivery_lng)


async def _order_at(db_session, stop: Stop) -> Order:
    order_id = (
        await db_session.execute(select(StopOrder.order_id).where(StopOrder.stop_id == stop.id))
    ).scalar_one()
    return await db_session.get(Order, order_id)


async def _assert_every_gap_is_leg_plus_dwell(
    db_session, stops: list[Stop], *, at_shop: float, after: dict | None = None
):
    """Each consecutive pair of stops is one leg apart plus the time at the earlier
    one's door: `at_shop` after the pickup, `after[stop.id]` where given, the
    placeholder everywhere else."""
    after = after or {}
    for earlier, later in zip(stops, stops[1:]):
        here, there = await _coords(db_session, earlier), await _coords(db_session, later)
        dwell = at_shop if earlier.stop_type == "pickup" else after.get(earlier.id, FLAT)
        assert _gap_minutes(earlier, later) == pytest.approx(
            _leg_minutes(here, there) + dwell, abs=0.05
        ), (earlier.sequence, later.sequence, here, there, dwell)


def _authed(hub_id, driver_id) -> AuthedDriver:
    return AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")


async def _accept(db_session, hub_id, driver_id):
    authed = _authed(hub_id, driver_id)
    await DispatchOptimizerService().run_cycle(str(hub_id))
    offers = await list_my_offers(driver=authed, session=db_session)
    assert offers, "the optimizer produced no offer - the fixture is wrong, not the ETA code"
    route = await accept_offer(offers[0].offer_id, driver=authed, session=db_session)
    return authed, route


async def _stops(db_session, route_id) -> list[Stop]:
    rows = (
        (
            await db_session.execute(
                select(Stop).where(Stop.route_id == route_id).order_by(Stop.sequence)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await db_session.refresh(row)
    return rows


# ---------------------------------------------------------------------------
# Written at all
# ---------------------------------------------------------------------------


async def test_accepting_an_offer_writes_an_eta_on_every_stop(db_session, real_redis_client):
    """The bug, directly. Before this, every one of these was None."""
    hub_id, driver_id, orders = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)

    stops = await _stops(db_session, uuid.UUID(route.route_id))
    assert len(stops) >= 4  # one pickup, three drops
    assert all(s.eta is not None for s in stops), [
        (s.sequence, s.stop_type, s.eta) for s in stops
    ]


async def test_etas_are_monotonic_along_the_route(db_session, real_redis_client):
    """Stop 2 never arrives before stop 1.

    Structural, because the walk follows the sequence the driver will actually drive.
    Copying the optimizer's planned visit times would break this the moment a HOT_SHOT
    or the all-pickups-before-all-dropoffs rule re-ordered anything.
    """
    hub_id, driver_id, _ = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)

    etas = [s.eta for s in await _stops(db_session, uuid.UUID(route.route_id))]
    assert etas == sorted(etas), etas


async def test_every_stop_has_a_distinct_later_eta(db_session, real_redis_client):
    """Not all the same timestamp.

    A walk that forgot to accumulate would produce a valid, monotonic, useless list of
    identical times - which `sorted()` above would happily accept.
    """
    hub_id, driver_id, _ = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)

    etas = [s.eta for s in await _stops(db_session, uuid.UUID(route.route_id))]
    assert len(set(etas)) == len(etas), etas
    # And the last stop is meaningfully later than the first, not milliseconds later.
    assert etas[-1] - etas[0] > timedelta(minutes=10)


async def test_an_eta_is_never_in_the_past(db_session, real_redis_client):
    """A stale anchor must not produce an arrival time that has already happened.

    The driver's last ping here is two hours old. Walking forward from it unguarded
    would put the first stop or two in the past, which reads as a bug on the phone.
    """
    hub_id, driver_id, _ = await _seed(db_session, drop_count=2)
    _, route = await _accept(db_session, hub_id, driver_id)

    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    db_session.add(
        DriverLocationPing(
            driver_id=driver_id, hub_id=hub_id, lat=30.20, lng=-97.90, recorded_at=stale
        )
    )
    await db_session.commit()

    await refresh_route_etas(db_session, uuid.UUID(route.route_id))
    await db_session.commit()

    now = datetime.now(timezone.utc)
    for stop in await _stops(db_session, uuid.UUID(route.route_id)):
        assert stop.eta > now, (stop.sequence, stop.eta)


# ---------------------------------------------------------------------------
# Kept honest as the route runs
# ---------------------------------------------------------------------------


async def test_a_late_route_stops_claiming_its_original_times(db_session, real_redis_client):
    """The whole reason this is refreshed rather than written once.

    The driver takes far longer over the first stop than planned. Every remaining ETA
    has to move; a plan written at acceptance would still be promising the original
    times to everyone downstream.
    """
    hub_id, driver_id, _ = await _seed(db_session, drop_count=3)
    authed, route = await _accept(db_session, hub_id, driver_id)
    route_id = uuid.UUID(route.route_id)

    before = {s.sequence: s.eta for s in await _stops(db_session, route_id)}

    stops = await _stops(db_session, route_id)
    first = stops[0]
    await arrive_at_stop(str(first.id), driver=authed, session=db_session)
    # An hour late leaving the first stop. Anchoring the walk here is what moves the
    # rest of the route.
    db_session.add(
        DriverLocationPing(
            driver_id=driver_id,
            hub_id=hub_id,
            lat=30.264,
            lng=-97.730,
            recorded_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db_session.commit()
    await refresh_route_etas(db_session, route_id)
    await db_session.commit()

    after = {s.sequence: s.eta for s in await _stops(db_session, route_id)}
    remaining = [s for s in await _stops(db_session, route_id) if s.sequence > first.sequence]
    assert remaining, "fixture should leave stops after the first"
    for stop in remaining:
        assert after[stop.sequence] > before[stop.sequence], (
            stop.sequence,
            before[stop.sequence],
            after[stop.sequence],
        )


async def test_an_eta_freezes_the_moment_the_driver_arrives(db_session, real_redis_client):
    """Not at completion - at arrival.

    "When will you get here" is answered the instant the driver pulls up, so recomputing
    from then on would replace a forecast with a description of the present. This test
    originally asserted the freeze happened at completion and caught the difference: the
    stop's ETA had already been rewritten by `arrive_at_stop`, 35ms after the fact.
    """
    hub_id, driver_id, _ = await _seed(db_session, drop_count=2)
    authed, route = await _accept(db_session, hub_id, driver_id)
    route_id = uuid.UUID(route.route_id)

    pickup = (await _stops(db_session, route_id))[0]
    await arrive_at_stop(str(pickup.id), driver=authed, session=db_session)
    # Read after arriving: this is the value that must now be immovable.
    on_arrival = (await _stops(db_session, route_id))[0].eta
    assert on_arrival is not None
    # A pickup will not complete until every parcel is accounted for (W10).
    await scan_parcels(
        str(pickup.id),
        ScanParcelsBody(scanned_count=pickup.parcel_count),
        driver=authed,
        session=db_session,
    )
    await complete_stop(
        str(pickup.id),
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )

    await refresh_route_etas(db_session, route_id)
    await db_session.commit()

    reloaded = (await _stops(db_session, route_id))[0]
    assert reloaded.eta == on_arrival


async def test_planned_eta_survives_every_refresh(db_session, real_redis_client):
    """The measurement I1 asked for, protected from the thing that makes `eta` useful.

    `eta` moves; `planned_eta` does not. `arrived_at - planned_eta` is then a real error
    over a real horizon rather than a comparison against a number recomputed seconds
    before arrival.
    """
    hub_id, driver_id, _ = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)
    route_id = uuid.UUID(route.route_id)

    planned = {s.sequence: s.planned_eta for s in await _stops(db_session, route_id)}
    assert all(v is not None for v in planned.values())

    db_session.add(
        DriverLocationPing(
            driver_id=driver_id,
            hub_id=hub_id,
            lat=30.10,
            lng=-98.10,
            recorded_at=datetime.now(timezone.utc) + timedelta(minutes=90),
        )
    )
    await db_session.commit()
    await refresh_route_etas(db_session, route_id)
    await db_session.commit()

    after = await _stops(db_session, route_id)
    assert {s.sequence: s.planned_eta for s in after} == planned
    # And the live value did move, or this test would be asserting nothing.
    assert any(s.eta != planned[s.sequence] for s in after)


# ---------------------------------------------------------------------------
# Refusing rather than guessing
# ---------------------------------------------------------------------------


async def test_an_unlocated_stop_ends_the_walk(db_session, real_redis_client):
    """No coordinates, no ETA - and none for anything after it either.

    The pickup is at a real shop so it still gets one. The drops have no delivery
    coordinates, so the moment the walk reaches the first of them it stops: the arrival
    time at a later stop is unknowable without knowing where the earlier one is.
    """
    hub_id, driver_id, _ = await _seed(db_session, drop_count=2, locate_drops=False)
    _, route = await _accept(db_session, hub_id, driver_id)

    stops = await _stops(db_session, uuid.UUID(route.route_id))
    pickups = [s for s in stops if s.stop_type == "pickup"]
    dropoffs = [s for s in stops if s.stop_type == "dropoff"]

    assert pickups and all(s.eta is not None for s in pickups)
    assert dropoffs and all(s.eta is None for s in dropoffs)


async def test_the_result_says_why_it_stopped(db_session, real_redis_client):
    """"Wrote none" and "wrote none because a stop has no address" are different facts,
    and only the second one is actionable."""
    hub_id, driver_id, _ = await _seed(db_session, drop_count=2, locate_drops=False)
    _, route = await _accept(db_session, hub_id, driver_id)

    result = await refresh_route_etas(db_session, uuid.UUID(route.route_id))
    assert result["reason"].endswith("unlocated")
    assert result["written"] >= 1  # the pickup still got one


async def test_refreshing_an_unknown_route_is_not_an_error(db_session):
    """It runs inside driver transitions, so it has to be safe on a route that has
    since gone."""
    result = await refresh_route_etas(db_session, uuid.uuid4())
    assert result == {"written": 0, "reason": "no_route"}


# ---------------------------------------------------------------------------
# The recipient sees the same number
# ---------------------------------------------------------------------------


async def test_a_recipient_who_is_not_next_gets_the_route_eta(db_session, real_redis_client):
    """The case that used to fall through to `promised_at`.

    Rule 1 of the tracking page withholds the driver's position unless this drop is the
    driver's current stop - correct, and it left everyone else looking at what we
    committed to rather than when we now expect to arrive. A route running an hour late
    told three of its four recipients that nothing had changed.

    Now they get their own stop's ETA, walked along the driver's actual remaining route,
    and it is distinct from `promised_at` - which the fixture deliberately sets three
    hours out so the two cannot be confused.
    """
    from app.tracking.service import ensure_tracking_token, resolve_tracking

    hub_id, driver_id, orders = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)

    stops = await _stops(db_session, uuid.UUID(route.route_id))
    dropoffs = [s for s in stops if s.stop_type == "dropoff"]
    # The LAST drop - unambiguously not the driver's current stop.
    last_drop = dropoffs[-1]
    order_id = (
        await db_session.execute(
            select(StopOrder.order_id).where(StopOrder.stop_id == last_drop.id)
        )
    ).scalar_one()

    order = await db_session.get(Order, order_id)
    token = await ensure_tracking_token(db_session, order)
    await db_session.commit()

    view = await resolve_tracking(db_session, token)
    assert view.driver_position is None  # rule 1 still holds
    assert view.estimated_arrival == last_drop.eta
    assert view.estimated_arrival != order.promised_at


# ---------------------------------------------------------------------------
# Time at the door is the dock's own
# ---------------------------------------------------------------------------


async def test_an_unknown_dock_gets_the_flat_stop_time(db_session, real_redis_client):
    """The baseline, so the tests below mean something: with no dock on file every
    gap is the drive plus the placeholder."""
    hub_id, driver_id, orders = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)
    stops = await _stops(db_session, uuid.UUID(route.route_id))

    await _assert_every_gap_is_leg_plus_dwell(db_session, stops, at_shop=FLAT)


async def test_a_measured_dock_replaces_the_flat_stop_time_with_its_own_median(
    db_session, real_redis_client
):
    """Twenty minutes at a counter we have measured twelve times is twenty minutes
    in every ETA after it, not eight."""
    hub_id, driver_id, orders = await _seed(
        db_session, drop_count=3, shop_dock={"dwell_sample_count": 12, "dwell_p50_seconds": 1200}
    )
    _, route = await _accept(db_session, hub_id, driver_id)
    stops = await _stops(db_session, uuid.UUID(route.route_id))

    await _assert_every_gap_is_leg_plus_dwell(db_session, stops, at_shop=20.0)


async def test_a_thin_dock_figure_is_not_planned_on(db_session, real_redis_client):
    """Two visits make arithmetic, not a dwell. The placeholder stands until there
    are more."""
    hub_id, driver_id, orders = await _seed(
        db_session, drop_count=2, shop_dock={"dwell_sample_count": 2, "dwell_p50_seconds": 1200}
    )
    _, route = await _accept(db_session, hub_id, driver_id)
    stops = await _stops(db_session, uuid.UUID(route.route_id))

    await _assert_every_gap_is_leg_plus_dwell(db_session, stops, at_shop=FLAT)


async def test_a_dock_whose_median_is_zero_is_still_planned_a_minute(db_session, real_redis_client):
    """Ten taps of Arrive and Complete back to back make a median of zero seconds.
    Walking in and out of a counter is never free."""
    hub_id, driver_id, orders = await _seed(
        db_session, drop_count=2, shop_dock={"dwell_sample_count": 12, "dwell_p50_seconds": 0}
    )
    _, route = await _accept(db_session, hub_id, driver_id)
    stops = await _stops(db_session, uuid.UUID(route.route_id))

    await _assert_every_gap_is_leg_plus_dwell(db_session, stops, at_shop=PLANNING_FLOOR_MINUTES)


async def test_an_inherited_dwell_is_planned_on_at_a_dock_we_have_not_visited(
    db_session, real_redis_client
):
    """The cold-start prior doing its job: forty of a previous operator's visits say
    five minutes, and we have none of our own."""
    hub_id, driver_id, orders = await _seed(
        db_session,
        drop_count=2,
        shop_dock={
            "dwell_sample_count": 0,
            "inherited_dwell_p50_seconds": 300,
            "inherited_dwell_sample_count": 40,
            "inherited_dwell_source": "design partner export",
        },
    )
    _, route = await _accept(db_session, hub_id, driver_id)
    stops = await _stops(db_session, uuid.UUID(route.route_id))

    await _assert_every_gap_is_leg_plus_dwell(db_session, stops, at_shop=5.0)


async def _dock_the_first_drop_with_a_stop_after_it(db_session, stops, profile_fields) -> Stop:
    """Give a delivery door a dock, after acceptance: whichever drop the stub put
    first has a stop after it to be delayed, whatever order it chose."""
    first_drop = next(s for s in stops if s.stop_type == "dropoff")
    assert first_drop.sequence < stops[-1].sequence
    order = await _order_at(db_session, first_drop)
    order.delivery_location_id = await _dock(db_session, profile_fields)
    await db_session.commit()
    return first_drop


async def test_a_delivery_door_has_its_own_dwell_too(db_session, real_redis_client):
    """A drop-off reaches its dock through the order's delivery door (#182), so a
    slow receiving bay delays the stops after it while the unknown shop keeps the
    placeholder."""
    hub_id, driver_id, orders = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)
    route_id = uuid.UUID(route.route_id)
    docked = await _dock_the_first_drop_with_a_stop_after_it(
        db_session, await _stops(db_session, route_id), {"dwell_sample_count": 15, "dwell_p50_seconds": 900}
    )

    await refresh_route_etas(db_session, route_id)
    await db_session.commit()
    stops = await _stops(db_session, route_id)

    await _assert_every_gap_is_leg_plus_dwell(db_session, stops, at_shop=FLAT, after={docked.id: 15.0})


async def test_a_merged_dock_answers_with_the_surviving_docks_figure(db_session, real_redis_client):
    """A merge repoints shops but not the delivery door on orders taken before it.
    Those must read the surviving dock's figure, not lose it."""
    hub_id, driver_id, orders = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)
    route_id = uuid.UUID(route.route_id)
    docked = await _dock_the_first_drop_with_a_stop_after_it(
        db_session, await _stops(db_session, route_id), {"dwell_sample_count": 15, "dwell_p50_seconds": 900}
    )
    order = await _order_at(db_session, docked)
    surviving = order.delivery_location_id
    absorbed = await resolve_location(
        db_session, address=f"{uuid.uuid4().int % 9000 + 100} Old Dock Rd, Austin, TX"
    )
    absorbed.merged_into_id = surviving
    order.delivery_location_id = absorbed.id
    await db_session.commit()

    await refresh_route_etas(db_session, route_id)
    await db_session.commit()
    stops = await _stops(db_session, route_id)

    await _assert_every_gap_is_leg_plus_dwell(db_session, stops, at_shop=FLAT, after={docked.id: 15.0})


async def test_a_driver_at_a_door_is_not_leaving_it_yet(db_session, real_redis_client):
    """On arrival the current stop freezes and the walk used to start the next leg at
    that instant - as if the driver turned round at the counter. They leave when the
    dock's dwell runs out."""
    hub_id, driver_id, orders = await _seed(
        db_session, drop_count=2, shop_dock={"dwell_sample_count": 12, "dwell_p50_seconds": 1200}
    )
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    arrived_pickup, next_stop, *_ = await _stops(db_session, uuid.UUID(route.route_id))
    arrived_at = arrived_pickup.arrived_at

    # Five minutes into a twenty-minute stop, and the phone has not pinged since.
    await refresh_route_etas(
        db_session, uuid.UUID(route.route_id), now=arrived_at + timedelta(minutes=5)
    )
    await db_session.commit()
    _, next_stop, *_ = await _stops(db_session, uuid.UUID(route.route_id))

    leaves = arrived_at + timedelta(minutes=20)
    expected = leaves + timedelta(minutes=_leg_minutes(SHOP, await _coords(db_session, next_stop)))
    assert abs((next_stop.eta - expected).total_seconds()) < 3


async def test_a_stop_left_on_arrived_earlier_does_not_hold_up_the_route(
    db_session, real_redis_client
):
    """The wait applies to the door the driver is at now. A driver who arrived at
    one stop, drove on without completing it and has since finished another is not
    still standing at the first - however slow that dock usually is."""
    hub_id, driver_id, orders = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)
    route_id = uuid.UUID(route.route_id)
    pickup, left_on_arrived, finished, last = await _stops(db_session, route_id)
    now = datetime.now(timezone.utc)
    pickup.status, pickup.arrived_at, pickup.completed_at = (
        "completed", now - timedelta(minutes=50), now - timedelta(minutes=45)
    )
    left_on_arrived.status, left_on_arrived.arrived_at = "arrived", now - timedelta(minutes=30)
    finished.status, finished.arrived_at, finished.completed_at = (
        "completed", now - timedelta(minutes=10), now - timedelta(minutes=5)
    )
    await db_session.commit()
    await refresh_route_etas(db_session, route_id, now=now)
    await db_session.commit()
    *_, last = await _stops(db_session, route_id)
    before = last.eta

    # Make the forgotten stop a forty-five-minute dock. Arrived 30 minutes ago, a
    # wait keyed on it would push the rest of the route a quarter of an hour.
    order = await _order_at(db_session, left_on_arrived)
    order.delivery_location_id = await _dock(
        db_session, {"dwell_sample_count": 12, "dwell_p50_seconds": 2700}
    )
    await db_session.commit()
    await refresh_route_etas(db_session, route_id, now=now)
    await db_session.commit()
    *_, last = await _stops(db_session, route_id)

    assert last.eta == before


async def test_a_cancelled_stop_is_driven_past_not_to(db_session, real_redis_client):
    """Dispatch marks a cancelled order's stops rather than deleting them (#187). The
    walk used to drive to them and wait there, so every later ETA carried a leg and a
    dwell for a stop nobody was visiting."""
    hub_id, driver_id, orders = await _seed(db_session, drop_count=2)
    _, route = await _accept(db_session, hub_id, driver_id)
    route_id = uuid.UUID(route.route_id)
    pickup, cancelled, last = await _stops(db_session, route_id)
    before = last.eta

    cancelled.status = "cancelled"
    await db_session.commit()
    result = await refresh_route_etas(db_session, route_id)
    await db_session.commit()
    pickup, _, last = await _stops(db_session, route_id)

    assert result == {"written": 2, "reason": "ok"}
    assert last.eta < before
    assert _gap_minutes(pickup, last) == pytest.approx(
        _leg_minutes(SHOP, await _coords(db_session, last)) + FLAT, abs=0.05
    )


async def test_a_stop_flagged_since_is_where_the_driver_last_was(db_session, real_redis_client):
    """A flag is the driver leaving a stop as much as a completion is, and it is
    dated by the flag, not by the arrival before it. A driver who arrived at one
    stop, then at another and flagged that one a minute ago, is not standing at
    the first."""
    hub_id, driver_id, orders = await _seed(db_session, drop_count=3)
    _, route = await _accept(db_session, hub_id, driver_id)
    route_id = uuid.UUID(route.route_id)
    pickup, left_on_arrived, flagged, last = await _stops(db_session, route_id)
    now = datetime.now(timezone.utc)
    pickup.status, pickup.arrived_at, pickup.completed_at = (
        "completed", now - timedelta(minutes=50), now - timedelta(minutes=45)
    )
    left_on_arrived.status, left_on_arrived.arrived_at = "arrived", now - timedelta(minutes=30)
    # Arrived before the other was, and flagged after it: the arrival alone would
    # date it earlier than the stop left on "arrived".
    flagged.status, flagged.arrived_at, flagged.flagged_at = (
        "failed", now - timedelta(minutes=40), now - timedelta(minutes=1)
    )
    await db_session.commit()
    await refresh_route_etas(db_session, route_id, now=now)
    await db_session.commit()
    *_, last = await _stops(db_session, route_id)
    before = last.eta

    order = await _order_at(db_session, left_on_arrived)
    order.delivery_location_id = await _dock(
        db_session, {"dwell_sample_count": 12, "dwell_p50_seconds": 2700}
    )
    await db_session.commit()
    await refresh_route_etas(db_session, route_id, now=now)
    await db_session.commit()
    *_, last = await _stops(db_session, route_id)

    assert last.eta == before


async def test_our_own_median_is_not_hidden_by_a_thin_inherited_one(db_session, real_redis_client):
    """Seven of our own visits at ten minutes, and two inherited at five. The
    inherited figure is too thin to plan on, and that is no reason to throw our
    own away for the placeholder."""
    hub_id, driver_id, orders = await _seed(
        db_session,
        drop_count=2,
        shop_dock={
            "dwell_sample_count": 7,
            "dwell_p50_seconds": 600,
            "inherited_dwell_p50_seconds": 300,
            "inherited_dwell_sample_count": 2,
            "inherited_dwell_source": "design partner export",
        },
    )
    _, route = await _accept(db_session, hub_id, driver_id)
    stops = await _stops(db_session, uuid.UUID(route.route_id))

    await _assert_every_gap_is_leg_plus_dwell(db_session, stops, at_shop=10.0)
