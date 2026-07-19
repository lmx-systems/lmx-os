"""
Integration coverage for the driver app's Phase 2 profile screen (1r):
payment method, documents, the document-expiry gate on going online, and
real trip-count computation. See docs/NEXT_STEPS.md item 12.

Calls the route functions directly, same pattern as
tests/integration/test_driver_app_integration.py.
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.api.driver_routes import (
    get_my_profile,
    list_my_documents,
    update_my_availability,
    update_my_document,
    update_my_payment_method,
)
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.route import Route
from app.schemas.driver_app import DriverAvailabilityUpdate, DriverDocumentUpdate, PaymentMethodUpdate
from app.schemas.fleet import DriverState

pytestmark = pytest.mark.integration


async def _seed_driver(db_session):
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Profile Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(
        Driver(
            id=driver_id, hub_id=hub_id, name="Jordan P.", phone="+15555550299",
            vehicle_capacity_units=5, vehicle_type="van", plate_number="ABC-1234", delivery_zone="Zone 4",
        )
    )
    await db_session.commit()
    return hub_id, driver_id


async def test_payment_method_roundtrip(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id))

    profile = await get_my_profile(driver=authed, session=db_session)
    assert profile.payment_bank_last4 is None

    updated = await update_my_payment_method(
        PaymentMethodUpdate(bank_last4="4471"), driver=authed, session=db_session
    )
    assert updated.payment_bank_last4 == "4471"


async def test_documents_roundtrip_and_are_not_expired_by_default(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id))

    assert await list_my_documents(driver=authed, session=db_session) == []

    future = date.today() + timedelta(days=180)
    await update_my_document(
        "license", DriverDocumentUpdate(expires_at=future, file_url="https://example.com/license.jpg"),
        driver=authed, session=db_session,
    )
    await update_my_document(
        "insurance", DriverDocumentUpdate(expires_at=future), driver=authed, session=db_session
    )

    docs = await list_my_documents(driver=authed, session=db_session)
    by_type = {d.doc_type: d for d in docs}
    assert set(by_type) == {"license", "insurance"}
    assert by_type["license"].is_expired is False
    assert by_type["license"].file_url == "https://example.com/license.jpg"

    # Updating the same doc_type again upserts rather than duplicating.
    sooner = date.today() + timedelta(days=10)
    await update_my_document("license", DriverDocumentUpdate(expires_at=sooner), driver=authed, session=db_session)
    docs_after = await list_my_documents(driver=authed, session=db_session)
    assert len([d for d in docs_after if d.doc_type == "license"]) == 1


async def test_going_online_blocked_when_a_document_is_expired(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id))

    expired = date.today() - timedelta(days=1)
    await update_my_document("insurance", DriverDocumentUpdate(expires_at=expired), driver=authed, session=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await update_my_availability(DriverAvailabilityUpdate(status="available"), driver=authed, session=db_session)
    assert exc_info.value.status_code == 409
    assert "insurance" in exc_info.value.detail

    # Going off-shift/on-break should never be blocked by document status -
    # only *going online* (available) is gated.
    await update_my_availability(DriverAvailabilityUpdate(status="on_break"), driver=authed, session=db_session)


async def test_renewing_an_expired_document_unblocks_going_online(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id))

    expired = date.today() - timedelta(days=1)
    await update_my_document("insurance", DriverDocumentUpdate(expires_at=expired), driver=authed, session=db_session)

    future = date.today() + timedelta(days=90)
    await update_my_document("insurance", DriverDocumentUpdate(expires_at=future), driver=authed, session=db_session)

    await update_my_availability(DriverAvailabilityUpdate(status="available"), driver=authed, session=db_session)
    fleet_state = FleetStateManager()
    state = await fleet_state.get_driver_state(str(hub_id), str(driver_id))
    assert state.status == "available"


async def test_trip_count_reflects_completed_routes(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id))

    profile = await get_my_profile(driver=authed, session=db_session)
    assert profile.trip_count == 0

    db_session.add(Route(hub_id=hub_id, driver_id=driver_id, status="completed", plan_version=1))
    db_session.add(Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1))
    await db_session.commit()

    profile_after = await get_my_profile(driver=authed, session=db_session)
    # Only the completed route counts - the still-active one doesn't.
    assert profile_after.trip_count == 1
