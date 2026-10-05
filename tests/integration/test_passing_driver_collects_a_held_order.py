"""A driver already heading a held order's way collects it on the way (§6, Q3).

The queue counted idle drivers. A hub whose only driver was out on a route had
none, so a held order was kept "for want of a driver" while that driver's van
passed the shop door, and it left the queue only when its hold ran out.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.models.order import Order, OrderStatus
from app.models.stop import Stop, StopOrder
from app.optimizer.service import DispatchOptimizerService
from tests.integration import test_optimizer_live_route_push as push

pytestmark = pytest.mark.integration


async def _held(db_session, order) -> HeldOrder:
    """The order as the hold queue holds it, with an hour of hold left."""
    order.status = OrderStatus.held
    order.hold_deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    await db_session.commit()
    held = HeldOrder(
        order_id=str(order.id),
        shop_lat=34.06,
        shop_lng=-118.26,
        sla_tier="T2",
        hold_deadline=order.hold_deadline,
        held_since=datetime.now(timezone.utc),
        shop_name="New Shop",
        delivery_lat=float(order.delivery_lat),
        delivery_lng=float(order.delivery_lng),
    )
    await HoldQueueStore().add(str(order.hub_id), held)
    return held


async def test_a_passing_driver_collects_a_held_order(db_session, real_redis_client):
    hub_id, client_id, _shop, _driver, route, _ = await push._seed_active_route(db_session)
    order, _ = await push._seed_new_order(db_session, hub_id, client_id)
    await _held(db_session, order)

    result = await DispatchOptimizerService().run_cycle(str(hub_id))

    assert result.unassigned_stop_ids == []
    placed_on = {
        row.route_id
        for row in (
            await db_session.execute(
                select(Stop).join(StopOrder, StopOrder.stop_id == Stop.id).where(StopOrder.order_id == order.id)
            )
        ).scalars()
    }
    assert placed_on == {route.id}
    assert await HoldQueueStore().get_all(str(hub_id)) == []
    order_id = order.id
    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.assigned


async def test_the_decision_says_a_driver_was_passing(db_session, real_redis_client):
    hub_id, client_id, *_ = await push._seed_active_route(db_session)
    order, _ = await push._seed_new_order(db_session, hub_id, client_id)
    await _held(db_session, order)

    plan = await DispatchOptimizerService().plan_cycle(str(hub_id))

    [decision] = plan.hold_decisions
    assert (decision.action, decision.reason) == ("release", "driver_passing")


async def test_a_driver_who_is_not_passing_leaves_it_held(db_session, real_redis_client):
    """Without question 3 the reason was the same, but so was the outcome for
    the order a van was passing. Here the van is twenty miles away, and the
    hold is right."""
    hub_id, client_id, *_ = await push._seed_active_route(db_session, ends_at=push.FAR_AWAY)
    order, _ = await push._seed_new_order(db_session, hub_id, client_id)
    await _held(db_session, order)

    result = await DispatchOptimizerService().run_cycle(str(hub_id))

    assert result.assignments == []
    assert [h.order_id for h in await HoldQueueStore().get_all(str(hub_id))] == [str(order.id)]
    order_id = order.id
    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.held
