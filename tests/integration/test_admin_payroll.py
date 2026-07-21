"""
POST /admin/payroll/{hub_id}/run - submits every driver's most recently
*completed* pay period to the configured PayrollProvider. No Rippling
credentials are configured in tests, so this always goes through
StubPayrollProvider (app/payroll/stub_client.py) - same "unconfigured
credential -> stub" status as Twilio/Google Routes elsewhere in this
codebase.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.payroll.hours as payroll_hours
from app.api.admin_routes import run_payroll_for_hub
from app.models.driver import Driver
from app.models.driver_shift_event import DriverShiftEvent
from app.models.hub import Hub

pytestmark = pytest.mark.integration


async def test_run_payroll_submits_previous_period_hours_for_a_w2_driver(db_session, real_redis_client):
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Payroll Run Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(
        Driver(
            id=driver_id, hub_id=hub_id, name="Riley P.", phone="+15555550310",
            vehicle_capacity_units=5, employment_type="w2",
        )
    )
    await db_session.commit()

    now = datetime.now(timezone.utc)
    prev_start, _prev_end = payroll_hours.previous_pay_period_bounds("w2", now)
    online_at = prev_start + timedelta(hours=9)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=online_at),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=online_at + timedelta(hours=4)),
        ]
    )
    await db_session.commit()

    result = await run_payroll_for_hub(str(hub_id), session=db_session)

    assert result.hub_id == str(hub_id)
    assert result.engine == "stub"
    assert len(result.submissions) == 1
    submission = result.submissions[0]
    assert submission.driver_id == str(driver_id)
    assert submission.employment_type == "w2"
    assert 3.9 <= submission.hours_worked <= 4.1
    assert submission.overtime_hours == 0.0
    assert submission.provider_reference is None  # StubPayrollProvider never returns a real reference


async def test_run_payroll_skips_a_driver_with_no_shift_events_last_period(db_session, real_redis_client):
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Payroll Run Empty Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(
        Driver(id=driver_id, hub_id=hub_id, name="Idle I.", phone="+15555550311", vehicle_capacity_units=5)
    )
    await db_session.commit()

    result = await run_payroll_for_hub(str(hub_id), session=db_session)
    assert result.submissions == []
