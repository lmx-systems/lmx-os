"""
Hub closure / holiday calendar (docs/ROADMAP.md R6) against real
Postgres/Redis. Covers the timezone-aware calendar helper, the optimizer
skipping a closed hub, and the admin closure CRUD.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.admin_routes import add_hub_closure, list_hub_closures, remove_hub_closure
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.hub_calendar import is_hub_closed_at, is_hub_closed_on
from app.models.hub import Hub
from app.models.hub_closure import HubClosure
from app.optimizer.service import DispatchOptimizerService
from app.schemas.admin import HubClosureBody

pytestmark = pytest.mark.integration


async def _seed_hub(db_session, *, tz: str = "America/Los_Angeles") -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Closure Hub", timezone=tz, lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.commit()
    return hub


async def test_is_hub_closed_resolves_the_hubs_local_calendar_day(db_session):
    hub = await _seed_hub(db_session, tz="America/Los_Angeles")
    db_session.add(HubClosure(hub_id=hub.id, closure_date=date(2026, 7, 4)))
    await db_session.commit()
    hid = str(hub.id)

    # 6pm UTC on Jul 4 == 11am PDT Jul 4 -> closed.
    assert await is_hub_closed_at(db_session, hid, datetime(2026, 7, 4, 18, tzinfo=timezone.utc)) is True
    # 5am UTC on Jul 4 == 10pm PDT Jul 3 -> a *different* local day -> open.
    # This is the whole point of resolving in the hub's timezone, not UTC.
    assert await is_hub_closed_at(db_session, hid, datetime(2026, 7, 4, 5, tzinfo=timezone.utc)) is False
    assert await is_hub_closed_on(db_session, hid, date(2026, 7, 5)) is False


async def test_is_hub_closed_fails_open_for_an_unknown_hub(db_session):
    # A missing hub must never read as "closed" and silently halt dispatch.
    result = await is_hub_closed_at(db_session, str(uuid.uuid4()), datetime(2026, 7, 4, 18, tzinfo=timezone.utc))
    assert result is False


async def test_optimizer_skips_a_closed_hub_and_leaves_held_orders_queued(db_session, real_redis_client):
    hub = await _seed_hub(db_session, tz="UTC")
    hid = str(hub.id)
    # Closed on the real local (== UTC) today, so run_cycle's own now() lands
    # on this closure.
    db_session.add(HubClosure(hub_id=hub.id, closure_date=datetime.now(timezone.utc).date()))
    await db_session.commit()

    store = HoldQueueStore()
    await store.add(
        hid,
        HeldOrder(
            order_id=str(uuid.uuid4()), shop_lat=34.05, shop_lng=-118.25, sla_tier="T2",
            hold_deadline=datetime.now(timezone.utc) + timedelta(hours=1),
            held_since=datetime.now(timezone.utc), shop_name="S",
        ),
    )

    result = await DispatchOptimizerService().run_cycle(hid)

    assert result.assignments == []
    assert result.unassigned_stop_ids == []
    # The held order was left untouched, waiting for the next open day -
    # not dropped, not assigned to no one.
    assert len(await store.get_all(hid)) == 1


async def test_add_list_and_remove_a_closure(db_session):
    hub = await _seed_hub(db_session)
    hid = str(hub.id)

    created = await add_hub_closure(
        hid, HubClosureBody(closure_date=date(2026, 12, 25), reason="Christmas"), session=db_session
    )
    assert created.closure_date == date(2026, 12, 25)
    assert created.reason == "Christmas"

    listed = await list_hub_closures(hid, session=db_session)
    assert [c.closure_date for c in listed] == [date(2026, 12, 25)]

    await remove_hub_closure(hid, date(2026, 12, 25), session=db_session)
    assert await list_hub_closures(hid, session=db_session) == []


async def test_add_closure_rejects_duplicate_and_unknown_hub(db_session):
    hub = await _seed_hub(db_session)
    hid = str(hub.id)
    await add_hub_closure(hid, HubClosureBody(closure_date=date(2026, 12, 25)), session=db_session)

    with pytest.raises(HTTPException) as exc:
        await add_hub_closure(hid, HubClosureBody(closure_date=date(2026, 12, 25)), session=db_session)
    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as exc:
        await add_hub_closure(
            str(uuid.uuid4()), HubClosureBody(closure_date=date(2026, 12, 25)), session=db_session
        )
    assert exc.value.status_code == 404


async def test_remove_a_nonexistent_closure_404s(db_session):
    hub = await _seed_hub(db_session)
    with pytest.raises(HTTPException) as exc:
        await remove_hub_closure(str(hub.id), date(2026, 1, 1), session=db_session)
    assert exc.value.status_code == 404
