"""
Reaching the two states that were declared and never used (docs/ROADMAP.md L11),
against real Postgres + Redis.

`Stop.status` has documented `pending | en_route | arrived | completed | failed` since
the model was written and nothing ever wrote `en_route`. `OrderStatus.en_route_drop` is
in the enum and in the transition map, and nothing ever advanced an order into it - so a
client watching their delivery went `PICKED_UP -> DELIVERED`, and F3's tracking page had
to derive "your driver is on the way" from stop rows because the status could not be
trusted to say it.

**The test that justifies the design is
`test_only_the_next_dropoff_goes_en_route_on_a_multi_stop_route`.** L11's own note
rejects stamping `en_route_drop` at pickup completion as a meaningless timestamp, and on
a multi-stop route it is worse: a driver who collects four orders and drives to the first
customer is not en route to the fourth, and marking all four would tell three clients
their driver is inbound while he is thirty minutes away.

**And `test_scanning_still_requires_arrival` is the regression that filling a dead state
caused.** Four guards spelled "has this driver arrived" as `status == "pending"`, which
was correct only while `pending` was the sole pre-arrival state. A dead state is not
inert.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.driver_routes import (
    accept_offer,
    arrive_at_stop,
    complete_stop,
    flag_stop_issue,
    get_my_route,
    list_my_offers,
    scan_parcels,
)
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.models.stop import Stop
from app.optimizer.service import DispatchOptimizerService
from app.schemas.driver_app import (
    CompleteStopBody,
    FlagStopBody,
    ScanParcelsBody,
    StopFailureReason,
)
from app.schemas.fleet import DriverLocation, DriverState
from tests.integration.conftest import make_driver_compliant

pytestmark = pytest.mark.integration

POD_PHOTO = "local-capture://pod/test/photo.jpg"


async def _seed(db_session, *, drop_count: int = 1):
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
            phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=10,
        )
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address="220 Harbor St",
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
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

    orders = []
    queue = HoldQueueStore()
    for index in range(drop_count):
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
            delivery_address=f"{900 + index} Congress Ave, Austin TX",
            # Spread the drops so the optimizer produces a real sequence.
            delivery_lat=30.27 + index * 0.01,
            delivery_lng=-97.75 - index * 0.01,
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
                delivery_lat=float(order.delivery_lat),
                delivery_lng=float(order.delivery_lng),
            ),
        )

    return hub_id, driver_id, orders


def _authed(hub_id, driver_id) -> AuthedDriver:
    return AuthedDriver(
        driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device"
    )


async def _accept(db_session, hub_id, driver_id):
    authed = _authed(hub_id, driver_id)
    await DispatchOptimizerService().run_cycle(str(hub_id))
    offers = await list_my_offers(driver=authed, session=db_session)
    route = await accept_offer(offers[0].offer_id, driver=authed, session=db_session)
    return authed, route


async def _stop_row(db_session, stop_id) -> Stop:
    stop = await db_session.get(Stop, uuid.UUID(stop_id))
    await db_session.refresh(stop)
    return stop


# ---------------------------------------------------------------------------
# Stop.status = en_route
# ---------------------------------------------------------------------------


async def test_accepting_a_route_puts_the_first_stop_en_route(db_session, real_redis_client):
    """`Stop.status` documented this value from the start and nothing ever wrote it."""
    hub_id, driver_id, _orders = await _seed(db_session)
    _authed_driver, route = await _accept(db_session, hub_id, driver_id)

    first = min(route.stops, key=lambda s: s.sequence)
    assert (await _stop_row(db_session, first.stop_id)).status == "en_route"


async def test_completing_a_stop_puts_the_next_one_en_route(db_session, real_redis_client):
    hub_id, driver_id, _orders = await _seed(db_session)
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    dropoff = next(s for s in route.stops if s.stop_type == "dropoff")

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(
        pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session
    )
    await complete_stop(
        pickup.stop_id,
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )

    assert (await _stop_row(db_session, dropoff.stop_id)).status == "en_route"


async def test_arrival_is_not_walked_backwards(db_session, real_redis_client):
    """Idempotent: re-running this against an already-arrived stop would undo the
    driver's own progress, and flagging one stop on a route can trigger it while another
    is arrived at."""
    from app.delivery.en_route import mark_current_stop_en_route

    hub_id, driver_id, _orders = await _seed(db_session)
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await mark_current_stop_en_route(db_session, uuid.UUID(route.route_id))
    await db_session.commit()

    assert (await _stop_row(db_session, pickup.stop_id)).status == "arrived"


# ---------------------------------------------------------------------------
# OrderStatus.en_route_drop
# ---------------------------------------------------------------------------


async def test_an_order_reaches_en_route_drop(db_session, real_redis_client):
    """The state that existed in the machine and was never reached, so a client's order
    went straight from PICKED_UP to DELIVERED."""
    hub_id, driver_id, orders = await _seed(db_session)
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(
        pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session
    )
    await complete_stop(
        pickup.stop_id,
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )

    await db_session.refresh(orders[0])
    assert orders[0].status == OrderStatus.en_route_drop


async def test_only_the_next_dropoff_goes_en_route_on_a_multi_stop_route(
    db_session, real_redis_client
):
    """**The reason the trigger is the stop sequence and not pickup completion.**

    L11 rejects stamping at pickup as meaningless; on a multi-stop route it is worse than
    meaningless. A driver who collects three orders and drives to the first customer is
    not en route to the third, and marking all three would tell two clients their driver
    is inbound while he is half an hour away - the opposite of what F3's tracking page is
    for.
    """
    hub_id, driver_id, orders = await _seed(db_session, drop_count=3)
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    dropoffs = sorted(
        (s for s in route.stops if s.stop_type == "dropoff"), key=lambda s: s.sequence
    )
    assert len(dropoffs) == 3, "the optimizer should have produced three drops"

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(
        pickup.stop_id,
        ScanParcelsBody(scanned_count=(await _stop_row(db_session, pickup.stop_id)).parcel_count),
        driver=authed,
        session=db_session,
    )
    await complete_stop(
        pickup.stop_id,
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )

    statuses = []
    for order in orders:
        await db_session.refresh(order)
        statuses.append(order.status)

    # Exactly one order is "on the way": the one whose drop is next.
    assert statuses.count(OrderStatus.en_route_drop) == 1
    assert statuses.count(OrderStatus.picked_up) == 2

    # And it is the FIRST drop in the sequence, not an arbitrary one.
    first_drop = await _stop_row(db_session, dropoffs[0].stop_id)
    assert first_drop.status == "en_route"
    assert (await _stop_row(db_session, dropoffs[1].stop_id)).status == "pending"


async def test_delivering_one_drop_promotes_the_next(db_session, real_redis_client):
    """The point of a sequence-driven signal: it keeps working down the route without
    anyone tapping anything."""
    hub_id, driver_id, orders = await _seed(db_session, drop_count=2)
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    dropoffs = sorted(
        (s for s in route.stops if s.stop_type == "dropoff"), key=lambda s: s.sequence
    )

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(
        pickup.stop_id,
        ScanParcelsBody(scanned_count=(await _stop_row(db_session, pickup.stop_id)).parcel_count),
        driver=authed,
        session=db_session,
    )
    await complete_stop(
        pickup.stop_id,
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )

    # Deliver the first drop; the second should become the one being driven to.
    await arrive_at_stop(dropoffs[0].stop_id, driver=authed, session=db_session)
    await complete_stop(
        dropoffs[0].stop_id,
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )

    assert (await _stop_row(db_session, dropoffs[1].stop_id)).status == "en_route"
    second_order_ids = {str(o.id) for o in orders}
    en_route = []
    for order in orders:
        await db_session.refresh(order)
        if order.status == OrderStatus.en_route_drop:
            en_route.append(order)
    assert len(en_route) == 1
    assert str(en_route[0].id) in second_order_ids


async def test_a_flagged_stop_also_promotes_the_next(db_session, real_redis_client):
    """A flagged stop is finished too - the driver moves on, and the status has to."""
    hub_id, driver_id, orders = await _seed(db_session, drop_count=2)
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    dropoffs = sorted(
        (s for s in route.stops if s.stop_type == "dropoff"), key=lambda s: s.sequence
    )

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(
        pickup.stop_id,
        ScanParcelsBody(scanned_count=(await _stop_row(db_session, pickup.stop_id)).parcel_count),
        driver=authed,
        session=db_session,
    )
    await complete_stop(
        pickup.stop_id,
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )

    await arrive_at_stop(dropoffs[0].stop_id, driver=authed, session=db_session)
    await flag_stop_issue(
        dropoffs[0].stop_id,
        FlagStopBody(reason=StopFailureReason.REFUSED),
        driver=authed,
        session=db_session,
    )

    assert (await _stop_row(db_session, dropoffs[1].stop_id)).status == "en_route"


async def test_a_failed_order_is_not_dragged_back_to_on_the_way(
    db_session, real_redis_client
):
    """advance_orders skips what the machine forbids, so an order flagged on a stop
    stays flagged rather than being pulled back into en_route_drop."""
    hub_id, driver_id, orders = await _seed(db_session)
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    dropoff = next(s for s in route.stops if s.stop_type == "dropoff")

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(
        pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session
    )
    await complete_stop(
        pickup.stop_id,
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )
    await arrive_at_stop(dropoff.stop_id, driver=authed, session=db_session)
    await flag_stop_issue(
        dropoff.stop_id,
        FlagStopBody(reason=StopFailureReason.REFUSED),
        driver=authed,
        session=db_session,
    )

    await db_session.refresh(orders[0])
    assert orders[0].status == OrderStatus.delivery_failed


# ---------------------------------------------------------------------------
# The regression filling a dead state caused
# ---------------------------------------------------------------------------


async def test_scanning_still_requires_arrival(db_session, real_redis_client):
    """**The bug introducing `en_route` caused.** Four guards spelled "has this driver
    arrived" as `status == "pending"`, correct only while `pending` was the sole
    pre-arrival state. Once a stop could be `en_route`, a driver merely driving toward it
    could scan its parcels and complete it - from anywhere."""
    hub_id, driver_id, _orders = await _seed(db_session)
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")

    # The stop is now `en_route`, not `pending`.
    assert (await _stop_row(db_session, pickup.stop_id)).status == "en_route"

    with pytest.raises(HTTPException) as exc_info:
        await scan_parcels(
            pickup.stop_id,
            ScanParcelsBody(scanned_count=1),
            driver=authed,
            session=db_session,
        )
    assert exc_info.value.status_code == 409


async def test_completing_still_requires_arrival(db_session, real_redis_client):
    hub_id, driver_id, _orders = await _seed(db_session)
    authed, route = await _accept(db_session, hub_id, driver_id)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")

    with pytest.raises(HTTPException) as exc_info:
        await complete_stop(
            pickup.stop_id,
            CompleteStopBody(method="photo", photo_url=POD_PHOTO),
            driver=authed,
            session=db_session,
        )
    assert exc_info.value.status_code == 409


async def test_the_route_view_shows_the_en_route_stop(db_session, real_redis_client):
    """So the app can highlight where the driver is headed rather than showing a route
    of identical pending stops."""
    hub_id, driver_id, _orders = await _seed(db_session)
    authed, _route = await _accept(db_session, hub_id, driver_id)

    view = await get_my_route(driver=authed, session=db_session)

    en_route = [s for s in view.stops if s.status == "en_route"]
    assert len(en_route) == 1
    assert en_route[0].sequence == min(s.sequence for s in view.stops)
