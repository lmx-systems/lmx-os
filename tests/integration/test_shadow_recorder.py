"""
Shadow mode's recording half (docs/ROADMAP.md W9, session decision D3), against real
Postgres/Redis.

Most of these are about the *absence* of behaviour. Shadow mode runs beside a live
human-dispatched operation on a real customer's orders, so the failure that matters is
not a wrong number in a report - it is the shadow path quietly acting, becoming a second
dispatcher competing with the real one for the same drivers. That cannot be asserted by
reading the code once; it has to be pinned by a test that fails if a write is ever added.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.models.hub import Hub
from app.models.shadow_decision import ShadowDecision, ShadowOrderDecision
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation, DriverState
from app.fleet_state.manager import FleetStateManager
from app.shadow.recorder import record_shadow_cycle

pytestmark = pytest.mark.integration


async def _seed_hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Shadow Hub", timezone="UTC", lat=30.26, lng=-97.74)
    db_session.add(hub)
    await db_session.commit()
    return hub


async def _hold(store: HoldQueueStore, hub_id: str, *, tier: str = "T2") -> str:
    order_id = str(uuid.uuid4())
    await store.add(
        hub_id,
        HeldOrder(
            order_id=order_id,
            shop_lat=30.26,
            shop_lng=-97.74,
            sla_tier=tier,
            # Deliberately long past, so the hold queue releases it on the first cycle
            # and the solver actually has something to decide about.
            hold_deadline=datetime(2020, 1, 1, tzinfo=timezone.utc),
            held_since=datetime(2020, 1, 1, tzinfo=timezone.utc),
            shop_name="Shadow Shop",
        ),
    )
    return order_id


async def _driver(fleet: FleetStateManager, hub_id: str) -> str:
    driver_id = str(uuid.uuid4())
    await fleet.upsert_driver_state(
        DriverState(
            driver_id=driver_id,
            hub_id=hub_id,
            status="available",
            capacity_units=10,
            load_units=0,
        )
    )
    # A driver with no known location is skipped by the optimizer entirely, so the
    # location is what makes this driver a candidate at all.
    await fleet.update_driver_location(
        DriverLocation(
            driver_id=driver_id,
            lat=30.26,
            lng=-97.74,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        hub_id,
    )
    return driver_id


async def test_a_shadow_cycle_records_what_it_would_have_done(db_session, real_redis_client):
    hub = await _seed_hub(db_session)
    hid = str(hub.id)
    store = HoldQueueStore()
    fleet = FleetStateManager()

    order_id = await _hold(store, hid)
    await _driver(fleet, hid)

    decision = await record_shadow_cycle(db_session, hid)
    await db_session.commit()

    assert decision.hub_closed is False
    assert decision.held_order_count == 1
    assert decision.driver_count == 1
    # The order was overdue on arrival, so the hold queue releases it immediately and
    # the solver has something to place.
    assert decision.assigned_order_count + decision.unassigned_order_count == 1

    rows = (
        await db_session.execute(
            select(ShadowOrderDecision).where(ShadowOrderDecision.shadow_decision_id == decision.id)
        )
    ).scalars().all()
    assert [str(r.order_id) for r in rows] == [order_id]
    assert rows[0].decision in {"assigned", "unassigned"}
    assert rows[0].sla_tier == "T2"


async def test_a_shadow_cycle_changes_nothing(db_session, real_redis_client):
    """The property the whole feature depends on.

    If this fails, shadow mode has become a second dispatcher racing the real one for
    the same drivers on a live customer's operation.
    """
    hub = await _seed_hub(db_session)
    hid = str(hub.id)
    store = HoldQueueStore()
    fleet = FleetStateManager()

    order_id = await _hold(store, hid)
    driver_id = await _driver(fleet, hid)

    before_queue = {o.order_id for o in await store.get_all(hid)}
    before_fleet = {
        d.driver_id: d.status for d in await fleet.get_fleet_snapshot(hid)
    }

    await record_shadow_cycle(db_session, hid)
    await db_session.commit()

    # The order is still held. A real cycle would have removed it on assignment.
    assert {o.order_id for o in await store.get_all(hid)} == before_queue == {order_id}

    # The driver is still available, not "offered" - no job offer was extended.
    after_fleet = {d.driver_id: d.status for d in await fleet.get_fleet_snapshot(hid)}
    assert after_fleet == before_fleet
    assert after_fleet[driver_id] == "available"


async def test_a_closed_hub_records_a_decision_that_says_it_decided_nothing(
    db_session, real_redis_client
):
    """A quiet day must not read as the optimizer having had a chance and taken it.

    Without `hub_closed`, a closed hub and a hub that considered its orders and placed
    none produce identical rows - and the scorecard's data-completeness metric would
    count the closure as a cycle where LMX OS assigned nothing.
    """
    from app.models.hub_closure import HubClosure

    hub = await _seed_hub(db_session)
    hid = str(hub.id)
    db_session.add(HubClosure(hub_id=hub.id, closure_date=datetime.now(timezone.utc).date()))
    await db_session.commit()

    store = HoldQueueStore()
    await _hold(store, hid)

    decision = await record_shadow_cycle(db_session, hid)
    await db_session.commit()

    assert decision.hub_closed is True
    assert decision.assigned_order_count == 0
    # And it did not report the held order as one it failed to place, which would be a
    # divergence against a scaffold that also (correctly) did nothing that day.
    assert decision.unassigned_order_count == 0
    rows = (
        await db_session.execute(
            select(ShadowOrderDecision).where(ShadowOrderDecision.shadow_decision_id == decision.id)
        )
    ).scalars().all()
    assert rows == []

    # The held order is untouched.
    assert len(await store.get_all(hid)) == 1


async def test_the_recorded_decision_is_the_same_call_run_cycle_makes(db_session, real_redis_client):
    """One implementation, two callers - asserted rather than trusted.

    A second implementation of the decision would drift from the live one, and every
    divergence it reported would then be ambiguous: a real disagreement between LMX OS
    and the scaffold, or the shadow path having fallen behind. This pins that
    `record_shadow_cycle` goes through the optimizer's own `plan_cycle`.
    """
    hub = await _seed_hub(db_session)
    hid = str(hub.id)
    store = HoldQueueStore()
    fleet = FleetStateManager()
    await _hold(store, hid)
    await _driver(fleet, hid)

    calls: list[str] = []
    optimizer = DispatchOptimizerService()
    real_plan_cycle = optimizer.plan_cycle

    async def _spy(hub_id: str):
        calls.append(hub_id)
        return await real_plan_cycle(hub_id)

    optimizer.plan_cycle = _spy  # type: ignore[method-assign]

    await record_shadow_cycle(db_session, hid, optimizer=optimizer)
    await db_session.commit()

    assert calls == [hid]


async def test_decisions_are_queryable_per_order_across_cycles(db_session, real_redis_client):
    """D3: the divergent orders are the entire point, so per-order is the unit.

    Two cycles over the same order must leave two rows that can be told apart by when
    the decision was made - otherwise "we changed our mind at 09:12" is unanswerable.
    """
    hub = await _seed_hub(db_session)
    hid = str(hub.id)
    store = HoldQueueStore()
    fleet = FleetStateManager()
    order_id = await _hold(store, hid)
    await _driver(fleet, hid)

    first = await record_shadow_cycle(db_session, hid)
    await db_session.commit()
    second = await record_shadow_cycle(db_session, hid)
    await db_session.commit()

    assert first.id != second.id

    rows = (
        await db_session.execute(
            select(ShadowOrderDecision)
            .where(ShadowOrderDecision.order_id == uuid.UUID(order_id))
            .order_by(ShadowOrderDecision.planned_at)
        )
    ).scalars().all()
    assert len(rows) == 2
    assert {r.shadow_decision_id for r in rows} == {first.id, second.id}


async def test_a_shadow_decision_survives_its_order_being_deleted(db_session, real_redis_client):
    """R3 retention deletes orders; the evidence in a cutover argument must outlive them.

    `order_id` is deliberately not a foreign key. A cascade would delete the evidence,
    and a restricting FK would block the retention sweep instead.
    """
    hub = await _seed_hub(db_session)
    decision = ShadowDecision(
        hub_id=hub.id,
        planned_at=datetime.now(timezone.utc),
        hub_closed=False,
        engine="stub_nearest_neighbor",
        plan_duration_seconds=0.01,
    )
    db_session.add(decision)
    await db_session.flush()

    # An order id that does not exist in `orders` at all - the state a retention sweep
    # leaves behind.
    db_session.add(
        ShadowOrderDecision(
            shadow_decision_id=decision.id,
            order_id=uuid.uuid4(),
            hub_id=hub.id,
            planned_at=decision.planned_at,
            decision="unassigned",
        )
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(ShadowOrderDecision).where(ShadowOrderDecision.shadow_decision_id == decision.id)
        )
    ).scalars().all()
    assert len(rows) == 1
