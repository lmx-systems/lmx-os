"""
Integration coverage for the driver location pipeline (docs/ROADMAP.md F1)
against real Postgres + Redis.

What this closes is narrower than "add GPS" and worth stating precisely,
because the pieces were half-present before. Redis already held a driver's
current position and app/optimizer/service.py already read it - but the
only way to *write* it was POST /fleet/{hub_id}/drivers/location, which
requires an ops admin. So in production nothing would ever have populated
a driver's position, and the optimizer skips any driver whose location is
None: a real fleet would have been assigned no work at all. The last test
in this file pins that behaviour down, since it's the actual reason this
work was prioritised.

Calls the route functions directly, same pattern as
tests/integration/test_driver_app_integration.py and
tests/integration/test_dashboard_enrichment.py.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.api.driver_routes import report_my_location
from app.api.routes import list_fleet_overview
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.models.client import Client
from app.models.driver import Driver
from app.models.driver_location_ping import DriverLocationPing
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.optimizer.service import DispatchOptimizerService
from app.schemas.driver_app import DriverLocationPingBody
from app.schemas.fleet import DriverState

pytestmark = pytest.mark.integration


async def _seed_driver(db_session, *, status: str = "available"):
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Location Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()

    db_session.add(
        Driver(
            id=driver_id, hub_id=hub_id, name="Sam O.", phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=5,
        )
    )
    await db_session.commit()

    await FleetStateManager().upsert_driver_state(
        DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status=status, capacity_units=5)
    )
    return hub_id, driver_id


def _authed(hub_id, driver_id) -> AuthedDriver:
    return AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")


async def test_ping_writes_both_redis_current_position_and_the_durable_trail(db_session, real_redis_client):
    """The two writes serve different consumers and neither substitutes for
    the other: Redis is the optimizer's hot path, Postgres is what makes
    miles-per-drop computable at all (W9's scorecard)."""
    hub_id, driver_id = await _seed_driver(db_session)
    recorded_at = datetime.now(timezone.utc)

    await report_my_location(
        DriverLocationPingBody(lat=34.0512, lng=-118.2512, recorded_at=recorded_at, accuracy_m=8.5),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    live = await FleetStateManager().get_driver_location(str(hub_id), str(driver_id))
    assert live is not None
    assert live.lat == pytest.approx(34.0512)
    assert live.lng == pytest.approx(-118.2512)

    rows = (
        await db_session.execute(
            select(DriverLocationPing).where(DriverLocationPing.driver_id == driver_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].lat == pytest.approx(34.0512)
    assert rows[0].accuracy_m == pytest.approx(8.5)
    # Denormalized from the token, so a hub's whole fleet trail is queryable
    # without joining through drivers.
    assert rows[0].hub_id == hub_id


async def test_successive_pings_append_a_trail_while_redis_holds_only_the_latest(db_session, real_redis_client):
    """The whole reason the Postgres table exists. If pings overwrote here
    the way they do in Redis, distance travelled would be unrecoverable."""
    hub_id, driver_id = await _seed_driver(db_session)
    start = datetime.now(timezone.utc)

    for index, (lat, lng) in enumerate([(34.050, -118.250), (34.055, -118.255), (34.060, -118.260)]):
        await report_my_location(
            DriverLocationPingBody(lat=lat, lng=lng, recorded_at=start + timedelta(minutes=index)),
            driver=_authed(hub_id, driver_id),
            session=db_session,
        )

    trail = (
        await db_session.execute(
            select(DriverLocationPing)
            .where(DriverLocationPing.driver_id == driver_id)
            .order_by(DriverLocationPing.recorded_at)
        )
    ).scalars().all()
    assert [row.lat for row in trail] == pytest.approx([34.050, 34.055, 34.060])

    live = await FleetStateManager().get_driver_location(str(hub_id), str(driver_id))
    assert live.lat == pytest.approx(34.060)


async def test_ping_lands_in_the_drivers_own_hub_not_one_supplied_by_the_client(db_session, real_redis_client):
    """The request body has no hub field at all - hub comes from the JWT. This
    pins that down so a future 'convenience' hub_id parameter can't be added
    without breaking a test, since it would let a driver write into another
    hub's fleet state."""
    hub_id, driver_id = await _seed_driver(db_session)
    other_hub_id = uuid.uuid4()

    assert "hub" not in DriverLocationPingBody.model_fields

    await report_my_location(
        DriverLocationPingBody(lat=34.05, lng=-118.25, recorded_at=datetime.now(timezone.utc)),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    manager = FleetStateManager()
    assert await manager.get_driver_location(str(hub_id), str(driver_id)) is not None
    assert await manager.get_driver_location(str(other_hub_id), str(driver_id)) is None


async def test_recorded_at_is_the_devices_observation_time_not_the_write_time(db_session, real_redis_client):
    """A ping queued through a dead zone must land at the moment it happened.
    Every distance/replay computation orders by recorded_at, so collapsing an
    offline stretch onto the reconnect instant would fabricate a stationary
    driver followed by a teleport."""
    hub_id, driver_id = await _seed_driver(db_session)
    observed_at = datetime.now(timezone.utc) - timedelta(minutes=20)

    await report_my_location(
        DriverLocationPingBody(lat=34.05, lng=-118.25, recorded_at=observed_at),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    row = (
        await db_session.execute(
            select(DriverLocationPing).where(DriverLocationPing.driver_id == driver_id)
        )
    ).scalar_one()
    assert row.recorded_at == observed_at
    # created_at is the write time and deliberately trails it.
    assert row.created_at > row.recorded_at


@pytest.mark.parametrize(
    "lat,lng",
    [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)],
)
def test_out_of_range_coordinates_are_rejected_at_the_edge(lat, lng):
    with pytest.raises(ValidationError):
        DriverLocationPingBody(lat=lat, lng=lng, recorded_at=datetime.now(timezone.utc))


def test_negative_accuracy_is_rejected():
    with pytest.raises(ValidationError):
        DriverLocationPingBody(
            lat=34.05, lng=-118.25, recorded_at=datetime.now(timezone.utc), accuracy_m=-1
        )


async def test_fleet_overview_exposes_the_last_reported_position(db_session, real_redis_client):
    """What makes the ops map (F2) buildable - the roster previously carried
    status and capacity but no position, so the dashboard could list a
    driver's stops and never say where they were."""
    hub_id, driver_id = await _seed_driver(db_session)
    recorded_at = datetime.now(timezone.utc)

    await report_my_location(
        DriverLocationPingBody(lat=34.0577, lng=-118.2577, recorded_at=recorded_at),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    roster = await list_fleet_overview(str(hub_id), session=db_session)
    assert len(roster) == 1
    assert roster[0].lat == pytest.approx(34.0577)
    assert roster[0].lng == pytest.approx(-118.2577)
    assert roster[0].location_recorded_at is not None
    # The existing display-name enrichment still works alongside it.
    assert roster[0].name == "Sam O."


async def test_fleet_overview_reports_null_position_for_a_driver_who_never_pinged(db_session, real_redis_client):
    """Null here is the diagnostic for "why is nobody being assigned work" -
    see the optimizer test below."""
    hub_id, driver_id = await _seed_driver(db_session)

    roster = await list_fleet_overview(str(hub_id), session=db_session)
    assert len(roster) == 1
    assert roster[0].lat is None
    assert roster[0].lng is None
    assert roster[0].location_recorded_at is None


async def test_optimizer_assigns_nothing_until_a_driver_reports_a_position(db_session, real_redis_client):
    """The reason F1 was prioritised ahead of every other open gap. An
    available driver with no known position is invisible to
    app/optimizer/service.py's candidate generation, so before a
    driver-authenticated write path existed a real fleet would have sat idle
    with a full hold queue.
    """
    hub_id, driver_id = await _seed_driver(db_session)

    client_id, shop_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id, client_id=client_id, name="Midtown Auto Parts", address="220 Harbor St",
            lat=34.051, lng=-118.251, external_ref=f"SHOP-LOC-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()

    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref=f"ORD-LOC-{uuid.uuid4().hex[:8]}", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now + timedelta(minutes=30), weight_units=1,
        status=OrderStatus.held, requested_at=now,
        delivery_address="14 Oak Ave", delivery_lat=34.0530, delivery_lng=-118.2530,
    )
    db_session.add(order)
    await db_session.commit()

    await HoldQueueStore().add(
        str(hub_id),
        HeldOrder(
            order_id=str(order.id), shop_lat=34.051, shop_lng=-118.251, sla_tier="T2",
            hold_deadline=now + timedelta(minutes=30), held_since=now, shop_name="Midtown Auto Parts",
        ),
    )

    service = DispatchOptimizerService()

    # No position reported yet: the order is held and a driver is available,
    # and still nothing can be assigned.
    before = await service.run_cycle(str(hub_id))
    assert before.assignments == []

    await report_my_location(
        DriverLocationPingBody(lat=34.0511, lng=-118.2511, recorded_at=now),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    after = await service.run_cycle(str(hub_id))
    assert len(after.assignments) == 1
    assert after.assignments[0].driver_id == str(driver_id)
