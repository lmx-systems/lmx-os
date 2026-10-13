"""The playbook's rule for orders nobody has, against Postgres and Redis (`DEC-10`).

It recommends a free driver - every check a person's assign makes - or hands
the order to a person, once per spell of being unplaced; a person's assign of
the same driver agrees with it, anything else overrules it.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.api.admin_routes import assign_order, cancel_order_as_dispatch
from app.api.driver_routes import accept_offer, decline_offer, get_my_earnings, list_my_offers
from app.api.routes import override_order
from app.config import settings
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.models.driver import Driver
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.stop import Stop, StopOrder
from app.models.playbook_decision import PlaybookDecision
from app.ops_auth.dependencies import AuthedOpsUser
from app.orders.status_service import advance_orders
from app.playbook.report import build_playbook_report
from app.playbook.rules import RULE_UNPLACED
from app.playbook.sweep import run_playbook
from app.reporting.exceptions import build_exception_queue
from app.schemas.admin import AssignOrderBody
from app.schemas.fleet import DriverLocation
from app.schemas.reporting import OverrideRequest
from tests.integration import test_driver_app_integration as driver_app
from tests.integration.conftest import make_driver_compliant

pytestmark = pytest.mark.integration

DISPATCHER = AuthedOpsUser(ops_user_id="ops-dispatch-1", email="dispatch@example.com", name="Dispatch", role="dispatcher")


async def _unplaced(db_session, *, compliant=True):
    """An idle driver and an order whose hold ran out long enough ago to count as unplaced."""
    hub_id, _client, _shop, driver_id, order = await driver_app._seed(db_session)
    order_id = order.id
    if compliant:
        await make_driver_compliant(db_session, driver_id)
    row = await db_session.get(Order, order_id)
    row.hold_deadline = datetime.now(timezone.utc) - timedelta(seconds=settings.stuck_order_after_seconds + 60)
    await db_session.commit()
    return hub_id, driver_id, order_id


async def _calls(db_session, order_id) -> list[PlaybookDecision]:
    db_session.expire_all()
    return list(
        await db_session.scalars(
            select(PlaybookDecision)
            .where(PlaybookDecision.rule == RULE_UNPLACED, PlaybookDecision.order_id == order_id)
            .order_by(PlaybookDecision.attempt)
        )
    )


async def _set_state(hub_id, driver_id, **changes):
    manager = FleetStateManager()
    state = await manager.get_driver_state(str(hub_id), str(driver_id))
    await manager.upsert_driver_state(state.model_copy(update=changes))


async def test_a_free_driver_is_recommended_once(db_session, real_redis_client):
    hub_id, driver_id, order_id = await _unplaced(db_session)

    sweep = await run_playbook(db_session, hub_id)

    assert sweep.called == 1
    [call] = await _calls(db_session, order_id)
    assert (call.action, call.target, call.outcome, call.mode) == ("offer", str(driver_id), "open", "recommend")
    assert call.reason.startswith("Jordan P. is free")
    queue = await build_exception_queue(db_session, hub_id=hub_id)
    [item] = [i for i in queue.items if i.order_id == order_id]
    assert (item.playbook.rule, item.playbook.action) == ("unplaced", "offer")

    assert (await run_playbook(db_session, hub_id)).called == 0


async def test_a_person_offering_the_same_driver_agrees(db_session, real_redis_client):
    hub_id, driver_id, order_id = await _unplaced(db_session)
    await run_playbook(db_session, hub_id)

    await assign_order(order_id, AssignOrderBody(driver_id=driver_id), session=db_session, ops=DISPATCHER)

    [call] = await _calls(db_session, order_id)
    assert (call.outcome, call.human_action, call.human_target) == ("agreed", "offer", str(driver_id))
    assert call.closed_at is not None
    [rule] = [r for r in (await build_playbook_report(db_session, hub_id=hub_id)).rules if r.rule == RULE_UNPLACED]
    assert (rule.yes, rule.no, rule.acts, rule.next_mode) == (1, 0, False, None)


async def test_a_refused_assign_leaves_the_call_open(db_session, real_redis_client):
    """The close is in the assign's transaction, so a refusal undoes it."""
    hub_id, driver_id, order_id = await _unplaced(db_session)
    await run_playbook(db_session, hub_id)
    await _set_state(hub_id, driver_id, status="on_break")

    with pytest.raises(Exception):
        await assign_order(order_id, AssignOrderBody(driver_id=driver_id), session=db_session, ops=DISPATCHER)
    await db_session.rollback()

    [call] = await _calls(db_session, order_id)
    assert (call.outcome, call.closed_at) == ("open", None)


async def test_releasing_it_overrules(db_session, real_redis_client):
    hub_id, _driver, order_id = await _unplaced(db_session)
    await run_playbook(db_session, hub_id)

    await override_order(
        order_id=order_id,
        body=OverrideRequest(action="release", reason_code="customer_called"),
        session=db_session,
        ops=DISPATCHER,
    )

    [call] = await _calls(db_session, order_id)
    assert (call.outcome, call.human_action, call.human_target) == ("overruled", "release", None)


async def test_nobody_free_is_escalated_once_and_answered_by_a_cancel(db_session, real_redis_client):
    hub_id, driver_id, order_id = await _unplaced(db_session)
    await _set_state(hub_id, driver_id, status="off_shift")

    first = await run_playbook(db_session, hub_id)
    second = await run_playbook(db_session, hub_id)

    assert (first.called, first.escalated, second.called) == (1, 1, 0)
    [call] = await _calls(db_session, order_id)
    assert (call.action, call.outcome, call.closed_at) == ("escalate", "escalated", None)

    await cancel_order_as_dispatch(str(order_id), session=db_session, ops=DISPATCHER)
    [call] = await _calls(db_session, order_id)
    assert (call.outcome, call.human_action) == ("escalated", "cancel")
    assert call.closed_at is not None


async def test_a_driver_without_papers_is_not_free(db_session, real_redis_client):
    hub_id, _driver, order_id = await _unplaced(db_session, compliant=False)

    await run_playbook(db_session, hub_id)

    [call] = await _calls(db_session, order_id)
    assert call.action == "escalate"


async def test_a_quiet_app_is_named_to_phone_first(db_session, real_redis_client):
    hub_id, driver_id, order_id = await _unplaced(db_session)
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    await FleetStateManager().update_driver_location(
        DriverLocation(driver_id=str(driver_id), lat=34.0511, lng=-118.2511, recorded_at=long_ago), str(hub_id)
    )

    await run_playbook(db_session, hub_id)

    [call] = await _calls(db_session, order_id)
    assert call.target == str(driver_id)
    assert "phone them" in call.reason


async def test_placed_some_other_way_the_call_is_superseded_and_a_new_spell_is_a_new_call(
    db_session, real_redis_client
):
    hub_id, _driver, order_id = await _unplaced(db_session)
    await run_playbook(db_session, hub_id)

    # The cycle places it, the driver declines, and it is unplaced again.
    await advance_orders(db_session, [order_id], OrderStatus.assigned, only_from=(OrderStatus.held,))
    await db_session.commit()
    swept = await run_playbook(db_session, hub_id)
    assert swept.superseded == 1
    # Back in the queue as a lapsed offer's requeue writes it (app/orders/requeue.py).
    await db_session.execute(update(Order).where(Order.id == order_id).values(status=OrderStatus.held))
    await db_session.commit()
    await run_playbook(db_session, hub_id)

    calls = await _calls(db_session, order_id)
    assert [(c.attempt, c.outcome) for c in calls] == [(1, "superseded"), (2, "open")]


async def _turn_it_down(db_session, hub_id, driver_id, order_id, *, with_bonus=False):
    """A person offers it, the driver declines, and its hold has long run out again."""
    result = await assign_order(
        order_id, AssignOrderBody(driver_id=driver_id, with_bonus=with_bonus), session=db_session, ops=DISPATCHER
    )
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await decline_offer(result.offer_id, driver=authed, session=db_session)
    row = await db_session.get(Order, order_id, populate_existing=True)
    assert row.status == OrderStatus.held
    row.hold_deadline = datetime.now(timezone.utc) - timedelta(seconds=settings.stuck_order_after_seconds + 60)
    await db_session.commit()
    return result, authed


async def test_turned_down_once_the_next_call_carries_the_bonus(db_session, real_redis_client):
    hub_id, driver_id, order_id = await _unplaced(db_session)
    await _turn_it_down(db_session, hub_id, driver_id, order_id)

    await run_playbook(db_session, hub_id)

    call = (await _calls(db_session, order_id))[-1]
    assert (call.action, call.target, call.outcome) == ("offer_bonus", str(driver_id), "open")
    assert "$5.00 bonus" in call.reason

    # A person follows it: the offer carries the bonus, the driver sees it, the call agrees.
    result = await assign_order(
        order_id, AssignOrderBody(driver_id=driver_id, with_bonus=True), session=db_session, ops=DISPATCHER
    )
    assert result.bonus_cents == settings.offer_bonus_cents == 500
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    [offer] = await list_my_offers(driver=authed, session=db_session)
    assert offer.bonus_cents == 500
    call = (await _calls(db_session, order_id))[-1]
    assert (call.outcome, call.human_action) == ("agreed", "offer_bonus")


async def test_without_the_bonus_a_person_overrules_the_bonus_call(db_session, real_redis_client):
    hub_id, driver_id, order_id = await _unplaced(db_session)
    await _turn_it_down(db_session, hub_id, driver_id, order_id)
    await run_playbook(db_session, hub_id)

    await assign_order(order_id, AssignOrderBody(driver_id=driver_id), session=db_session, ops=DISPATCHER)

    call = (await _calls(db_session, order_id))[-1]
    assert (call.outcome, call.human_action) == ("overruled", "offer")


async def test_turned_down_with_the_bonus_too_goes_to_a_person(db_session, real_redis_client):
    hub_id, driver_id, order_id = await _unplaced(db_session)
    await _turn_it_down(db_session, hub_id, driver_id, order_id, with_bonus=True)

    await run_playbook(db_session, hub_id)

    call = (await _calls(db_session, order_id))[-1]
    assert call.action == "escalate"


async def test_the_bonus_is_owed_once_delivered(db_session, real_redis_client):
    hub_id, driver_id, order_id = await _unplaced(db_session)
    result = await assign_order(
        order_id, AssignOrderBody(driver_id=driver_id, with_bonus=True), session=db_session, ops=DISPATCHER
    )
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await accept_offer(result.offer_id, driver=authed, session=db_session)
    assert (await get_my_earnings(driver=authed, session=db_session)).bonuses_owed_cents == 0

    await _finish_drop_off(db_session, order_id, driver_id, status="completed")

    assert (await get_my_earnings(driver=authed, session=db_session)).bonuses_owed_cents == 500


async def _finish_drop_off(db_session, order_id, driver_id, *, status):
    """The drop-off for this order on the driver's route, marked done or failed."""
    stop_id = await db_session.scalar(
        select(Stop.id)
        .join(StopOrder, StopOrder.stop_id == Stop.id)
        .join(Route, Route.id == Stop.route_id)
        .where(StopOrder.order_id == order_id, Stop.stop_type == "dropoff", Route.driver_id == driver_id)
    )
    now = datetime.now(timezone.utc)
    await db_session.execute(
        update(Stop).where(Stop.id == stop_id).values(status=status, completed_at=now if status == "completed" else None)
    )
    if status == "completed":
        await db_session.execute(
            update(Order).where(Order.id == order_id).values(status=OrderStatus.delivered, delivered_at=now)
        )
    await db_session.commit()


async def test_the_bonus_is_not_owed_when_somebody_else_delivers(db_session, real_redis_client):
    """The drop-off failed on the bonus driver's route and another driver delivered
    the redelivery: the bonus was for a delivery this driver did not make."""
    hub_id, driver_id, order_id = await _unplaced(db_session)
    result = await assign_order(
        order_id, AssignOrderBody(driver_id=driver_id, with_bonus=True), session=db_session, ops=DISPATCHER
    )
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await accept_offer(result.offer_id, driver=authed, session=db_session)
    await _finish_drop_off(db_session, order_id, driver_id, status="failed")

    other = Driver(hub_id=hub_id, name="Other D.", phone=f"+1555{uuid.uuid4().int % 10**7:07d}", vehicle_capacity_units=5)
    db_session.add(other)
    await db_session.flush()
    route = Route(hub_id=hub_id, driver_id=other.id, status="completed")
    db_session.add(route)
    await db_session.flush()
    stop = Stop(route_id=route.id, sequence=1, stop_type="dropoff", status="completed",
                completed_at=datetime.now(timezone.utc), parcel_count=1)
    db_session.add(stop)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order_id))
    await db_session.execute(
        update(Order).where(Order.id == order_id).values(status=OrderStatus.delivered, delivered_at=datetime.now(timezone.utc))
    )
    await db_session.commit()

    assert (await get_my_earnings(driver=authed, session=db_session)).bonuses_owed_cents == 0


async def test_a_decline_between_sweeps_brings_the_call_up_to_date(db_session, real_redis_client):
    """A cycle offers the order and the driver declines before the next sweep, so
    no sweep sees it leave. The call must not go on advising the refused offer."""
    hub_id, driver_id, order_id = await _unplaced(db_session)
    await run_playbook(db_session, hub_id)
    db_session.add(
        RouteOffer(
            hub_id=hub_id, driver_id=driver_id, status="declined",
            stop_payload=[{"order_id": str(order_id), "lat": 34.051, "lng": -118.251, "sla_tier": "T2"}],
            offered_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    sweep = await run_playbook(db_session, hub_id)

    assert (sweep.called, sweep.superseded) == (1, 1)
    calls = await _calls(db_session, order_id)
    assert [(c.attempt, c.action, c.outcome) for c in calls] == [
        (1, "offer", "superseded"),
        (2, "offer_bonus", "open"),
    ]
    # And unchanged, it is left alone.
    assert (await run_playbook(db_session, hub_id)).called == 0
