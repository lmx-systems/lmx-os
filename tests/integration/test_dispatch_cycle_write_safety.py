"""The dispatch cycle's writes, and the writers that race them (`CON-5`).

Left out of `CON-4`'s assign on purpose. Three gaps, all older than the assign:

  - a cycle whose offers fail left the orders it moved `assigned` to nobody;
  - every status writer but the cycle wrote over whatever the row had become,
    so a dispatcher's cancel queued behind a driver's pickup cancelled a
    collected order;
  - the offers did not lock the orders they carried, so a cancel landing
    between the cycle's status write and its offers' commit went through with
    the order still on an offer.
"""
import asyncio
import contextlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.api.driver_routes import accept_offer
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.db import session_scope
from app.delivery.resolution import RETURN_TO_SHOP, OrderNotFailedError, resolve_failed_order
from app.fleet_state.manager import FleetStateManager
from app.models.order import Order, OrderStatus
from app.models.route_offer import RouteOffer
from app.models.stop import Stop
from app.optimizer.service import DispatchOptimizerService
from app.orders.cancellation import (
    OFFER_WITHDRAWN,
    STOPS_REMOVED,
    OrderNotCancellable,
    cancel_before_collection,
    cancel_live_order,
)
from app.orders.requeue import return_stranded_assignments
from app.orders.state_machine import legal_sources
from app.orders.status_service import advance_orders
from tests.integration import test_driver_app_integration as driver_app
from tests.integration.conftest import make_driver_compliant
from tests.integration.queue_helpers import let_the_hold_run_out
from tests.integration.test_dispatch_assigns_an_order import _assign, _held_ids, _idle

pytestmark = pytest.mark.integration


async def _waits(task: asyncio.Task) -> None:
    await asyncio.sleep(0.3)
    assert not task.done(), "this write should be waiting on the other transaction's lock"


async def _accepted(db_session):
    """An accepted route whose pickup the driver hasn't reached."""
    hub_id, _client, _shop, driver_id, order_id, authed = await _idle(db_session)
    result = await _assign(db_session, order_id, driver_id)
    route = await accept_offer(result.offer_id, driver=authed, session=db_session)
    return hub_id, driver_id, order_id, route


def _failing_offers(monkeypatch) -> None:
    import app.optimizer.service as service_module

    def no_offers(**_kwargs):
        raise RuntimeError("the offer insert failed")

    monkeypatch.setattr(service_module, "RouteOffer", no_offers)


async def _a_failed_cycle(db_session, monkeypatch):
    """A cycle that moved the hub's order to `assigned` and then failed to make
    its offer."""
    hub_id, _client, _shop, driver_id, order = await driver_app._seed(db_session)
    order_id = order.id
    await let_the_hold_run_out(hub_id)
    _failing_offers(monkeypatch)
    with pytest.raises(RuntimeError):
        await DispatchOptimizerService().run_cycle(str(hub_id))
    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.assigned
    return hub_id, driver_id, order_id


def _past_the_grace() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=settings.stranded_assignment_grace_seconds + 5)


# ---------------------------------------------------------------------------
# (2) A status write states the status it expects
# ---------------------------------------------------------------------------


class TestACancelBehindAPickup:
    async def test_it_waits_for_the_pickup_and_then_refuses(self, db_session, real_redis_client):
        """The roadmap's case. Dispatch's cancel read the pickup stop as not yet
        reached while the driver was completing it, waited on the stop, then
        marked the collected pickup cancelled and the `picked_up` order
        `cancelled` - legal for the state machine, as a return. It now reads the
        stops locked, waits for the driver, and refuses."""
        _hub, _driver, order_id, route = await _accepted(db_session)
        pickup_id = uuid.UUID(next(s.stop_id for s in route.stops if s.stop_type == "pickup"))

        async def the_cancel():
            async with session_scope() as session:
                order = await session.get(Order, order_id)
                return await cancel_live_order(session, HoldQueueStore(), order)

        async with session_scope() as driver:
            pickup = (
                await driver.execute(select(Stop).where(Stop.id == pickup_id).with_for_update())
            ).scalar_one()
            pickup.status = "completed"
            pickup.completed_at = datetime.now(timezone.utc)
            assert await advance_orders(driver, [order_id], OrderStatus.picked_up)
            await driver.flush()
            cancel = asyncio.create_task(the_cancel())
            await _waits(cancel)
            await driver.commit()

        with pytest.raises(OrderNotCancellable):
            await cancel
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.picked_up
        assert (await db_session.get(Stop, pickup_id)).status == "completed"

    async def test_the_cancel_itself_names_what_it_may_cancel(self, db_session, real_redis_client):
        """The backstop under the locked read: a `picked_up` order is not
        cancelled by dispatch's write even when nothing stopped the cancel
        earlier."""
        _hub, _driver, order_id, _route = await _accepted(db_session)
        order = await db_session.get(Order, order_id)
        order.status = OrderStatus.picked_up
        await db_session.commit()

        moved = await advance_orders(
            db_session, [order_id], OrderStatus.cancelled, only_from=(OrderStatus.assigned, OrderStatus.en_route_pickup)
        )

        assert moved == []


class TestADriverWriteBehindACancel:
    async def test_the_pickup_does_not_land_on_a_cancelled_order(self, db_session, real_redis_client):
        """A writer naming no precondition gets the state machine's. The driver's
        write read `en_route_pickup`, queued behind dispatch's cancel, and wrote
        `picked_up` over `cancelled`."""
        _hub, _driver, order_id, _route = await _accepted(db_session)

        async def the_pickup():
            async with session_scope() as driver:
                return await advance_orders(driver, [order_id], OrderStatus.picked_up)

        async with session_scope() as dispatch:
            row = (
                await dispatch.execute(select(Order).where(Order.id == order_id).with_for_update())
            ).scalar_one()
            row.status = OrderStatus.cancelled
            await dispatch.flush()
            pickup = asyncio.create_task(the_pickup())
            await _waits(pickup)
            await dispatch.commit()

        assert await pickup == []
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled

    async def test_a_newer_status_it_may_still_leave_from_is_taken(self, db_session, real_redis_client):
        """Not compare-and-set against the read: a delivery read `picked_up`,
        the order moved on to `en_route_drop` meanwhile, and the delivery must
        still land - a skip there would leave a completed stop's order short of
        delivered."""
        _hub, _driver, order_id, _route = await _accepted(db_session)
        order = await db_session.get(Order, order_id)
        order.status = OrderStatus.picked_up
        await db_session.commit()

        async def the_delivery():
            async with session_scope() as driver:
                return [o.id for o in await advance_orders(driver, [order_id], OrderStatus.delivered)]

        async with session_scope() as other:
            row = (
                await other.execute(select(Order).where(Order.id == order_id).with_for_update())
            ).scalar_one()
            row.status = OrderStatus.en_route_drop
            await other.flush()
            delivery = asyncio.create_task(the_delivery())
            await _waits(delivery)
            await other.commit()

        assert await delivery == [order_id]
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.delivered

    def test_the_machines_precondition(self):
        assert legal_sources(OrderStatus.picked_up) == {OrderStatus.assigned, OrderStatus.en_route_pickup}
        assert OrderStatus.cancelled not in legal_sources(OrderStatus.picked_up)
        assert OrderStatus.delivered not in legal_sources(OrderStatus.cancelled)

    async def test_updated_at_still_moves(self, db_session, real_redis_client):
        """Tracking-link expiry reads it."""
        _hub, _driver, order_id, _route = await _accepted(db_session)
        before = (await db_session.get(Order, order_id)).updated_at

        [moved] = await advance_orders(db_session, [order_id], OrderStatus.picked_up)
        await db_session.commit()

        db_session.expire_all()
        after = (await db_session.get(Order, order_id)).updated_at
        assert after > before
        assert moved.updated_at == after


class TestAClientCancelBehindTheCycle:
    async def test_it_waits_for_the_cycle_and_then_refuses(self, db_session, real_redis_client):
        """A client's cancel read `held`, queued behind the cycle's `assigned`,
        and cancelled an order whose offer was going out."""
        _hub, _client, _shop, _driver, order_id, _authed = await _idle(db_session)

        async def the_client():
            async with session_scope() as session:
                order = await session.get(Order, order_id)
                await cancel_before_collection(session, HoldQueueStore(), order)

        async with session_scope() as cycle:
            assert await advance_orders(
                cycle, [order_id], OrderStatus.assigned, only_from=(OrderStatus.held, OrderStatus.queued)
            )
            await cycle.flush()
            client = asyncio.create_task(the_client())
            await _waits(client)
            await cycle.commit()

        with pytest.raises(OrderNotCancellable):
            await client
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.assigned


class TestTwoResolutionsOfOneFailure:
    async def test_the_second_is_refused(self, db_session, real_redis_client):
        _hub, _client, _shop, _driver, order_id, _authed = await _idle(db_session)
        order = await db_session.get(Order, order_id)
        order.status = OrderStatus.delivery_failed
        await db_session.commit()

        async def the_second():
            async with session_scope() as session:
                order = await session.get(Order, order_id)
                await resolve_failed_order(session, HoldQueueStore(), order, RETURN_TO_SHOP)

        async with session_scope() as first:
            row = (
                await first.execute(select(Order).where(Order.id == order_id).with_for_update())
            ).scalar_one()
            row.status = OrderStatus.cancelled
            await first.flush()
            second = asyncio.create_task(the_second())
            await _waits(second)
            await first.commit()

        with pytest.raises(OrderNotFailedError):
            await second
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled


# ---------------------------------------------------------------------------
# (3) The offers lock the orders they carry
# ---------------------------------------------------------------------------


class TestACancelBetweenTheStatusWriteAndTheOffers:
    async def test_a_cancel_mid_offers_waits_and_withdraws_the_offer(self, db_session, real_redis_client):
        """The cancel lands while the cycle is making its offers. It found no
        offer and no stop, and cancelled the order; then the offer went out
        carrying it. Now it waits for the offers' commit and withdraws."""
        hub_id, _client, _shop, _driver, order = await driver_app._seed(db_session)
        order_id = order.id
        await let_the_hold_run_out(hub_id)
        service = DispatchOptimizerService()
        claiming, release = asyncio.Event(), asyncio.Event()
        real_claim = service._fleet_state.claim_for_offer

        async def slow_claim(*args, **kwargs):
            claiming.set()
            await release.wait()
            return await real_claim(*args, **kwargs)

        service._fleet_state.claim_for_offer = slow_claim
        cycle = asyncio.create_task(service.run_cycle(str(hub_id)))
        await asyncio.wait_for(claiming.wait(), timeout=10)

        async def the_cancel():
            async with session_scope() as session:
                row = await session.get(Order, order_id)
                return await cancel_live_order(session, HoldQueueStore(), row)

        cancel = asyncio.create_task(the_cancel())
        try:
            await _waits(cancel)
        finally:
            # Never left holding its transaction open, or teardown waits on it.
            release.set()
            await cycle

        assert await cancel == OFFER_WITHDRAWN
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled
        [offer] = (await db_session.execute(select(RouteOffer).where(RouteOffer.hub_id == hub_id))).scalars()
        assert offer.status == "withdrawn"

    async def test_a_cancel_before_the_offers_keeps_the_order_off_them(self, db_session, real_redis_client):
        """The cancel commits after the status write and before the offers: the
        offers read the order cancelled and leave it out, and the driver is not
        taken out of the pool for an offer with nothing on it."""
        hub_id, _client, _shop, driver_id, order = await driver_app._seed(db_session)
        order_id = order.id
        await let_the_hold_run_out(hub_id)
        service = DispatchOptimizerService()
        real_guard = service._giving_back_claims_on_failure
        outcomes = []

        @contextlib.asynccontextmanager
        async def cancel_first(claims):
            async with session_scope() as session:
                row = await session.get(Order, order_id)
                outcomes.append(await cancel_live_order(session, HoldQueueStore(), row))
            async with real_guard(claims):
                yield

        service._giving_back_claims_on_failure = cancel_first
        await service.run_cycle(str(hub_id))

        assert outcomes == [STOPS_REMOVED]
        offers = (
            await db_session.execute(select(func.count()).select_from(RouteOffer).where(RouteOffer.hub_id == hub_id))
        ).scalar_one()
        assert offers == 0
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "available"


# ---------------------------------------------------------------------------
# (1) Orders a failed cycle left `assigned` to nobody go back
# ---------------------------------------------------------------------------


class TestStrandedAssignments:
    async def test_they_go_back_to_the_queue_after_the_grace(self, db_session, real_redis_client, monkeypatch):
        hub_id, driver_id, order_id = await _a_failed_cycle(db_session, monkeypatch)
        assert str(order_id) not in await _held_ids(hub_id)

        returned = await return_stranded_assignments(db_session, str(hub_id), now=_past_the_grace())
        await db_session.commit()

        assert returned == 1
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held
        assert str(order_id) in await _held_ids(hub_id)
        # The driver was given back when the offers failed, and is left alone.
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "available"

    async def test_not_within_the_grace(self, db_session, real_redis_client, monkeypatch):
        """A cycle is between its status write and its offers for a moment; an
        order in that moment is not stranded."""
        hub_id, _driver, order_id = await _a_failed_cycle(db_session, monkeypatch)

        assert await return_stranded_assignments(db_session, str(hub_id)) == 0
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.assigned

    async def test_not_an_order_on_an_open_offer(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await _assign(db_session, order_id, driver_id)

        assert await return_stranded_assignments(db_session, str(hub_id), now=_past_the_grace()) == 0

    async def test_not_an_order_on_a_route(self, db_session, real_redis_client):
        hub_id, _driver, order_id, _route = await _accepted(db_session)
        order = await db_session.get(Order, order_id)
        order.status = OrderStatus.assigned  # whatever the status says, a stop holds it
        await db_session.commit()

        assert await return_stranded_assignments(db_session, str(hub_id), now=_past_the_grace()) == 0

    async def test_not_history(self, db_session, real_redis_client, monkeypatch):
        hub_id, _driver, order_id = await _a_failed_cycle(db_session, monkeypatch)
        order = await db_session.get(Order, order_id)
        order.intake_mode = "backfill"
        await db_session.commit()

        assert await return_stranded_assignments(db_session, str(hub_id), now=_past_the_grace()) == 0

    async def test_returning_them_does_not_wake_the_cycle(self, db_session, real_redis_client, monkeypatch):
        """Sending them back from the failure path woke the cycle into the same
        failure about once a second. The sweep runs before the scheduled cycles
        and wakes nothing."""
        import app.orders.requeue as requeue_module

        hub_id, _driver, _order_id = await _a_failed_cycle(db_session, monkeypatch)
        published = []

        async def record(hub, event):
            published.append((hub, event))

        monkeypatch.setattr(requeue_module.dispatch_event_bus, "publish", record)

        assert await return_stranded_assignments(db_session, str(hub_id), now=_past_the_grace()) == 1
        assert published == []


class TestTheScheduledSweep:
    @pytest.fixture
    def configured(self, monkeypatch):
        monkeypatch.setattr(settings, "internal_api_token", "a-real-internal-token-value")

    async def test_a_recurring_failure_costs_one_round_trip_per_sweep(
        self, db_session, real_redis_client, monkeypatch, configured
    ):
        """With the offers failing every time, each sweep sends the order back
        once and its cycle takes it again - one return per sweep, every five
        minutes in production, not one a second."""
        from app.api.internal_routes import run_dispatch_for_all_hubs

        hub_id, driver_id, order_id = await _a_failed_cycle(db_session, monkeypatch)
        await make_driver_compliant(db_session, driver_id)
        # Back in the queue with its own deadline, so the cycle releases it at once.
        order = await db_session.get(Order, order_id)
        order.hold_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db_session.commit()
        monkeypatch.setattr(settings, "stranded_assignment_grace_seconds", 0)

        for _ in range(2):
            await let_the_hold_run_out(hub_id)
            result = await run_dispatch_for_all_hubs(session=db_session)
            assert result["stranded_returned"] == {str(hub_id): 1}
            assert result["hubs"][str(hub_id)].startswith("error")

        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.assigned
