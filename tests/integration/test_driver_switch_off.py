"""Switching a driver off, and the revocation it rests on.

A driver who left kept working sessions: the token renews itself on every app
open, and the only revocation was per device, held in a Redis set. These cover
the switch (`POST /admin/drivers/{id}/deactivate` and `/reactivate`), the
roster the console needs to reach somebody who has left, and revocation read
from the database so a Redis flush cannot undo it.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_routes import (
    admin_deactivate_driver,
    admin_issue_sign_in_code,
    admin_list_hub_drivers,
    admin_reactivate_driver,
    admin_revoke_driver_device,
)
from app.api.driver_routes import sign_in, update_my_availability
from app.batch_queue.store import HoldQueueStore
from app.driver_auth import sign_in_codes
from app.driver_auth.dependencies import AuthedDriver, get_current_driver
from app.fleet_state.manager import FleetStateManager
from app.models.driver import Driver
from app.models.driver_device import DriverDevice
from app.models.hub import Hub
from app.models.order import OrderStatus
from app.models.ops_user import OpsUser
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.ops_auth.dependencies import AuthedOpsUser
from app.optimizer.service import DispatchOptimizerService
from app.schemas.driver_app import DriverAvailabilityUpdate
from app.schemas.driver_auth import SignInBody
from app.schemas.fleet import DriverState
from tests.integration import test_driver_app_integration as driver_app
from tests.integration.conftest import fake_request, sign_in_driver
from tests.integration.queue_helpers import let_the_hold_run_out

pytestmark = pytest.mark.integration

PHONE = "+15555550410"


async def _seed(db_session):
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Switch-off Hub", lat=30.27, lng=-97.74))
    await db_session.commit()
    db_session.add(
        Driver(id=driver_id, hub_id=hub_id, name="Ana R.", phone=PHONE, vehicle_capacity_units=5)
    )
    ops_id = uuid.uuid4()
    db_session.add(
        OpsUser(id=ops_id, email=f"ops-{ops_id.hex[:6]}@example.com", password_hash="x", name="Ops", role="admin")
    )
    await db_session.commit()
    admin = AuthedOpsUser(ops_user_id=str(ops_id), email="ops@example.com", name="Ops", role="admin")
    return hub_id, driver_id, admin


async def _sign_in(db_session, device_id: str) -> str:
    driver_id = await db_session.scalar(select(Driver.id).where(Driver.phone == PHONE))
    return await sign_in_driver(db_session, driver_id, device_id)


async def test_switching_a_driver_off_ends_every_session(db_session, real_redis_client):
    _, driver_id, admin = await _seed(db_session)
    first = await _sign_in(db_session, "phone-a")
    second = await _sign_in(db_session, "phone-b")

    view = await admin_deactivate_driver(driver_id=str(driver_id), session=db_session, _admin=admin)

    assert view.is_active is False and view.deactivated_at is not None
    for token in (first, second):
        with pytest.raises(HTTPException) as exc:
            await get_current_driver(authorization=f"Bearer {token}")
        assert exc.value.status_code == 401
        assert "switched off" in exc.value.detail
    devices = (
        await db_session.scalars(select(DriverDevice).where(DriverDevice.driver_id == driver_id))
    ).all()
    assert len(devices) == 2 and all(d.revoked_at is not None for d in devices)


async def test_a_switched_off_driver_cannot_sign_in_again(db_session, real_redis_client):
    """Not with a code issued before the switch-off, and no new code is issued:
    signing in clears a device's revocation, so an old code must not bring a
    departed driver's phone back."""
    _, driver_id, admin = await _seed(db_session)
    early = await sign_in_codes.issue_code(db_session, driver_id, None)
    await db_session.commit()

    await admin_deactivate_driver(driver_id=str(driver_id), session=db_session, _admin=admin)

    with pytest.raises(HTTPException) as exc:
        await sign_in(
            SignInBody(code=early.code, device_id="phone-a", device_name="Phone"),
            request=fake_request(),
            session=db_session,
        )
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await admin_issue_sign_in_code(driver_id=str(driver_id), session=db_session, _admin=admin)
    assert exc.value.status_code == 409


async def test_switching_off_is_refused_while_the_driver_holds_a_route(db_session, real_redis_client):
    hub_id, driver_id, admin = await _seed(db_session)
    db_session.add(Route(hub_id=hub_id, driver_id=driver_id, status="active"))
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await admin_deactivate_driver(driver_id=str(driver_id), session=db_session, _admin=admin)

    assert exc.value.status_code == 409
    assert "1 open route" in exc.value.detail
    driver = await db_session.get(Driver, driver_id)
    await db_session.refresh(driver)
    assert driver.is_active is True


async def test_switching_off_is_refused_while_an_offer_waits_for_an_answer(db_session, real_redis_client):
    hub_id, driver_id, admin = await _seed(db_session)
    now = datetime.now(timezone.utc)
    db_session.add(
        RouteOffer(
            hub_id=hub_id, driver_id=driver_id, status="offered", stop_payload=[],
            offered_at=now, expires_at=now + timedelta(minutes=2),
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await admin_deactivate_driver(driver_id=str(driver_id), session=db_session, _admin=admin)

    assert exc.value.status_code == 409
    assert "1 unanswered offer" in exc.value.detail


async def test_switching_off_takes_the_driver_off_shift(db_session, real_redis_client):
    """So dispatch, which reads the fleet state, stops offering them work."""
    hub_id, driver_id, admin = await _seed(db_session)
    manager = FleetStateManager()
    await manager.upsert_driver_state(
        DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5, load_units=0)
    )
    driver = await db_session.get(Driver, driver_id)
    driver.status = "available"
    await db_session.commit()

    await admin_deactivate_driver(driver_id=str(driver_id), session=db_session, _admin=admin)

    state = await manager.get_driver_state(str(hub_id), str(driver_id))
    assert state is not None and state.status == "off_shift"
    assert not await real_redis_client.sismember(f"fleet:{hub_id}:available_drivers", str(driver_id))
    await db_session.refresh(driver)
    assert driver.status == "off_shift"


async def test_a_revoked_device_stays_revoked_when_redis_is_flushed(db_session, real_redis_client):
    """Revocation used to live only in a Redis set; a flush brought the phone back."""
    _, driver_id, admin = await _seed(db_session)
    token = await _sign_in(db_session, "phone-a")
    await admin_revoke_driver_device(
        driver_id=str(driver_id), device_id="phone-a", session=db_session, _admin=admin
    )

    await real_redis_client.flushdb()

    with pytest.raises(HTTPException) as exc:
        await get_current_driver(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401
    assert "revoked" in exc.value.detail


async def test_switching_back_on_lets_the_driver_sign_in_again(db_session, real_redis_client):
    _, driver_id, admin = await _seed(db_session)
    old = await _sign_in(db_session, "phone-a")
    await admin_deactivate_driver(driver_id=str(driver_id), session=db_session, _admin=admin)

    view = await admin_reactivate_driver(driver_id=str(driver_id), session=db_session, _admin=admin)

    assert view.is_active is True and view.deactivated_at is None
    # The old session stays dead: the device is still revoked until the
    # driver signs in again on it.
    with pytest.raises(HTTPException):
        await get_current_driver(authorization=f"Bearer {old}")
    fresh = await _sign_in(db_session, "phone-a")
    authed = await get_current_driver(authorization=f"Bearer {fresh}")
    assert authed.driver_id == str(driver_id)


async def test_the_roster_lists_switched_off_drivers_after_active_ones(db_session, real_redis_client):
    hub_id, driver_id, admin = await _seed(db_session)
    db_session.add(Driver(hub_id=hub_id, name="Zed Q.", phone="+15555550411", vehicle_capacity_units=5))
    await db_session.commit()
    await admin_deactivate_driver(driver_id=str(driver_id), session=db_session, _admin=admin)

    roster = await admin_list_hub_drivers(hub_id=str(hub_id), session=db_session, _admin=admin)

    assert [(d.name, d.is_active) for d in roster] == [("Zed Q.", True), ("Ana R.", False)]


async def test_a_retried_switch_off_puts_dispatchs_pool_right(db_session, real_redis_client):
    """A first attempt that committed and then failed on Redis left the driver
    in dispatch's pool, and every retry returned early. The retry now repairs it."""
    hub_id, driver_id, admin = await _seed(db_session)
    manager = FleetStateManager()
    await manager.upsert_driver_state(
        DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5, load_units=0)
    )
    driver = await db_session.get(Driver, driver_id)
    driver.is_active = False
    await db_session.commit()

    await admin_deactivate_driver(driver_id=str(driver_id), session=db_session, _admin=admin)

    state = await manager.get_driver_state(str(hub_id), str(driver_id))
    assert state is not None and state.status == "off_shift"


async def test_the_duty_switch_refuses_a_driver_switched_off_mid_request(db_session, real_redis_client):
    """Authenticated a moment before the switch-off, the request must still lose:
    otherwise the driver goes back on duty and payroll keeps counting."""
    hub_id, driver_id, _ = await _seed(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="phone-a")
    driver = await db_session.get(Driver, driver_id)
    driver.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await update_my_availability(
            DriverAvailabilityUpdate(status="available"), driver=authed, session=db_session
        )

    assert exc.value.status_code == 401
    assert await FleetStateManager().get_driver_state(str(hub_id), str(driver_id)) is None


async def test_dispatch_offers_nothing_to_a_driver_switched_off_since_its_snapshot(
    db_session, real_redis_client
):
    """Dispatch plans from the fleet state, which can still say available. The
    row says otherwise, so the work goes back to the queue and the driver
    comes out of the pool."""
    hub_id, _client_id, _shop_id, driver_id, order = await driver_app._seed(db_session)
    driver = await db_session.get(Driver, driver_id)
    driver.is_active = False
    await db_session.commit()
    await let_the_hold_run_out(hub_id)

    await DispatchOptimizerService().run_cycle(str(hub_id))

    offers = (await db_session.scalars(select(RouteOffer).where(RouteOffer.driver_id == driver_id))).all()
    assert offers == []
    await db_session.refresh(order)
    assert order.status == OrderStatus.held
    assert str(order.id) in {h.order_id for h in await HoldQueueStore().get_all(str(hub_id))}
    state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
    assert state is not None and state.status == "off_shift"
