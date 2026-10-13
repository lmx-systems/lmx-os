"""The playbook against real Postgres and Redis (`DEC-7`, `DEC-8`, `DEC-9`).

The failed-delivery rule in each of its modes, a person agreeing with it or
overruling it, escalations, calls the world moved past, and the touch count a
person's write leaves behind.
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.api.admin_routes import resolve_order
from app.batch_queue.store import HoldQueueStore
from app.client_auth.passwords import hash_password
from app.config import settings
from app.main import app
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.ops_touch import OpsTouch
from app.models.ops_user import DISPATCHER_ROLE, OpsUser
from app.models.order import INTAKE_BACKFILL, Order, OrderStatus
from app.models.playbook_decision import PlaybookDecision
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.ops_auth.dependencies import AuthedOpsUser
from app.ops_auth.tokens import issue_token
from app.orders.status_service import advance_orders
from app.playbook.report import build_playbook_report
from app.playbook.sweep import run_playbook
from app.reporting.exceptions import build_exception_queue
from app.schemas.admin import ResolveFailedOrderBody

pytestmark = pytest.mark.integration

_OPS = AuthedOpsUser(ops_user_id=str(uuid.uuid4()), email="ops@example.com", name="Ops", role="admin")


async def _hub(db_session):
    hub = Hub(id=uuid.uuid4(), name="Playbook Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Playbook Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    shop = Shop(client_id=client.id, name="Playbook Shop", address="1 Somewhere Way", lat=34.06, lng=-118.24)
    driver = Driver(
        hub_id=hub.id, name="Playbook D.", phone=f"+1555{uuid.uuid4().int % 10**7:07d}", vehicle_capacity_units=5
    )
    db_session.add_all([shop, driver])
    await db_session.flush()
    # Ids, not rows: the sweep commits, and an expired row read outside a greenlet raises.
    seeded = (hub.id, client.id, shop.id, driver.id)
    await db_session.commit()
    return seeded


async def _failed(db_session, seeded, *, reason="SHOP_CLOSED", at="dropoff", attempts=1, intake_mode=None):
    """An order whose `at` stop a driver flagged with `reason`, as flag_stop_issue leaves it."""
    hub_id, client_id, shop_id, driver_id = seeded
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref=f"PB-{uuid.uuid4().hex[:6]}", source_system="flat_file", raw_payload={},
        sla_tier="T2", status=OrderStatus.delivery_failed, failure_reason=reason,
        requested_at=now, updated_at=now, delivery_attempts=attempts,
        delivery_address="2 Elsewhere Rd", delivery_lat=34.07, delivery_lng=-118.23,
    )
    if intake_mode is not None:
        order.intake_mode = intake_mode
    route = Route(hub_id=hub_id, driver_id=driver_id, status="completed")
    db_session.add_all([order, route])
    await db_session.flush()
    stop = Stop(
        route_id=route.id, shop_id=shop_id if at == "pickup" else None, sequence=1, stop_type=at,
        status="failed", failure_reason=reason, flagged_at=now, parcel_count=1,
    )
    db_session.add(stop)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    order_id = order.id
    await db_session.commit()
    return order_id


async def _decision(db_session, order_id) -> PlaybookDecision:
    db_session.expire_all()
    return (
        await db_session.scalars(
            select(PlaybookDecision)
            .where(PlaybookDecision.order_id == order_id)
            .order_by(PlaybookDecision.attempt.desc())
        )
    ).first()


async def _order(db_session, order_id) -> Order:
    return await db_session.get(Order, order_id, populate_existing=True)


async def _resolve(db_session, order_id, action):
    return await resolve_order(str(order_id), ResolveFailedOrderBody(action=action), session=db_session, ops=_OPS)


async def test_recommend_mode_calls_and_does_nothing(db_session, real_redis_client):
    seeded = await _hub(db_session)
    order = await _failed(db_session, seeded)

    sweep = await run_playbook(db_session, seeded[0])

    assert sweep.called == 1 and sweep.acted == 0
    decision = await _decision(db_session, order)
    assert (decision.action, decision.mode, decision.outcome) == ("redeliver", "recommend", "open")
    assert decision.due_at is None
    assert (await _order(db_session, order)).status == OrderStatus.delivery_failed

    # The exceptions queue shows it beside the order.
    queue = await build_exception_queue(db_session, hub_id=seeded[0])
    [item] = [i for i in queue.items if i.order_id == order]
    assert item.playbook is not None
    assert (item.playbook.action, item.playbook.outcome) == ("redeliver", "open")

    # Once per attempt: a second sweep makes no second call.
    assert (await run_playbook(db_session, seeded[0])).called == 0


async def test_a_person_agreeing_and_a_person_overruling(db_session, real_redis_client):
    seeded = await _hub(db_session)
    agreed = await _failed(db_session, seeded)
    overruled = await _failed(db_session, seeded)
    await run_playbook(db_session, seeded[0])

    await _resolve(db_session, agreed, "redeliver")
    await _resolve(db_session, overruled, "cancel")

    first = await _decision(db_session, agreed)
    assert (first.outcome, first.human_action, first.ops_user_id) == ("agreed", "redeliver", _OPS.ops_user_id)
    second = await _decision(db_session, overruled)
    assert (second.outcome, second.human_action) == ("overruled", "cancel")

    report = await build_playbook_report(db_session, hub_id=seeded[0])
    [rule] = [r for r in report.rules if r.rule == "failed_delivery"]
    assert (rule.yes, rule.no, rule.agreement) == (1, 1, 0.5)
    assert not rule.ready_to_move_up
    [mistake] = report.overruled
    assert (mistake.order_id, mistake.rule_action, mistake.human_action) == (overruled, "redeliver", "cancel")


async def test_veto_mode_waits_then_acts(db_session, real_redis_client, monkeypatch):
    monkeypatch.setattr(settings, "playbook_failed_delivery_mode", "veto")
    seeded = await _hub(db_session)
    order = await _failed(db_session, seeded)
    now = datetime.now(timezone.utc)

    await run_playbook(db_session, seeded[0], now=now)
    decision = await _decision(db_session, order)
    assert decision.due_at == now + timedelta(minutes=settings.playbook_veto_minutes)
    assert (await _order(db_session, order)).status == OrderStatus.delivery_failed

    later = now + timedelta(minutes=settings.playbook_veto_minutes + 1)
    sweep = await run_playbook(db_session, seeded[0], now=later)

    assert sweep.acted == 1
    db_session.expire_all()
    redelivered = await _order(db_session, order)
    assert redelivered.status == OrderStatus.held
    assert redelivered.delivery_attempts == 2
    assert str(order) in {h.order_id for h in await HoldQueueStore().get_all(str(seeded[0]))}
    assert (await _decision(db_session, order)).outcome == "acted"

    # Back in flight, so no new call and nothing superseded.
    again = await run_playbook(db_session, seeded[0], now=later)
    assert (again.called, again.acted, again.superseded) == (0, 0, 0)


async def test_a_person_inside_the_veto_window_wins(db_session, real_redis_client, monkeypatch):
    monkeypatch.setattr(settings, "playbook_failed_delivery_mode", "veto")
    seeded = await _hub(db_session)
    order = await _failed(db_session, seeded)
    now = datetime.now(timezone.utc)
    await run_playbook(db_session, seeded[0], now=now)

    await _resolve(db_session, order, "return_to_shop")
    sweep = await run_playbook(db_session, seeded[0], now=now + timedelta(hours=1))

    assert sweep.acted == 0
    assert (await _order(db_session, order)).status == OrderStatus.returned
    assert (await _decision(db_session, order)).outcome == "overruled"


async def test_act_mode_acts_in_the_same_pass(db_session, real_redis_client, monkeypatch):
    monkeypatch.setattr(settings, "playbook_failed_delivery_mode", "act")
    seeded = await _hub(db_session)
    refused = await _failed(db_session, seeded, reason="REFUSED")

    sweep = await run_playbook(db_session, seeded[0])

    assert (sweep.called, sweep.acted) == (1, 1)
    db_session.expire_all()
    assert (await _order(db_session, refused)).status == OrderStatus.returned
    # A person arriving after gets the 409 two people resolving one failure always got.
    with pytest.raises(Exception) as refused_late:
        await _resolve(db_session, refused, "redeliver")
    assert getattr(refused_late.value, "status_code", None) == 409
    await db_session.rollback()
    assert (await _decision(db_session, refused)).outcome == "acted"


async def test_a_second_failure_is_a_new_call(db_session, real_redis_client, monkeypatch):
    monkeypatch.setattr(settings, "playbook_failed_delivery_mode", "act")
    seeded = await _hub(db_session)
    order = await _failed(db_session, seeded)
    await run_playbook(db_session, seeded[0])  # redelivers: attempt 2

    # The second attempt fails the same way.
    await advance_orders(db_session, [order], OrderStatus.delivery_failed, only_from=(OrderStatus.held,))
    (await _order(db_session, order)).failure_reason = "SHOP_CLOSED"
    await db_session.commit()
    await run_playbook(db_session, seeded[0])

    db_session.expire_all()
    calls = list(
        await db_session.scalars(
            select(PlaybookDecision).where(PlaybookDecision.order_id == order).order_by(PlaybookDecision.attempt)
        )
    )
    assert [(c.attempt, c.action, c.outcome) for c in calls] == [
        (1, "redeliver", "acted"),
        (2, "return_to_shop", "acted"),
    ]
    assert (await _order(db_session, order)).status == OrderStatus.returned


async def test_an_escalation_is_left_to_a_person(db_session, real_redis_client, monkeypatch):
    monkeypatch.setattr(settings, "playbook_failed_delivery_mode", "act")
    seeded = await _hub(db_session)
    order = await _failed(db_session, seeded, reason="COD_DISPUTE")

    sweep = await run_playbook(db_session, seeded[0])

    assert (sweep.called, sweep.escalated, sweep.acted) == (1, 1, 0)
    assert (await _order(db_session, order)).status == OrderStatus.delivery_failed
    queue = await build_exception_queue(db_session, hub_id=seeded[0])
    [item] = [i for i in queue.items if i.order_id == order]
    assert item.playbook.outcome == "escalated"

    await _resolve(db_session, order, "cancel")
    decision = await _decision(db_session, order)
    # Still an escalation - not counted for or against the rule - with the answer it lacked.
    assert (decision.outcome, decision.human_action) == ("escalated", "cancel")


async def test_a_call_the_order_moved_past_is_superseded(db_session, real_redis_client):
    seeded = await _hub(db_session)
    order = await _failed(db_session, seeded)
    await run_playbook(db_session, seeded[0])

    # The client cancels it some other way.
    await advance_orders(db_session, [order], OrderStatus.cancelled, only_from=(OrderStatus.delivery_failed,))
    await db_session.commit()
    sweep = await run_playbook(db_session, seeded[0])

    assert sweep.superseded == 1
    assert (await _decision(db_session, order)).outcome == "superseded"


async def test_backfilled_orders_get_no_call(db_session, real_redis_client):
    seeded = await _hub(db_session)
    await _failed(db_session, seeded, intake_mode=INTAKE_BACKFILL)

    assert (await run_playbook(db_session, seeded[0])).called == 0


async def test_a_person_resolving_over_http_is_one_touch(db_session, real_redis_client):
    seeded = await _hub(db_session)
    order = await _failed(db_session, seeded)
    delivered = await _failed(db_session, seeded)
    await advance_orders(db_session, [delivered], OrderStatus.cancelled, only_from=(OrderStatus.delivery_failed,))
    await db_session.commit()
    user = OpsUser(
        email=f"dispatch-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("correct horse battery staple"),
        name="Test dispatcher", is_active=True, role=DISPATCHER_ROLE,
    )
    db_session.add(user)
    await db_session.flush()
    user_id = str(user.id)
    await db_session.commit()
    headers = {"Authorization": f"Bearer {issue_token(user_id)}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post(f"/admin/orders/{order}/resolve", json={"action": "redeliver"}, headers=headers)
        refused = await client.post(
            f"/admin/orders/{delivered}/resolve", json={"action": "redeliver"}, headers=headers
        )
        read = await client.get(f"/operations/playbook?hub_id={seeded[0]}", headers=headers)

    assert ok.status_code == 200, ok.text
    assert refused.status_code == 409
    db_session.expire_all()
    touches = list(await db_session.scalars(select(OpsTouch).where(OpsTouch.ops_user_id == user_id)))
    # The refusal and the read are not touches.
    assert len(touches) == 1
    [touch] = touches
    assert (touch.action, touch.category, touch.method) == ("resolve_order", "day", "POST")
    assert (touch.order_id, touch.hub_id) == (order, seeded[0])
    assert touch.path_params == {"order_id": str(order)}

    assert read.status_code == 200, read.text
    body = read.json()
    assert body["touches"] == 1
    assert body["orders"] == 2
    assert body["touches_per_100_orders"] == 50.0
    assert body["by_action"] == [{"action": "resolve_order", "category": "day", "count": 1}]


async def test_a_touch_cannot_be_edited(db_session):
    db_session.add(
        OpsTouch(
            touched_at=datetime.now(timezone.utc), ops_user_id="x", action="resolve_order",
            category="day", method="POST", path_params={},
        )
    )
    await db_session.commit()
    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(OpsTouch.__table__.delete())
        await db_session.commit()
    await db_session.rollback()
    assert await db_session.scalar(select(func.count()).select_from(OpsTouch)) >= 1


async def test_turning_the_rule_down_stops_what_it_scheduled(db_session, real_redis_client, monkeypatch):
    """The mode is the off switch: a rule set back to recommend carries out none
    of the calls it scheduled in veto mode."""
    monkeypatch.setattr(settings, "playbook_failed_delivery_mode", "veto")
    seeded = await _hub(db_session)
    order = await _failed(db_session, seeded)
    now = datetime.now(timezone.utc)
    await run_playbook(db_session, seeded[0], now=now)

    monkeypatch.setattr(settings, "playbook_failed_delivery_mode", "recommend")
    sweep = await run_playbook(db_session, seeded[0], now=now + timedelta(hours=1))

    assert sweep.acted == 0
    assert (await _order(db_session, order)).status == OrderStatus.delivery_failed
    decision = await _decision(db_session, order)
    assert (decision.mode, decision.due_at, decision.outcome) == ("recommend", None, "open")


async def test_a_touch_on_a_stop_belongs_to_its_hub(db_session, real_redis_client):
    """Most record work names a stop, a return or a driver rather than an order;
    without the hub it vanished from the hub's rate."""
    from app.playbook.touches import _hub_of

    seeded = await _hub(db_session)
    order = await _failed(db_session, seeded)
    stop_id = await db_session.scalar(select(StopOrder.stop_id).where(StopOrder.order_id == order))

    assert await _hub_of(db_session, {"stop_id": str(stop_id)}) == seeded[0]
    assert await _hub_of(db_session, {"driver_id": str(seeded[3])}) == seeded[0]
    assert await _hub_of(db_session, {"client_id": str(seeded[1])}) == seeded[0]
    assert await _hub_of(db_session, {"location_id": str(uuid.uuid4())}) is None
