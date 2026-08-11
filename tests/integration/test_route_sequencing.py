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

from app.api.driver_routes import complete_stop
from app.driver_auth.dependencies import AuthedDriver
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
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
