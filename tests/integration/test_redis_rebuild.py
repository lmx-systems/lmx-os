"""Putting back what Redis lost, from Postgres (app/optimizer/redis_rebuild.py).

A flushed or replaced Redis node came back empty, and nothing restored the hold
queue or the fleet state: held orders were never dispatched and on-shift drivers
were invisible to dispatch, while the liveness check reported healthy.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.internal_routes import run_dispatch_for_all_hubs
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.events.bus import hub_lock
from app.fleet_state.manager import FleetStateManager
from app.health.checks import check_hold_queue_matches_postgres
from app.models.driver import Driver
from app.models.driver_location_ping import DriverLocationPing
from app.models.order import INTAKE_BACKFILL, Order, OrderStatus
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.optimizer.redis_rebuild import SENTINEL_KEY, reconcile_redis_state
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverState
from tests.integration import test_driver_app_integration as driver_app

pytestmark = pytest.mark.integration


async def _another_order(db_session, hub_id, client_id, shop_id, **overrides) -> Order:
    now = datetime.now(timezone.utc)
    fields = dict(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now + timedelta(minutes=30), weight_units=1,
        status=OrderStatus.held, requested_at=now,
        delivery_address="9 Elm St", delivery_lat=34.054, delivery_lng=-118.254,
    )
    fields.update(overrides)
    order = Order(**fields)
    db_session.add(order)
    await db_session.commit()
    return order


async def test_a_flush_loses_nothing_the_queue_needs(db_session, real_redis_client):
    hub_id, client_id, shop_id, _driver_id, order = await driver_app._seed(db_session)
    released = await _another_order(db_session, hub_id, client_id, shop_id, status=OrderStatus.queued)
    backfilled = await _another_order(db_session, hub_id, client_id, shop_id, intake_mode=INTAKE_BACKFILL)
    offered = await _another_order(db_session, hub_id, client_id, shop_id, status=OrderStatus.assigned)
    await real_redis_client.flushdb()

    result = await reconcile_redis_state()

    queued = {h.order_id: h for h in await HoldQueueStore().get_all(str(hub_id))}
    assert set(queued) == {str(order.id), str(released.id)}
    # Never a backfilled order (a delivery that already happened) and never an
    # order on an offer (offering it again would double-dispatch).
    assert str(backfilled.id) not in queued and str(offered.id) not in queued
    entry = queued[str(order.id)]
    assert (entry.sla_tier, entry.shop_name) == ("T2", "Midtown Auto Parts")
    assert entry.delivery_lat == pytest.approx(34.053)
    assert result["lost"] is True and result["complete"] is True
    assert await real_redis_client.exists(SENTINEL_KEY)


async def test_the_rebuild_never_overwrites_a_live_entry(db_session, real_redis_client):
    hub_id, _client_id, _shop_id, _driver_id, order = await driver_app._seed(db_session)
    before = {h.order_id: h for h in await HoldQueueStore().get_all(str(hub_id))}[str(order.id)]

    await reconcile_redis_state()
    await reconcile_redis_state()

    after = await HoldQueueStore().get_all(str(hub_id))
    assert len(after) == 1 and after[0].held_since == before.held_since


async def test_after_a_flush_the_sweep_dispatches_the_held_order(db_session, real_redis_client):
    """End to end: Redis empties, the sweep rebuilds, and the order goes out."""
    hub_id, _client_id, _shop_id, driver_id, order = await driver_app._seed(db_session)
    driver = await db_session.get(Driver, driver_id)
    driver.status = "available"  # what the duty switch writes (#159)
    order.hold_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    # The driver's last ping, which every ping also writes to Postgres.
    db_session.add(DriverLocationPing(driver_id=driver_id, hub_id=hub_id, lat=34.0511, lng=-118.2511,
                                      recorded_at=datetime.now(timezone.utc) - timedelta(seconds=20)))
    await db_session.commit()
    await real_redis_client.flushdb()

    await run_dispatch_for_all_hubs(session=db_session)

    offers = (await db_session.scalars(select(RouteOffer).where(RouteOffer.driver_id == driver_id))).all()
    assert len(offers) == 1
    assert offers[0].stop_payload[0]["order_id"] == str(order.id)


async def test_fleet_state_is_derived_from_what_postgres_knows(db_session, real_redis_client):
    hub_id, _client_id, _shop_id, on_route, _order = await driver_app._seed(db_session)
    on_offer = uuid.uuid4()
    off = uuid.uuid4()
    gone = uuid.uuid4()
    db_session.add_all([
        Driver(id=on_offer, hub_id=hub_id, name="Offered O.", phone="+15555550301", vehicle_capacity_units=7),
        Driver(id=off, hub_id=hub_id, name="Off O.", phone="+15555550302", vehicle_capacity_units=3),
        Driver(id=gone, hub_id=hub_id, name="Gone G.", phone="+15555550303", vehicle_capacity_units=3,
               is_active=False, status="available"),
    ])
    await db_session.commit()
    route = Route(hub_id=hub_id, driver_id=on_route, status="active")
    now = datetime.now(timezone.utc)
    db_session.add(route)
    db_session.add(RouteOffer(hub_id=hub_id, driver_id=on_offer, status="offered", stop_payload=[],
                              offered_at=now, expires_at=now + timedelta(minutes=2)))
    await db_session.commit()
    await real_redis_client.flushdb()

    await reconcile_redis_state()

    manager = FleetStateManager()
    states = {d: await manager.get_driver_state(str(hub_id), str(d)) for d in (on_route, on_offer, off, gone)}
    assert (states[on_route].status, states[on_route].current_route_id) == ("en_route", str(route.id))
    assert (states[on_offer].status, states[on_offer].capacity_units) == ("offered", 7)
    assert states[off].status == "off_shift"
    assert states[gone].status == "off_shift"
    # Nobody here is offerable, so no second job lands on a driver mid-offer.
    assert await manager.get_available_driver_ids(str(hub_id)) == []
    assert set(await manager.get_all_driver_ids(str(hub_id))) == {str(d) for d in (on_route, on_offer, off, gone)}


async def test_offered_with_no_offer_outstanding_is_put_back(db_session, real_redis_client):
    """A cycle that set `offered` and then failed to commit its offer left the
    driver out of the pool until they toggled off and on."""
    hub_id, _client_id, _shop_id, driver_id, _order = await driver_app._seed(db_session)
    manager = FleetStateManager()
    await manager.upsert_driver_state(
        DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status="offered", capacity_units=5)
    )

    await reconcile_redis_state()

    assert (await manager.get_driver_state(str(hub_id), str(driver_id))).status == "available"


async def test_a_stale_entry_is_never_planned_and_is_pruned(db_session, real_redis_client):
    """A snapshot restore, or a write that raced a commit, can leave an entry for
    an order that is already delivered."""
    hub_id, _client_id, _shop_id, _driver_id, order = await driver_app._seed(db_session)
    order.status = OrderStatus.delivered
    await db_session.commit()
    store = HoldQueueStore()
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    await store.add(str(hub_id), HeldOrder(order_id=str(order.id), shop_lat=34.051, shop_lng=-118.251,
                                           sla_tier="T2", hold_deadline=past, held_since=past))

    outcome = await DispatchOptimizerService().run_cycle(str(hub_id))
    assert outcome.assignments == []

    await reconcile_redis_state()
    assert await store.order_ids(str(hub_id)) == set()


async def test_a_hub_mid_cycle_is_skipped_and_the_pass_stays_incomplete(db_session, real_redis_client):
    hub_id, *_ = await driver_app._seed(db_session)
    await real_redis_client.flushdb()

    async with hub_lock(str(hub_id)) as acquired:
        assert acquired
        result = await reconcile_redis_state()

    assert result["complete"] is False
    assert not await real_redis_client.exists(SENTINEL_KEY)


async def test_a_lock_is_released_only_by_its_owner(db_session, real_redis_client):
    async with hub_lock("hub-x") as first:
        assert first
        async with hub_lock("hub-x") as second:
            assert not second
        # The inner, unacquired context must not have released the outer lock.
        assert await real_redis_client.exists("events:running:hub-x")


async def test_the_health_check_sees_held_orders_missing_from_the_queue(db_session, real_redis_client):
    hub_id, _client_id, _shop_id, _driver_id, order = await driver_app._seed(db_session)
    order.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db_session.commit()
    assert (await check_hold_queue_matches_postgres()).ok

    await real_redis_client.flushdb()
    result = await check_hold_queue_matches_postgres()

    assert not result.ok and "1 waiting order" in result.detail


async def test_a_route_that_ends_on_a_flag_puts_the_driver_back_in_the_pool(db_session, real_redis_client):
    """It used to stay en_route until the driver toggled off and on."""
    from app.api.driver_routes import flag_stop_issue
    from app.schemas.driver_app import FlagStopBody, StopFailureReason

    hub_id, _client_id, _shop_id, driver_id, _order = await driver_app._seed(db_session)
    authed, pickup, dropoff = await driver_app._accept_one_offer(db_session, hub_id, driver_id)
    for stop in (pickup, dropoff):
        await flag_stop_issue(
            stop.stop_id, FlagStopBody(reason=StopFailureReason.SHOP_CLOSED), driver=authed, session=db_session
        )

    state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
    assert (state.status, state.current_route_id) == ("available", None)


async def test_an_accept_after_a_flush_keeps_the_vehicles_capacity(db_session, real_redis_client):
    """The fallback was 1, which sent the driver out with one slot."""
    from app.api.driver_routes import accept_offer, list_my_offers
    from app.driver_auth.dependencies import AuthedDriver
    from tests.integration.queue_helpers import let_the_hold_run_out

    hub_id, _client_id, _shop_id, driver_id, _order = await driver_app._seed(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await let_the_hold_run_out(hub_id)
    await DispatchOptimizerService().run_cycle(str(hub_id))
    [offer] = await list_my_offers(driver=authed, session=db_session)
    await real_redis_client.flushdb()

    await accept_offer(offer.offer_id, driver=authed, session=db_session)

    state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
    assert (state.status, state.capacity_units) == ("en_route", 5)
