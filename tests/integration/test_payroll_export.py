"""
Pay-period export assembly (roadmap item A9) against real Postgres:
hours + completed drops per driver in a window, zero-activity drivers
excluded, routes outside the window excluded, stub submission end-to-end.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.driver import Driver
from app.models.hub import Hub
from app.models.route import Route
from app.models.stop import Stop
from app.payroll.export import assemble_and_submit, assemble_pay_period

pytestmark = pytest.mark.integration


async def _seed(db_session):
    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Payroll Test Hub", lat=34.0, lng=-118.0))
    await db_session.commit()

    linked = Driver(
        id=uuid.uuid4(), hub_id=hub_id, name="Linked L.", phone="+15555550401",
        vehicle_capacity_units=5, payroll_employee_id="emp_linked_1",
    )
    unlinked = Driver(
        id=uuid.uuid4(), hub_id=hub_id, name="Unlinked U.", phone="+15555550402",
        vehicle_capacity_units=5,
    )
    idle = Driver(
        id=uuid.uuid4(), hub_id=hub_id, name="Idle I.", phone="+15555550403",
        vehicle_capacity_units=5, payroll_employee_id="emp_idle_1",
    )
    db_session.add_all([linked, unlinked, idle])
    await db_session.commit()
    return hub_id, linked, unlinked, idle


async def _completed_route(db_session, hub_id, driver_id, *, hours: float, drops: int, end: datetime):
    route = Route(hub_id=hub_id, driver_id=driver_id, status="completed", plan_version=1)
    route.created_at = end - timedelta(hours=hours)
    route.updated_at = end
    db_session.add(route)
    await db_session.commit()
    for i in range(drops):
        db_session.add(
            Stop(route_id=route.id, sequence=i + 1, stop_type="dropoff", status="completed",
                 completed_at=end)
        )
    await db_session.commit()
    return route


async def test_assembles_hours_and_drops_per_driver_within_window(db_session):
    hub_id, linked, unlinked, _idle = await _seed(db_session)
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(days=7), now

    await _completed_route(db_session, hub_id, linked.id, hours=3.0, drops=4, end=now - timedelta(days=1))
    await _completed_route(db_session, hub_id, linked.id, hours=2.0, drops=3, end=now - timedelta(days=2))
    await _completed_route(db_session, hub_id, unlinked.id, hours=1.5, drops=2, end=now - timedelta(days=3))
    # Outside the window - must not count.
    await _completed_route(db_session, hub_id, linked.id, hours=8.0, drops=9, end=now - timedelta(days=20))

    inputs = await assemble_pay_period(db_session, str(hub_id), start, end)
    by_driver = {i.driver_id: i for i in inputs}

    assert set(by_driver) == {str(linked.id), str(unlinked.id)}  # idle driver excluded
    assert by_driver[str(linked.id)].hours_worked == pytest.approx(5.0, abs=0.01)
    assert by_driver[str(linked.id)].completed_drops == 7
    assert by_driver[str(unlinked.id)].hours_worked == pytest.approx(1.5, abs=0.01)
    assert by_driver[str(unlinked.id)].completed_drops == 2


async def test_submit_via_stub_splits_linked_and_unlinked(db_session):
    hub_id, linked, unlinked, _idle = await _seed(db_session)
    now = datetime.now(timezone.utc)

    await _completed_route(db_session, hub_id, linked.id, hours=4.0, drops=5, end=now - timedelta(days=1))
    await _completed_route(db_session, hub_id, unlinked.id, hours=2.0, drops=1, end=now - timedelta(days=1))

    result = await assemble_and_submit(db_session, str(hub_id), now - timedelta(days=7), now)

    assert result.provider == "stub"  # no Rippling credentials in tests
    assert [e.driver_id for e in result.submitted] == [str(linked.id)]
    assert [e.driver_id for e in result.skipped_unlinked] == [str(unlinked.id)]
