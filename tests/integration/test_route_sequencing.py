"""
Interleaved routes: collect A, collect B, drop B, drop A.

The optimizer has always planned routes this way - `optimizeTours` is given a shipment
with a pickup and a delivery and solves both legs together, which is what makes its
travel times and feasibility correct. What it could not do is *tell us*:
`RouteAssignment` modelled a route as a flat list of order ids, so `_visit_sequence`
deduplicated the two visits per shipment down to one and the interleaved drop ordering
was discarded on the way out of the client.

`accept_offer` then rebuilt a route as "every pickup, then every dropoff", which is a
legal route but not the one that was solved for. Two consequences: the trips are longer
than the plan the solver costed, and `Stop.eta` could only ever be straight-line
distance at an assumed speed, because the solver's own arrival times describe a sequence
we were not driving.

**The guard this breaks is the interesting part**, and it is why
`test_a_delivery_is_allowed_once_its_own_pickup_is_done` comes first in this file.
`complete_stop` used to ask "is any earlier-sequenced pickup on this route still open?"
and rely on pickups-before-dropoffs to make that mean "have this order's parcels been
collected?". Under interleaving those diverge, and the proxy refuses a delivery whose own
parcels are already aboard. A dead invariant is not inert - the same lesson `en_route`
taught when filling a declared-but-unused status silently defeated four arrival checks.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.driver_routes import accept_offer, complete_stop
from app.driver_auth.dependencies import AuthedDriver
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.schemas.driver_app import CompleteStopBody
from tests.integration.conftest import make_driver_compliant

pytestmark = pytest.mark.integration

POD_PHOTO = "local-capture://pod/test/photo.jpg"


async def _interleaved_route(db_session):
    """A route built by hand in the order the solver actually plans.

    Constructed directly rather than through `accept_offer` on purpose: this asserts a
    property of `complete_stop`, and it has to hold for any legal route ordering rather
    than only the one today's construction happens to produce.

        seq 0  pickup   order A   (still pending - the trap)
        seq 1  pickup   order B   (completed)
        seq 2  dropoff  order B   (arrived)  <- must be allowed
        seq 3  dropoff  order A   (arrived)

    Both dropoffs are left `arrived` because `complete_stop` checks arrival before it
    checks anything else, and this file is about the pickup guard. A driver would only
    be at one of them at a time; nothing here depends on that.
    """
    hub_id, client_id, driver_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
            phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=10,
        )
    )
    await db_session.commit()
    await make_driver_compliant(db_session, driver_id)

    shops = {}
    for label, (lat, lng) in {"A": (30.26, -97.73), "B": (30.28, -97.76)}.items():
        shop_id = uuid.uuid4()
        db_session.add(
            Shop(
                id=shop_id,
                client_id=client_id,
                name=f"Shop {label}",
                address=f"{label} Harbor St",
                lat=lat,
                lng=lng,
                external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
            )
        )
        shops[label] = shop_id
    await db_session.commit()

    now = datetime.now(timezone.utc)
    orders = {}
    for label in ("A", "B"):
        order = Order(
            hub_id=hub_id,
            client_id=client_id,
            shop_id=shops[label],
            external_order_ref=f"ORD-{label}-{uuid.uuid4().hex[:6]}",
            source_order_ref=f"REF-{label}-{uuid.uuid4().hex[:6]}",
            source_system="flat_file",
            raw_payload={},
            sla_tier="T2",
            hold_deadline=now,
            weight_units=1,
            status=OrderStatus.picked_up,
            requested_at=now,
            delivery_address=f"{label} Congress Ave",
            delivery_lat=30.30,
            delivery_lng=-97.80,
        )
        db_session.add(order)
        orders[label] = order
    await db_session.commit()

    route = Route(hub_id=hub_id, driver_id=driver_id, status="active")
    db_session.add(route)
    await db_session.flush()

    stops = {}
    plan = [
        ("pickup", "A", "pending"),
        ("pickup", "B", "completed"),
        ("dropoff", "B", "arrived"),
        ("dropoff", "A", "arrived"),
    ]
    for sequence, (kind, label, status) in enumerate(plan):
        stop = Stop(
            route_id=route.id,
            shop_id=shops[label] if kind == "pickup" else None,
            sequence=sequence,
            stop_type=kind,
            status=status,
            parcel_count=1,
            scanned_count=1 if kind == "pickup" else 0,
            completed_at=now if status == "completed" else None,
            arrived_at=now if status == "arrived" else None,
        )
        db_session.add(stop)
        await db_session.flush()
        db_session.add(StopOrder(stop_id=stop.id, order_id=orders[label].id))
        stops[f"{kind}_{label}"] = stop
    await db_session.commit()

    authed = AuthedDriver(
        driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device"
    )
    return authed, stops


async def test_a_delivery_is_allowed_once_its_own_pickup_is_done(
    db_session, real_redis_client
):
    """Order B's parcels are aboard, so B can be delivered.

    Order A's pickup is still open and sits *earlier* in the sequence. The old guard
    counted that and refused - blocking a delivery whose goods the driver is physically
    carrying, on the strength of an unrelated stop. This is the test that separates
    "have this order's parcels been collected" from "is every earlier pickup done".
    """
    authed, stops = await _interleaved_route(db_session)

    result = await complete_stop(
        str(stops["dropoff_B"].id),
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )
    assert result.status == "completed"


async def test_a_delivery_is_still_refused_before_its_own_pickup(
    db_session, real_redis_client
):
    """The half of the guard that must not be lost while fixing the other half.

    Order A has not been collected. Delivering it would mean recording proof for parcels
    the driver never picked up, which is exactly what the guard exists to prevent.
    """
    authed, stops = await _interleaved_route(db_session)

    with pytest.raises(HTTPException) as exc:
        await complete_stop(
            str(stops["dropoff_A"].id),
            CompleteStopBody(method="photo", photo_url=POD_PHOTO),
            driver=authed,
            session=db_session,
        )
    assert exc.value.status_code == 409
    assert "Collect this order" in exc.value.detail


async def test_a_failed_pickup_does_not_block_its_delivery_forever(
    db_session, real_redis_client
):
    """Pre-existing behaviour, preserved deliberately.

    A failed pickup is never going to become completed, so treating it as "unfinished"
    would leave its dropoff permanently uncompletable - a stop the driver can neither
    finish nor escape. The guard tests for a *non-terminal* pickup, not for a completed
    one, and this is the case that distinguishes those.
    """
    authed, stops = await _interleaved_route(db_session)
    stops["pickup_A"].status = "failed"
    stops["pickup_A"].completed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db_session.commit()

    result = await complete_stop(
        str(stops["dropoff_A"].id),
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Building a route from the optimizer's plan
# ---------------------------------------------------------------------------


async def _fixture(db_session, *, tiers: dict[str, str], shops: dict[str, str]):
    """Orders keyed by label, each at the named shop, on one driver's hub."""
    hub_id, client_id, driver_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
            phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=20,
        )
    )
    await db_session.commit()
    await make_driver_compliant(db_session, driver_id)

    shop_ids: dict[str, uuid.UUID] = {}
    for index, name in enumerate(sorted(set(shops.values()))):
        shop_id = uuid.uuid4()
        db_session.add(
            Shop(
                id=shop_id,
                client_id=client_id,
                name=name,
                address=f"{index} Harbor St",
                lat=30.26 + index * 0.02,
                lng=-97.73 - index * 0.02,
                external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
            )
        )
        shop_ids[name] = shop_id
    await db_session.commit()

    now = datetime.now(timezone.utc)
    orders: dict[str, Order] = {}
    for index, (label, tier) in enumerate(tiers.items()):
        order = Order(
            hub_id=hub_id,
            client_id=client_id,
            shop_id=shop_ids[shops[label]],
            external_order_ref=f"ORD-{label}-{uuid.uuid4().hex[:6]}",
            source_order_ref=f"REF-{label}-{uuid.uuid4().hex[:6]}",
            source_system="flat_file",
            raw_payload={},
            sla_tier=tier,
            hold_deadline=now + timedelta(minutes=30),
            weight_units=1,
            status=OrderStatus.assigned,
            requested_at=now,
            delivery_address=f"{label} Congress Ave",
            delivery_lat=30.30 + index * 0.03,
            delivery_lng=-97.80 - index * 0.03,
        )
        db_session.add(order)
        orders[label] = order
    await db_session.commit()

    authed = AuthedDriver(
        driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device"
    )
    return hub_id, driver_id, orders, authed


async def _accept_with_plan(db_session, hub_id, driver_id, orders, authed, plan):
    """Offer these orders with an explicit planned leg sequence, and accept it.

    `plan` is a list of (label, kind) pairs. Constructed directly rather than via a
    dispatch cycle because the stub optimizer has no drop locations and therefore no
    basis for interleaving - the plans under test here are the ones the real solver
    produces, and this is how to exercise them without a Google account.
    """
    now = datetime.now(timezone.utc)
    offer = RouteOffer(
        hub_id=hub_id,
        driver_id=driver_id,
        status="offered",
        stop_payload=[
            {
                "order_id": str(order.id),
                "lat": 30.26,
                "lng": -97.73,
                "sla_tier": order.sla_tier.value if hasattr(order.sla_tier, "value") else order.sla_tier,
                "shop_name": "Shop",
            }
            for order in orders.values()
        ],
        visit_payload=None
        if plan is None
        else [
            {"order_id": str(orders[label].id), "kind": kind, "arrival": None}
            for label, kind in plan
        ],
        offered_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    db_session.add(offer)
    await db_session.commit()
    return await accept_offer(str(offer.id), driver=authed, session=db_session)


def _readable(route, orders):
    """The route as [(label, kind), ...] in sequence, for asserting against a plan."""
    by_id = {str(order.id): label for label, order in orders.items()}
    out = []
    for stop in sorted(route.stops, key=lambda s: s.sequence):
        labels = sorted(by_id[oid] for oid in stop.order_ids)
        out.append(("+".join(labels), "pickup" if stop.stop_type == "pickup" else "delivery"))
    return out


async def test_the_route_follows_the_planned_leg_order(db_session, real_redis_client):
    """Collect A, collect B, drop B, drop A - driven in that order.

    The headline. This plan was always what the solver produced; the route built from it
    used to be "collect A, collect B, drop A, drop B" because the drop ordering was
    discarded on the way out of the client.
    """
    hub_id, driver_id, orders, authed = await _fixture(
        db_session, tiers={"A": "T2", "B": "T2"}, shops={"A": "Shop A", "B": "Shop B"}
    )
    route = await _accept_with_plan(
        db_session,
        hub_id,
        driver_id,
        orders,
        authed,
        [("A", "pickup"), ("B", "pickup"), ("B", "delivery"), ("A", "delivery")],
    )
    assert _readable(route, orders) == [
        ("A", "pickup"),
        ("B", "pickup"),
        ("B", "delivery"),
        ("A", "delivery"),
    ]


async def test_a_drop_can_precede_a_later_collection(db_session, real_redis_client):
    """The invariant this work deliberately breaks.

    "Every pickup before every dropoff" held only because the old construction imposed
    it. A genuinely optimal route can deliver something and then collect something else,
    and this asserts the route is allowed to say so.
    """
    hub_id, driver_id, orders, authed = await _fixture(
        db_session, tiers={"A": "T2", "B": "T2"}, shops={"A": "Shop A", "B": "Shop B"}
    )
    route = await _accept_with_plan(
        db_session,
        hub_id,
        driver_id,
        orders,
        authed,
        [("A", "pickup"), ("A", "delivery"), ("B", "pickup"), ("B", "delivery")],
    )
    stops = sorted(route.stops, key=lambda s: s.sequence)
    pickups = [s.sequence for s in stops if s.stop_type == "pickup"]
    dropoffs = [s.sequence for s in stops if s.stop_type == "dropoff"]
    assert min(dropoffs) < max(pickups)


async def test_consecutive_pickups_at_one_shop_become_one_stop(db_session, real_redis_client):
    """Commingling survives interleaving. One visit to one door is one stop."""
    hub_id, driver_id, orders, authed = await _fixture(
        db_session,
        tiers={"A": "T2", "B": "T2"},
        shops={"A": "Shop A", "B": "Shop A"},
    )
    route = await _accept_with_plan(
        db_session,
        hub_id,
        driver_id,
        orders,
        authed,
        [("A", "pickup"), ("B", "pickup"), ("A", "delivery"), ("B", "delivery")],
    )
    pickups = [s for s in route.stops if s.stop_type == "pickup"]
    assert len(pickups) == 1
    assert sorted(pickups[0].order_ids) == sorted([str(orders["A"].id), str(orders["B"].id)])
    assert pickups[0].parcel_count == 2


async def test_a_shop_revisited_later_is_a_second_stop(db_session, real_redis_client):
    """Non-consecutive visits to the same shop are not merged.

    If the plan goes shop A, shop B, shop A, the solver had a reason - a time window, a
    capacity limit - and collapsing the two visits into one stop would silently discard
    it and give the driver a route that does not match the plan's travel times.
    """
    hub_id, driver_id, orders, authed = await _fixture(
        db_session,
        tiers={"A": "T2", "B": "T2", "C": "T2"},
        shops={"A": "Shop A", "B": "Shop B", "C": "Shop A"},
    )
    route = await _accept_with_plan(
        db_session,
        hub_id,
        driver_id,
        orders,
        authed,
        [
            ("A", "pickup"),
            ("B", "pickup"),
            ("B", "delivery"),
            ("C", "pickup"),
            ("A", "delivery"),
            ("C", "delivery"),
        ],
    )
    assert orders["A"].shop_id == orders["C"].shop_id  # same shop, by construction
    at_shop_a = [
        stop
        for stop in route.stops
        if stop.stop_type == "pickup"
        and {str(orders["A"].id), str(orders["C"].id)} & set(stop.order_ids)
    ]
    assert len(at_shop_a) == 2, _readable(route, orders)


# ---------------------------------------------------------------------------
# HOT_SHOT: the promise that must survive the reordering
# ---------------------------------------------------------------------------


async def test_a_hot_shot_is_never_commingled_even_under_a_plan(
    db_session, real_redis_client
):
    """The tier is sold as direct point-to-point. It shares a stop with nothing.

    Same shop, consecutive pickup legs - the exact case commingling exists for - and it
    still gets its own stop.
    """
    hub_id, driver_id, orders, authed = await _fixture(
        db_session,
        tiers={"H": "HOT_SHOT", "R": "T2"},
        shops={"H": "Shop A", "R": "Shop A"},
    )
    route = await _accept_with_plan(
        db_session,
        hub_id,
        driver_id,
        orders,
        authed,
        [("H", "pickup"), ("R", "pickup"), ("H", "delivery"), ("R", "delivery")],
    )
    pickups = [s for s in route.stops if s.stop_type == "pickup"]
    assert len(pickups) == 2
    assert all(len(p.order_ids) == 1 for p in pickups)


async def test_a_hot_shot_is_collected_and_delivered_first(db_session, real_redis_client):
    """Both legs hoisted, which is stronger than what this replaced.

    The solver is told a HOT_SHOT must not be skipped (a million-unit penalty) but is
    never told when it is due - `hold_deadline` is not sent as a time window - so it has
    no reason to schedule one early. Letting the plan govern unqualified would quietly
    stop prioritising a tier customers pay extra for.

    Note what the old construction did: every pickup before every dropoff meant a hot
    shot's *delivery* waited behind every other collection on the route. Hoisting both
    legs is closer to the direct trip being sold, not merely equivalent.
    """
    hub_id, driver_id, orders, authed = await _fixture(
        db_session,
        tiers={"R1": "T2", "H": "HOT_SHOT", "R2": "T2"},
        shops={"R1": "Shop A", "H": "Shop B", "R2": "Shop C"},
    )
    # A plan that deliberately buries the hot shot in the middle.
    route = await _accept_with_plan(
        db_session,
        hub_id,
        driver_id,
        orders,
        authed,
        [
            ("R1", "pickup"),
            ("H", "pickup"),
            ("R1", "delivery"),
            ("H", "delivery"),
            ("R2", "pickup"),
            ("R2", "delivery"),
        ],
    )
    readable = _readable(route, orders)
    assert readable[:2] == [("H", "pickup"), ("H", "delivery")], readable
    # And the rest keeps its planned order.
    assert readable[2:] == [
        ("R1", "pickup"),
        ("R1", "delivery"),
        ("R2", "pickup"),
        ("R2", "delivery"),
    ], readable


# ---------------------------------------------------------------------------
# Falling back rather than building something a driver gets stuck in
# ---------------------------------------------------------------------------


async def test_an_offer_with_no_plan_still_works(db_session, real_redis_client):
    """The in-flight case at deploy time.

    Offers live for `job_offer_ttl_seconds`, so migration 0042 lands while real offers
    with no visit payload are in front of real drivers. Rejecting those would make a
    mid-shift deploy refuse work somebody was about to accept.
    """
    hub_id, driver_id, orders, authed = await _fixture(
        db_session, tiers={"A": "T2", "B": "T2"}, shops={"A": "Shop A", "B": "Shop B"}
    )
    route = await _accept_with_plan(db_session, hub_id, driver_id, orders, authed, None)

    stops = sorted(route.stops, key=lambda s: s.sequence)
    pickups = [s.sequence for s in stops if s.stop_type == "pickup"]
    dropoffs = [s.sequence for s in stops if s.stop_type == "dropoff"]
    # The old shape, exactly: every pickup, then every dropoff.
    assert max(pickups) < min(dropoffs)
    assert len(stops) == 4


async def test_a_plan_missing_a_leg_falls_back(db_session, real_redis_client):
    """A plan we cannot execute is worse than no plan.

    A drop with no collection can never complete - `complete_stop`'s pickup guard would
    refuse it forever - and a collection with no drop leaves parcels aboard. Rather than
    build a route with a stop the driver is stuck in, fall back to the construction that
    always produces both legs.
    """
    hub_id, driver_id, orders, authed = await _fixture(
        db_session, tiers={"A": "T2", "B": "T2"}, shops={"A": "Shop A", "B": "Shop B"}
    )
    route = await _accept_with_plan(
        db_session,
        hub_id,
        driver_id,
        orders,
        authed,
        # B's delivery leg is missing.
        [("A", "pickup"), ("B", "pickup"), ("A", "delivery")],
    )
    stops = sorted(route.stops, key=lambda s: s.sequence)
    assert len(stops) == 4  # both orders got both legs
    assert max(s.sequence for s in stops if s.stop_type == "pickup") < min(
        s.sequence for s in stops if s.stop_type == "dropoff"
    )


async def test_etas_are_still_monotonic_on_an_interleaved_route(
    db_session, real_redis_client
):
    """The ETA walk follows the sequence, so interleaving must not break it.

    app/delivery/eta.py walks the accepted sequence rather than the solver's plan, which
    is what made monotonicity structural. That property has to survive a route where the
    sequence itself now zig-zags between shops and doors.
    """
    hub_id, driver_id, orders, authed = await _fixture(
        db_session, tiers={"A": "T2", "B": "T2"}, shops={"A": "Shop A", "B": "Shop B"}
    )
    route = await _accept_with_plan(
        db_session,
        hub_id,
        driver_id,
        orders,
        authed,
        [("A", "pickup"), ("B", "pickup"), ("B", "delivery"), ("A", "delivery")],
    )
    etas = [s.eta for s in sorted(route.stops, key=lambda s: s.sequence)]
    assert all(e is not None for e in etas), etas
    assert etas == sorted(etas), etas
