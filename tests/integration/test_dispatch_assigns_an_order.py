"""Dispatch gives an order nobody has to a driver, or puts it back in the queue.

The exceptions queue's "nobody has it" row said "nobody has this order - assign
it or put it back in the queue", and the console offered only Cancel. An assign
sends an idle driver an offer they may decline (app/orders/assignment.py); a
driver already on a route is refused for now (`CON-5`). Putting it back is the
hold override, which already existed and which nothing in the console sent.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.api.admin_routes import assign_order
from app.api.driver_routes import accept_offer, decline_offer, list_my_offers
from app.api.routes import lookup_orders, override_order
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.hub_calendar import hub_local_date
from app.models.dispatch_assignment import DispatchAssignment
from app.models.driver import Driver
from app.models.driver_document import REQUIRED_DOC_TYPES
from app.models.hub import Hub
from app.models.hub_closure import HubClosure
from app.models.order import Order, OrderStatus
from app.models.route_offer import RouteOffer
from app.models.stop import Stop, StopOrder
from app.optimizer.google_routes_client import RouteOptimizationClient
from app.optimizer.service import _PLANNABLE, DispatchOptimizerService
from app.ops_auth.dependencies import AuthedOpsUser
from app.reporting.exceptions import KIND_UNPLACED, build_exception_queue
from app.schemas.admin import AssignOrderBody
from app.schemas.driver_app import OfferStopSummary
from app.schemas.optimizer import RouteAssignment, RouteVisit
from app.schemas.reporting import OverrideRequest
from tests.integration import test_driver_app_integration as driver_app
from tests.integration import test_optimizer_live_route_push as live
from tests.integration.conftest import make_driver_compliant
from tests.integration.queue_helpers import let_the_hold_run_out

pytestmark = pytest.mark.integration

DISPATCHER = AuthedOpsUser(
    ops_user_id="ops-dispatch-1", email="dispatch@lmxit.com", name="Dispatch", role="dispatcher"
)


async def _assign(db_session, order_id, driver_id):
    return await assign_order(
        order_id, AssignOrderBody(driver_id=driver_id), session=db_session, ops=DISPATCHER
    )


async def _refused(db_session, order_id, driver_id) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        await _assign(db_session, order_id, driver_id)
    return refused.value


async def _idle(db_session):
    """An available, compliant driver and a held order at their hub."""
    hub_id, client_id, shop_id, driver_id, order = await driver_app._seed(db_session)
    await make_driver_compliant(db_session, driver_id)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    return hub_id, client_id, shop_id, driver_id, order.id, authed


async def _working(db_session):
    """A driver mid-route and a queued order at the same hub, in the hold queue."""
    hub_id, client_id, _shop, driver_id, route, current_dropoff = await live._seed_active_route(db_session)
    await make_driver_compliant(db_session, driver_id)
    order, new_shop_id = await live._seed_new_order(db_session, hub_id, client_id)
    await db_session.commit()
    now = datetime.now(timezone.utc)
    await HoldQueueStore().add(
        str(hub_id),
        HeldOrder(
            order_id=str(order.id), shop_lat=34.06, shop_lng=-118.26, sla_tier="T2",
            hold_deadline=order.hold_deadline, held_since=now, shop_name="New Shop",
        ),
    )
    return hub_id, client_id, driver_id, route.id, current_dropoff.id, order.id, new_shop_id


async def _held_ids(hub_id) -> set[str]:
    return {h.order_id for h in await HoldQueueStore().get_all(str(hub_id))}


async def _set_state(hub_id, driver_id, **changes):
    manager = FleetStateManager()
    state = await manager.get_driver_state(str(hub_id), str(driver_id))
    await manager.upsert_driver_state(state.model_copy(update=changes))


class TestAnIdleDriverIsOffered:
    async def test_the_offer_is_one_the_driver_sees(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, authed = await _idle(db_session)

        result = await _assign(db_session, order_id, driver_id)

        assert result.offer_id
        [offer] = await list_my_offers(driver=authed, session=db_session)
        assert offer.offer_id == result.offer_id
        assert [s.order_id for s in offer.stops] == [str(order_id)]

        db_session.expire_all()
        row = await db_session.get(RouteOffer, uuid.UUID(result.offer_id))
        assert row.status == "offered"
        # One order has no sequence to plan: accept builds it from the order.
        assert row.visit_payload is None
        OfferStopSummary.model_validate(row.stop_payload[0])
        ttl = (row.expires_at - row.offered_at).total_seconds()
        assert ttl == pytest.approx(settings.job_offer_ttl_seconds, abs=1)

    async def test_the_order_is_assigned_and_out_of_the_hold_queue(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)

        await _assign(db_session, order_id, driver_id)

        db_session.expire_all()
        order = await db_session.get(Order, order_id)
        assert order.status == OrderStatus.assigned
        assert order.assigned_at is not None
        assert str(order_id) not in await _held_ids(hub_id)
        # Out of the pool, so the next cycle can't offer them a second job.
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "offered"

    async def test_accepting_it_builds_the_route(self, db_session, real_redis_client):
        _hub, _client, _shop, _driver, order_id, authed = await _idle(db_session)
        result = await _assign(db_session, order_id, uuid.UUID(authed.driver_id))

        route = await accept_offer(result.offer_id, driver=authed, session=db_session)

        assert [s.stop_type for s in route.stops] == ["pickup", "dropoff"]
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.en_route_pickup

    async def test_declining_it_puts_the_order_back(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, authed = await _idle(db_session)
        result = await _assign(db_session, order_id, driver_id)

        await decline_offer(result.offer_id, driver=authed, session=db_session)

        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held
        assert str(order_id) in await _held_ids(hub_id)
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "available"

    async def test_a_driver_whose_app_has_gone_quiet_is_flagged_not_refused(
        self, db_session, real_redis_client
    ):
        """The dispatcher may have phoned them. An offer nobody answers lapses."""
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await real_redis_client.delete(f"fleet:{hub_id}:driver:{driver_id}:location")

        result = await _assign(db_session, order_id, driver_id)

        assert result.driver_position_stale is True


class TestTheRecord:
    async def test_an_offer_names_who_made_it(self, db_session, real_redis_client):
        _hub, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)

        result = await _assign(db_session, order_id, driver_id)

        [row] = (
            await db_session.execute(select(DispatchAssignment).where(DispatchAssignment.order_id == order_id))
        ).scalars().all()
        assert str(row.offer_id) == result.offer_id
        assert (row.driver_id, row.ops_user_id, row.ops_user_email) == (
            driver_id, DISPATCHER.ops_user_id, DISPATCHER.email,
        )

    async def test_the_record_cannot_be_edited(self, db_session, real_redis_client):
        _hub, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await _assign(db_session, order_id, driver_id)

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(text("UPDATE dispatch_assignments SET ops_user_email = 'someone@else'"))
        await db_session.rollback()

    async def test_the_order_lookup_says_who_assigned_it(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await _assign(db_session, order_id, driver_id)

        page = await lookup_orders(hub_id=hub_id, q="ORD-DRIVER-APP", session=db_session, _ops=DISPATCHER)

        [row] = page.items
        assert row.assigned_by_dispatch == DISPATCHER.email
        assert row.assigned_by_dispatch_at is not None


class TestWhatIsRefused:
    async def test_an_unknown_order(self, db_session, real_redis_client):
        _hub, _client, _shop, driver_id, _order, _authed = await _idle(db_session)
        assert (await _refused(db_session, uuid.uuid4(), driver_id)).status_code == 404

    async def test_an_unknown_driver(self, db_session, real_redis_client):
        _hub, _client, _shop, _driver, order_id, _authed = await _idle(db_session)
        assert (await _refused(db_session, order_id, uuid.uuid4())).status_code == 404

    async def test_another_hubs_driver(self, db_session, real_redis_client):
        _hub, _client, _shop, _driver, order_id, _authed = await _idle(db_session)
        other_hub = Hub(id=uuid.uuid4(), name="Other Hub", lat=34.0, lng=-118.0)
        db_session.add(other_hub)
        await db_session.commit()
        stranger = Driver(hub_id=other_hub.id, name="Elsewhere E.", phone="+15555550911", vehicle_capacity_units=5)
        db_session.add(stranger)
        await db_session.commit()

        refused = await _refused(db_session, order_id, stranger.id)

        assert refused.status_code == 404

    @pytest.mark.parametrize(
        ("status", "says"),
        [
            (OrderStatus.assigned, "already has"),
            (OrderStatus.delivered, "delivered"),
            (OrderStatus.delivery_failed, "Resolve"),
            (OrderStatus.received, "hold queue"),
        ],
    )
    async def test_an_order_not_waiting_for_a_driver(self, db_session, real_redis_client, status, says):
        _hub, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        order = await db_session.get(Order, order_id)
        order.status = status
        if status == OrderStatus.delivered:
            order.delivered_at = datetime.now(timezone.utc)
        await db_session.commit()

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 409
        assert says in refused.detail

    async def test_a_second_assign_of_the_same_order(self, db_session, real_redis_client):
        _hub, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await _assign(db_session, order_id, driver_id)

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 409

    async def test_a_driver_without_documents_names_every_problem(self, db_session, real_redis_client):
        hub_id, client_id, shop_id, driver_id, order = await driver_app._seed(db_session)

        refused = await _refused(db_session, order.id, driver_id)

        assert refused.status_code == 409
        for doc_type in REQUIRED_DOC_TYPES:
            assert doc_type in refused.detail

    async def test_a_driver_whose_documents_expired(self, db_session, real_redis_client):
        hub_id, client_id, shop_id, driver_id, order = await driver_app._seed(db_session)
        await make_driver_compliant(db_session, driver_id, expires_in_days=-1)

        refused = await _refused(db_session, order.id, driver_id)

        assert refused.status_code == 409
        assert "expired" in refused.detail

    async def test_a_closed_hub(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        hub = await db_session.get(Hub, hub_id)
        db_session.add(HubClosure(hub_id=hub_id, closure_date=hub_local_date(hub, datetime.now(timezone.utc))))
        await db_session.commit()

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 409
        assert "closed" in refused.detail

    async def test_a_driver_with_no_live_state(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await real_redis_client.delete(f"fleet:{hub_id}:driver:{driver_id}:state")

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 409
        assert "not on shift" in refused.detail

    async def test_a_full_van(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await _set_state(hub_id, driver_id, load_units=5.0)

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 409
        assert "No room" in refused.detail

    @pytest.mark.parametrize("status", ["on_break", "offered", "off_shift"])
    async def test_a_driver_who_cannot_take_work(self, db_session, real_redis_client, status):
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await _set_state(hub_id, driver_id, status=status)

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 409
        assert "can't take work" in refused.detail
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held

    async def test_a_driver_on_a_route(self, db_session, real_redis_client):
        """Adding to a working driver's route by hand is `CON-5`'s second half,
        not built. Refused, saying so, and nothing moves."""
        hub_id, _client, driver_id, route_id, _current, order_id, _shop = await _working(db_session)

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 409
        assert "on a route" in refused.detail
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.queued
        assert str(order_id) in await _held_ids(hub_id)
        stops = (await db_session.execute(select(Stop.id).where(Stop.route_id == route_id))).all()
        assert len(stops) == 2

    async def test_a_switched_off_driver(self, db_session, real_redis_client):
        _hub, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        driver = await db_session.get(Driver, driver_id)
        driver.is_active = False
        await db_session.commit()

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 409
        assert "switched off" in refused.detail

    async def test_an_order_with_no_shop(self, db_session, real_redis_client):
        _hub, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        order = await db_session.get(Order, order_id)
        order.shop_id = None
        await db_session.commit()

        assert (await _refused(db_session, order_id, driver_id)).status_code == 422

    async def test_a_refusal_leaves_nothing_behind(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await _set_state(hub_id, driver_id, status="on_break")

        await _refused(db_session, order_id, driver_id)

        assert str(order_id) in await _held_ids(hub_id)
        assert (await db_session.scalar(select(RouteOffer.id).limit(1))) is None
        assert (await db_session.scalar(select(DispatchAssignment.id).limit(1))) is None


class TestTheGuard:
    async def test_it_is_a_dispatchers_write(self):
        import inspect

        from app.ops_auth.dependencies import require_dispatcher

        dependency = inspect.signature(assign_order).parameters["ops"].default
        assert dependency.dependency is require_dispatcher


class TestPuttingItBackInTheQueue:
    """The console's "Put back in the queue" is the hold override, sent from the
    exceptions row. Nothing in the console sent `hold` before."""

    async def test_a_released_order_goes_back_under_the_queues_rules(self, db_session, real_redis_client):
        hub_id, _client, driver_id, _route, _current, order_id, _shop = await _working(db_session)

        view = await override_order(
            order_id=order_id,
            body=OverrideRequest(action="hold", reason_code="hub_constraint"),
            session=db_session,
            ops=DISPATCHER,
        )

        assert (view.order_status_before, view.order_status_after) == ("queued", "held")
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held
        # Still in the hold queue: the next cycle judges it by the queue's rules.
        assert str(order_id) in await _held_ids(hub_id)

    async def test_and_leaves_the_exceptions_list_while_its_hold_runs(self, db_session, real_redis_client):
        hub_id, _client, _driver, _route, _current, order_id, _shop = await _working(db_session)
        order = await db_session.get(Order, order_id)
        order.requested_at = datetime.now(timezone.utc) - timedelta(hours=2)
        await db_session.commit()
        before = await build_exception_queue(db_session, hub_id=hub_id)
        assert [i.kind for i in before.items if i.order_id == order_id] == [KIND_UNPLACED]

        await override_order(
            order_id=order_id,
            body=OverrideRequest(action="hold", reason_code="hub_constraint"),
            session=db_session,
            ops=DISPATCHER,
        )

        after = await build_exception_queue(db_session, hub_id=hub_id)
        assert all(i.order_id != order_id for i in after.items)


class _TakesTheFirstOrderMidCycle(RouteOptimizationClient):
    """Plans every stop onto one driver, and while it does, the first order is
    assigned elsewhere - what dispatch assigning it by hand during a cycle
    looks like from inside the cycle."""

    engine_name = "spy_race"

    def __init__(self, driver_id: str, taken: uuid.UUID) -> None:
        self.driver_id = driver_id
        self.taken = taken

    async def optimize(self, drivers, stops):
        from app.db import session_scope

        async with session_scope() as session:
            order = await session.get(Order, self.taken)
            order.status = OrderStatus.assigned
            await session.commit()
        visits = [RouteVisit(order_id=s.stop_id, kind="pickup") for s in stops]
        visits += [RouteVisit(order_id=s.stop_id, kind="delivery") for s in stops]
        return [RouteAssignment(driver_id=self.driver_id, visits=visits)], []


class TestTheCycleOffersOnlyWhatItMoved:
    async def test_an_order_taken_mid_cycle_is_not_on_the_offer(self, db_session, real_redis_client):
        hub_id, client_id, shop_id, driver_id, first = await driver_app._seed(db_session)
        second = await _a_second_held_order(db_session, hub_id, client_id, shop_id)
        await let_the_hold_run_out(hub_id)
        first_id, second_id = first.id, second.id

        await DispatchOptimizerService(
            route_client=_TakesTheFirstOrderMidCycle(str(driver_id), first_id)
        ).run_cycle(str(hub_id))

        [offer] = (await db_session.execute(select(RouteOffer))).scalars().all()
        assert [s["order_id"] for s in offer.stop_payload] == [str(second_id)]

    async def test_a_cycle_left_with_nothing_offers_nothing(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order = await driver_app._seed(db_session)
        await let_the_hold_run_out(hub_id)

        await DispatchOptimizerService(
            route_client=_TakesTheFirstOrderMidCycle(str(driver_id), order.id)
        ).run_cycle(str(hub_id))

        assert (await db_session.scalar(select(RouteOffer.id).limit(1))) is None
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "available"


async def _a_second_held_order(db_session, hub_id, client_id, shop_id) -> Order:
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref="ORD-ASSIGN-2", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now + timedelta(minutes=30), weight_units=1,
        status=OrderStatus.held, requested_at=now,
        delivery_address="16 Oak Ave", delivery_lat=34.0535, delivery_lng=-118.2535,
    )
    db_session.add(order)
    await db_session.commit()
    await HoldQueueStore().add(
        str(hub_id),
        HeldOrder(
            order_id=str(order.id), shop_lat=34.051, shop_lng=-118.251, sla_tier="T2",
            hold_deadline=order.hold_deadline, held_since=now, shop_name="Midtown Auto Parts",
        ),
    )
    return order


class TestHeldOrdersNobodyCouldTake:
    """The second way into "nobody has it": the hold ran out, the cycle released
    it, and nobody could take it. It stays `held`, so it never showed."""

    async def test_a_held_order_past_its_deadline_is_listed(self, db_session, real_redis_client):
        hub_id, _client, _shop, _driver, order_id, _authed = await _idle(db_session)
        order = await db_session.get(Order, order_id)
        order.hold_deadline = datetime.now(timezone.utc) - timedelta(seconds=settings.stuck_order_after_seconds + 60)
        await db_session.commit()

        queue = await build_exception_queue(db_session, hub_id=hub_id)

        [item] = queue.items
        assert item.kind == KIND_UNPLACED
        assert "no driver has it" in item.detail
        assert "nobody has this order" in item.next_action
        assert item.status == "held"
        assert item.hold_deadline is not None


# ---------------------------------------------------------------------------
# Two writers, one order or one driver
# ---------------------------------------------------------------------------
#
# Dispatch now assigns orders and offers drivers alongside the cycle, and doesn't
# take the hub's cycle lock, so each race below is one the cycle can run into in
# the seconds a solve takes.


class TestTheStatusWriteSeesADispatcher:
    async def test_it_waits_for_the_dispatcher_and_then_moves_nothing(self, db_session, real_redis_client):
        """A dispatcher assigning by hand holds the order row until they commit.
        The cycle's status write read the row, saw `held`, queued its UPDATE behind
        the dispatcher's, and then reported the order moved - and offered it to a
        second driver. Written only from held or queued, as the cycle and its
        insertion now write it, the queued UPDATE re-checks once the dispatcher commits,
        matches nothing, and moves nothing."""
        from app.db import session_scope
        from app.orders.status_service import advance_orders

        _hub, _client, _shop, _driver, order_id, _authed = await _idle(db_session)

        async def the_cycles_write():
            async with session_scope() as cycle:
                return [
                    o.id
                    for o in await advance_orders(
                        cycle, [order_id], OrderStatus.assigned, only_from=_PLANNABLE
                    )
                ]

        async with session_scope() as dispatcher:
            row = (
                await dispatcher.execute(select(Order).where(Order.id == order_id).with_for_update())
            ).scalar_one()
            row.status = OrderStatus.assigned
            await dispatcher.flush()
            cycle = asyncio.create_task(the_cycles_write())
            await asyncio.sleep(0.3)
            assert not cycle.done(), "the cycle's write should be waiting on the dispatcher's lock"
            await dispatcher.commit()

        assert await cycle == []


class TestTheStatusWriteStillTakesARelease:
    async def test_a_released_order_moves(self, db_session, real_redis_client):
        """The condition is the cycle's own - held or queued - not the status it
        happened to read. A dispatcher's release landing between the cycle's
        read and its write leaves an order the cycle may place; matched against
        the read, the write would skip it, and the cycle has already taken it out
        of the hold queue, so nobody would place it."""
        from app.db import session_scope
        from app.orders.status_service import advance_orders

        _hub, _client, _shop, _driver, order_id, _authed = await _idle(db_session)
        order = await db_session.get(Order, order_id)
        order.status = OrderStatus.queued
        await db_session.commit()

        async with session_scope() as session:
            moved = await advance_orders(
                session, [order_id], OrderStatus.assigned, only_from=_PLANNABLE
            )

        assert [o.id for o in moved] == [order_id]


class TestInsertionLeavesATakenOrderAlone:
    async def test_an_order_dispatch_assigned_mid_cycle_is_not_put_on_a_route(
        self, db_session, real_redis_client
    ):
        """The cycle read the order from the hold queue before a dispatcher gave it
        to somebody by hand. Insertion used to append it to a passing driver's
        route regardless: one order, two drivers."""
        hub_id, client_id, *_ = await live._seed_active_route(db_session)
        order, _ = await live._seed_new_order(db_session, hub_id, client_id)
        order.status = OrderStatus.assigned
        await db_session.commit()
        order_id = order.id

        inserted = await live._insert(hub_id, order)

        assert inserted == set()
        stops = (
            await db_session.execute(select(StopOrder.stop_id).where(StopOrder.order_id == order_id))
        ).all()
        assert stops == []


class _DriverOfferedMidCycle(RouteOptimizationClient):
    """Plans every stop onto one driver, and while it does, dispatch offers that
    driver something by hand - which, seen from the fleet state, is the driver
    turning `offered`."""

    engine_name = "spy_driver_race"

    def __init__(self, hub_id: str, driver_id: str) -> None:
        self.hub_id = hub_id
        self.driver_id = driver_id

    async def optimize(self, drivers, stops):
        await _set_state(self.hub_id, self.driver_id, status="offered")
        visits = [RouteVisit(order_id=s.stop_id, kind="pickup") for s in stops]
        visits += [RouteVisit(order_id=s.stop_id, kind="delivery") for s in stops]
        return [RouteAssignment(driver_id=self.driver_id, visits=visits)], []


class TestTheCycleDoesNotOfferATakenDriver:
    async def test_the_driver_gets_no_second_offer_and_the_order_goes_back(
        self, db_session, real_redis_client
    ):
        """The solver planned against a driver who was free when the cycle read the
        fleet. By the time it wrote the offer, dispatch had offered them something:
        a second open offer, and two routes if they accepted both. The cycle now
        claims the driver at the moment it offers, and puts the order back."""
        hub_id, _client, _shop, driver_id, order = await driver_app._seed(db_session)
        await let_the_hold_run_out(hub_id)
        order_id = order.id

        await DispatchOptimizerService(
            route_client=_DriverOfferedMidCycle(str(hub_id), str(driver_id))
        ).run_cycle(str(hub_id))

        assert (await db_session.scalar(select(RouteOffer.id).limit(1))) is None
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held
        assert str(order_id) in await _held_ids(hub_id)
        # Still the other offer's: putting the order back must not free them.
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "offered"


class TestALateClaimLeavesNothing:
    async def test_a_driver_claimed_between_the_checks_and_the_offer(
        self, db_session, real_redis_client, monkeypatch
    ):
        """The checks passed, the order moved to `assigned`, the offer and the
        record were written - and then the claim found the driver taken. All of
        it has to roll back: an `assigned` order with nobody, or a record naming
        a driver who never got it, is the state this exists to prevent."""
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)

        async def taken(self, hub_id, driver_id):
            return None

        monkeypatch.setattr(FleetStateManager, "claim_for_offer", taken)

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 409
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held
        assert str(order_id) in await _held_ids(hub_id)
        assert (await db_session.scalar(select(RouteOffer.id).limit(1))) is None
        assert (await db_session.scalar(select(DispatchAssignment.id).limit(1))) is None


class TestTheLookupOnlyNamesAnAssignThatStands:
    async def test_not_after_the_driver_declined(self, db_session, real_redis_client):
        """The record is append-only, so a declined offer leaves its row. Naming
        the dispatcher then would credit them with a driver the optimizer chose
        afterwards, or with an order nobody has."""
        hub_id, _client, _shop, driver_id, order_id, authed = await _idle(db_session)
        result = await _assign(db_session, order_id, driver_id)
        await decline_offer(result.offer_id, driver=authed, session=db_session)

        page = await lookup_orders(hub_id=hub_id, q="ORD-DRIVER-APP", session=db_session, _ops=DISPATCHER)

        [row] = page.items
        assert row.assigned_by_dispatch is None
        assert row.assigned_by_dispatch_at is None


class TestTheLookupForgetsAnAssignOvertaken:
    async def test_not_once_the_order_was_placed_again(self, db_session, real_redis_client):
        """The dispatcher's offer was accepted, and the order later redelivered
        and placed again by the optimizer. The offer still reads accepted; the
        order's newer `assigned_at` says the assign no longer stands."""
        hub_id, _client, _shop, driver_id, order_id, authed = await _idle(db_session)
        result = await _assign(db_session, order_id, driver_id)
        await accept_offer(result.offer_id, driver=authed, session=db_session)
        order = await db_session.get(Order, order_id)
        order.assigned_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        await db_session.commit()

        page = await lookup_orders(hub_id=hub_id, q="ORD-DRIVER-APP", session=db_session, _ops=DISPATCHER)

        assert page.items[0].assigned_by_dispatch is None


class TestTheRequeueOnlyTakesBackWhatIsStillAssigned:
    async def test_a_cancelled_order_stays_cancelled(self, db_session, real_redis_client):
        """An offer's orders go back to the queue when the driver declines, the
        offer lapses, or the cycle finds the driver taken. One dispatch cancelled
        in between is not the offer's to send back: the requeue set `held`
        whatever the status, and the next cycle dispatched a cancelled order."""
        from app.orders.requeue import requeue_orders_from_offer

        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        order = await db_session.get(Order, order_id)
        order.status = OrderStatus.cancelled
        await db_session.commit()
        await HoldQueueStore().remove(str(hub_id), str(order_id))

        await requeue_orders_from_offer(
            db_session,
            str(hub_id),
            str(driver_id),
            [{"order_id": str(order_id), "lat": 34.051, "lng": -118.251, "sla_tier": "T2", "shop_name": "x"}],
            free_driver=False,
        )
        await db_session.commit()

        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled
        assert str(order_id) not in await _held_ids(hub_id)


class TestAnOverrideCannotUndoAnAssign:
    async def test_release_waits_and_then_refuses(self, db_session, real_redis_client):
        """A dispatcher releases an order while another assigns it. The release
        read `held`, queued behind the assign, and then wrote `queued` over
        `assigned` - an order a driver now had, back in the queue. It now
        re-checks once the assign commits, and refuses."""
        from app.db import session_scope
        from app.record.overrides import OverrideRefused, apply_override

        _hub, _client, _shop, _driver, order_id, _authed = await _idle(db_session)

        async def the_release():
            async with session_scope() as session:
                await apply_override(
                    session,
                    order_id=order_id,
                    action="release",
                    reason_code="customer_called",
                    ops_user_id="ops-2",
                    ops_user_email="other@lmxit.com",
                )

        async with session_scope() as assigner:
            row = (
                await assigner.execute(select(Order).where(Order.id == order_id).with_for_update())
            ).scalar_one()
            row.status = OrderStatus.assigned
            await assigner.flush()
            release = asyncio.create_task(the_release())
            await asyncio.sleep(0.3)
            await assigner.commit()

        with pytest.raises(OverrideRefused):
            await release
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.assigned


class TestACompareAndSetWriteStillDatesTheOrder:
    async def test_updated_at_moves(self, db_session, real_redis_client):
        """Tracking-link expiry reads `updated_at` for a cancelled order
        (app/tracking/service.py). The conditional write goes round the unit
        of work, so the column's onupdate has to fire on it all the same."""
        from app.db import session_scope
        from app.orders.status_service import advance_orders

        _hub, _client, _shop, _driver, order_id, _authed = await _idle(db_session)
        before = (await db_session.get(Order, order_id)).updated_at
        await asyncio.sleep(0.01)

        async with session_scope() as session:
            [moved] = await advance_orders(
                session, [order_id], OrderStatus.assigned, only_from=_PLANNABLE
            )
            in_session = moved.updated_at
            await session.commit()

        db_session.expire_all()
        after = (await db_session.get(Order, order_id)).updated_at
        assert after > before
        assert in_session == after


class TestOffersThatNeverCommit:
    async def test_the_drivers_claimed_are_given_back(self, db_session, real_redis_client, monkeypatch):
        """The cycle claims each driver as it offers. If the offers then fail, a
        driver left `offered` with no offer to lapse is out of every plan until
        something resets them. The orders the status write moved are not sent
        back from here (that would re-run the cycle into the same failure every
        second); the scheduled sweep does (`CON-5`,
        tests/integration/test_dispatch_cycle_write_safety.py)."""
        import app.optimizer.service as service_module

        hub_id, _client, _shop, driver_id, _order = await driver_app._seed(db_session)
        await let_the_hold_run_out(hub_id)

        def no_offers(**_kwargs):
            raise RuntimeError("the offer insert failed")

        monkeypatch.setattr(service_module, "RouteOffer", no_offers)

        with pytest.raises(RuntimeError):
            await DispatchOptimizerService().run_cycle(str(hub_id))

        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "available"


# ---------------------------------------------------------------------------
# Review of #216: history, the hub lock, the claim, and one requeue per offer
# ---------------------------------------------------------------------------


def _past_the_grace() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=settings.stuck_order_after_seconds + 60)


async def _lapse(db_session, offer_id: str) -> None:
    offer = await db_session.get(RouteOffer, uuid.UUID(offer_id))
    offer.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()


class TestHistoryIsNeverDispatched:
    """A backfill loads history as `held` with a deadline long past, on purpose
    and never queued. The exceptions queue listed every imported order as
    "nobody has it", with an Assign button that sent a driver to collect a
    delivery that had already happened."""

    async def test_a_backfilled_order_is_not_listed(self, db_session, real_redis_client):
        hub_id, _client, _shop, _driver, order_id, _authed = await _idle(db_session)
        order = await db_session.get(Order, order_id)
        order.hold_deadline = _past_the_grace()
        order.intake_mode = "backfill"
        await db_session.commit()

        queue = await build_exception_queue(db_session, hub_id=hub_id)

        assert all(i.order_id != order_id for i in queue.items)

    async def test_assigning_one_is_refused(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        order = await db_session.get(Order, order_id)
        order.intake_mode = "backfill"
        await db_session.commit()

        refused = await _refused(db_session, order_id, driver_id)

        assert refused.status_code == 422
        assert "history" in refused.detail
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held
        assert (await db_session.scalar(select(RouteOffer.id).limit(1))) is None
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "available"

    async def test_a_requeue_never_puts_one_in_the_hold_queue(self, db_session, real_redis_client):
        from app.orders.requeue import requeue_orders_from_offer

        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        await HoldQueueStore().remove(str(hub_id), str(order_id))
        order = await db_session.get(Order, order_id)
        order.intake_mode = "backfill"
        order.status = OrderStatus.assigned
        await db_session.commit()

        await requeue_orders_from_offer(
            db_session,
            str(hub_id),
            str(driver_id),
            [{"order_id": str(order_id), "lat": 34.051, "lng": -118.251, "sla_tier": "T2", "shop_name": "x"}],
        )
        await db_session.commit()

        assert str(order_id) not in await _held_ids(hub_id)


class TestTheAssignTakesTheHub:
    """The rebuild reads the open offers once, then corrects each `offered`
    driver it finds with none. An assign committed in between had its driver
    put back in the pool, and the sweep's next cycle offered them a second job.
    The assign now holds the hub lock every cycle and the rebuild take."""

    async def test_it_waits_then_refuses_while_dispatch_runs(self, db_session, real_redis_client, monkeypatch):
        import app.orders.assignment as assignment_module
        from app.events.bus import release_hub_lock, try_acquire_hub_lock

        monkeypatch.setattr(assignment_module, "HUB_LOCK_WAIT_SECONDS", 0.3)
        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        token = await try_acquire_hub_lock(str(hub_id))
        assert token is not None
        try:
            refused = await _refused(db_session, order_id, driver_id)
        finally:
            await release_hub_lock(str(hub_id), token)

        assert refused.status_code == 409
        assert "Dispatch is running" in refused.detail
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "available"

    async def test_it_goes_through_once_dispatch_lets_go(self, db_session, real_redis_client):
        from app.events.bus import release_hub_lock, try_acquire_hub_lock

        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        token = await try_acquire_hub_lock(str(hub_id))

        async def let_go():
            await asyncio.sleep(0.3)
            await release_hub_lock(str(hub_id), token)

        releasing = asyncio.create_task(let_go())
        result = await _assign(db_session, order_id, driver_id)
        await releasing

        assert result.offer_id
        # And the assign let go of it in turn, so the cycle its event wakes can run.
        again = await try_acquire_hub_lock(str(hub_id))
        assert again is not None
        await release_hub_lock(str(hub_id), again)

    async def test_the_rebuild_cannot_free_a_driver_mid_assign(self, db_session, real_redis_client):
        """The rebuild under the hub lock finds the assign holding it and skips
        the hub, rather than reading the offers before the assign's commit."""
        from app.events.bus import hub_lock
        from app.optimizer.redis_rebuild import reconcile_redis_state

        hub_id, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        async with hub_lock(str(hub_id)) as held:
            assert held
            rebuilt = await reconcile_redis_state()
        assert rebuilt["hubs"][str(hub_id)] == "skipped: a cycle is running"


class TestTheClaimIsOneStep:
    async def test_a_claim_takes_an_available_driver_once(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, _order_id, _authed = await _idle(db_session)
        manager = FleetStateManager()

        first = await manager.claim_for_offer(str(hub_id), str(driver_id))
        second = await manager.claim_for_offer(str(hub_id), str(driver_id))

        assert first is not None and second is None
        before, after = first
        assert (before.status, after.status) == ("available", "offered")
        assert before.capacity_units == after.capacity_units == 5
        assert str(driver_id) not in await manager.get_available_driver_ids(str(hub_id))
        assert (await manager.get_driver_state(str(hub_id), str(driver_id))).status == "offered"

    async def test_a_driver_on_break_is_not_claimed(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, _order_id, _authed = await _idle(db_session)
        await _set_state(hub_id, driver_id, status="on_break")

        assert await FleetStateManager().claim_for_offer(str(hub_id), str(driver_id)) is None
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "on_break"

    async def test_giving_a_claim_back_does_not_undo_a_break(self, db_session, real_redis_client):
        """The claim's commit failed, and in the meantime the driver went on
        break. Writing the claim's `before` back put them in the pool."""
        hub_id, _client, _shop, driver_id, _order_id, _authed = await _idle(db_session)
        manager = FleetStateManager()
        before, _ = await manager.claim_for_offer(str(hub_id), str(driver_id))
        await _set_state(hub_id, driver_id, status="on_break")

        assert await manager.give_back_claim(before) is False

        assert (await manager.get_driver_state(str(hub_id), str(driver_id))).status == "on_break"
        assert str(driver_id) not in await manager.get_available_driver_ids(str(hub_id))

    async def test_a_decline_does_not_undo_a_break(self, db_session, real_redis_client):
        hub_id, _client, _shop, driver_id, order_id, authed = await _idle(db_session)
        result = await _assign(db_session, order_id, driver_id)
        await _set_state(hub_id, driver_id, status="on_break")

        await decline_offer(result.offer_id, driver=authed, session=db_session)

        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "on_break"
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held


class TestAnOfferIsSentBackOnce:
    """The sweep, the driver's offer list and cancel each read open offers
    without a lock. A stale copy processed after the offer was already settled
    sent its orders back a second time - by then possibly given to somebody
    else - and freed a driver who had been offered other work."""

    async def test_a_stale_copy_of_a_lapsed_offer_sends_nothing_back(self, db_session, real_redis_client):
        from app.api.driver_routes import _expire_if_lapsed
        from app.db import session_scope

        hub_id, _client, _shop, driver_id, order_id, authed = await _idle(db_session)
        result = await _assign(db_session, order_id, driver_id)
        await _lapse(db_session, result.offer_id)

        async with session_scope() as sweep:
            stale = await sweep.get(RouteOffer, uuid.UUID(result.offer_id))
            assert stale.status == "offered"

            # The driver's app expires it first: the order goes back, the driver
            # is free - and then both are taken again by somebody else.
            assert await list_my_offers(driver=authed, session=db_session) == []
            order = await db_session.get(Order, order_id)
            order.status = OrderStatus.assigned
            await db_session.commit()
            await HoldQueueStore().remove(str(hub_id), str(order_id))
            await _set_state(hub_id, driver_id, status="offered")

            await _expire_if_lapsed(sweep, stale)
            await sweep.commit()

        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.assigned
        assert str(order_id) not in await _held_ids(hub_id)
        state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
        assert state.status == "offered"

    async def test_the_sweep_skips_an_offer_being_answered(self, db_session, real_redis_client):
        from app.api.driver_routes import expire_lapsed_offers
        from app.db import session_scope

        _hub, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        result = await _assign(db_session, order_id, driver_id)
        await _lapse(db_session, result.offer_id)

        async with session_scope() as answering:
            await answering.execute(
                select(RouteOffer).where(RouteOffer.id == uuid.UUID(result.offer_id)).with_for_update()
            )
            async with session_scope() as sweep:
                assert await asyncio.wait_for(expire_lapsed_offers(sweep), timeout=5) == 0
                await sweep.commit()
            await answering.rollback()

        assert await expire_lapsed_offers(db_session) == 1
        await db_session.commit()
        db_session.expire_all()
        assert (await db_session.get(Order, order_id)).status == OrderStatus.held

    async def test_cancel_waits_for_an_answer_and_does_not_overwrite_it(self, db_session, real_redis_client):
        """A cancel read the offer as open while the driver was declining it. It
        marked the declined offer `withdrawn`, and its requeue ran a second time."""
        from app.db import session_scope
        from app.orders.cancellation import STOPS_REMOVED, cancel_live_order

        _hub, _client, _shop, driver_id, order_id, _authed = await _idle(db_session)
        result = await _assign(db_session, order_id, driver_id)
        offer_id = uuid.UUID(result.offer_id)

        async def the_cancel():
            async with session_scope() as session:
                order = await session.get(Order, order_id)
                return await cancel_live_order(session, HoldQueueStore(), order)

        async with session_scope() as declining:
            offer = (
                await declining.execute(select(RouteOffer).where(RouteOffer.id == offer_id).with_for_update())
            ).scalar_one()
            offer.status = "declined"
            await declining.flush()
            cancel = asyncio.create_task(the_cancel())
            await asyncio.sleep(0.3)
            assert not cancel.done()
            await declining.commit()

        assert await cancel == STOPS_REMOVED
        db_session.expire_all()
        assert (await db_session.get(RouteOffer, offer_id)).status == "declined"
        assert (await db_session.get(Order, order_id)).status == OrderStatus.cancelled
