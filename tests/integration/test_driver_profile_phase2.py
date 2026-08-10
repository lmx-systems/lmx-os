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
    create_document_upload_url,
    get_my_profile,
    list_my_documents,
    my_compliance,
    update_my_availability,
    update_my_document,
    update_my_payment_method,
)
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.route import Route
from app.schemas.driver_app import (
    DriverAvailabilityUpdate,
    DriverDocumentUpdate,
    DriverDocumentUploadBody,
    PaymentMethodUpdate,
)
from tests.integration.conftest import make_driver_compliant

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
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    profile = await get_my_profile(driver=authed, session=db_session)
    assert profile.payment_bank_last4 is None

    updated = await update_my_payment_method(
        PaymentMethodUpdate(bank_last4="4471"), driver=authed, session=db_session
    )
    assert updated.payment_bank_last4 == "4471"


async def test_a_document_starts_unverified_and_the_driver_cannot_verify_it(db_session):
    """**The hole this closes.** The driver used to set their own expiry date and
    the gate read it back to them, so a lapsed license became a valid one by typing
    next year. The date they give is now recorded as a claim; only an ops reviewer's
    `verified_expires_at` counts."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    assert await list_my_documents(driver=authed, session=db_session) == []

    future = date.today() + timedelta(days=180)
    await create_document_upload_url(
        "license",
        DriverDocumentUploadBody(content_type="image/jpeg", claimed_expires_at=future),
        driver=authed,
        session=db_session,
    )

    doc = (await list_my_documents(driver=authed, session=db_session))[0]
    assert doc.claimed_expires_at == future
    assert doc.verified_expires_at is None
    assert doc.review_status == "pending"
    assert doc.is_usable is False, "nobody has looked at it yet"


async def test_the_driver_cannot_supply_their_own_file_url(db_session):
    """Before this, `PUT /me/documents/{type}` took a file_url and stored any string
    - so `https://example.com/anything` was indistinguishable from a real license
    scan. The field is gone from the request body; the backend writes it from a key
    it minted."""
    assert "file_url" not in DriverDocumentUpdate.model_fields
    assert "file_url" not in DriverDocumentUploadBody.model_fields


async def test_the_upload_url_is_scoped_to_this_driver_and_document(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    result = await create_document_upload_url(
        "insurance",
        DriverDocumentUploadBody(
            content_type="image/jpeg", claimed_expires_at=date.today() + timedelta(days=30)
        ),
        driver=authed,
        session=db_session,
    )

    assert "driver-documents" in result.final_url
    assert str(driver_id) in result.final_url
    assert "insurance" in result.final_url


async def test_an_invented_document_type_is_refused(db_session):
    """doc_type arrives as a URL path segment and used to be stored verbatim."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    with pytest.raises(HTTPException) as exc_info:
        await create_document_upload_url(
            "pilot_licence",
            DriverDocumentUploadBody(
                content_type="image/jpeg", claimed_expires_at=date.today() + timedelta(days=30)
            ),
            driver=authed,
            session=db_session,
        )
    assert exc_info.value.status_code == 422


async def test_an_executable_upload_format_is_refused(db_session):
    """content_type ends up in the presigned policy, so allowing anything would let
    a driver upload an HTML file that renders as a page when a reviewer opens the
    "scan"."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    with pytest.raises(HTTPException) as exc_info:
        await create_document_upload_url(
            "license",
            DriverDocumentUploadBody(
                content_type="text/html", claimed_expires_at=date.today() + timedelta(days=30)
            ),
            driver=authed,
            session=db_session,
        )
    assert exc_info.value.status_code == 422


async def test_going_online_is_blocked_when_no_documents_exist_at_all(
    db_session, real_redis_client
):
    """**The second hole, and the worse one.** The old gate refused only when a row
    on file had passed its expiry - so a driver with no documents whatsoever had
    nothing expired and went online. It blocked the honest driver who recorded a
    lapsed license and cleared the one who recorded nothing."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    with pytest.raises(HTTPException) as exc_info:
        await update_my_availability(
            DriverAvailabilityUpdate(status="available"), driver=authed, session=db_session
        )
    assert exc_info.value.status_code == 409
    # Both missing documents named at once, not one at a time.
    assert "license" in exc_info.value.detail
    assert "insurance" in exc_info.value.detail


async def test_going_online_is_blocked_while_a_document_awaits_review(
    db_session, real_redis_client
):
    """An uploaded-but-unreviewed document must not pass. Treating it as good would
    be the same self-attestation, moved one step later."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    future = date.today() + timedelta(days=180)

    for doc_type in ("license", "insurance"):
        await create_document_upload_url(
            doc_type,
            DriverDocumentUploadBody(content_type="image/jpeg", claimed_expires_at=future),
            driver=authed,
            session=db_session,
        )

    with pytest.raises(HTTPException) as exc_info:
        await update_my_availability(
            DriverAvailabilityUpdate(status="available"), driver=authed, session=db_session
        )
    assert exc_info.value.status_code == 409
    assert "waiting on an LMX review" in exc_info.value.detail


async def test_going_online_works_once_documents_are_verified(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    await make_driver_compliant(db_session, driver_id)

    await update_my_availability(
        DriverAvailabilityUpdate(status="available"), driver=authed, session=db_session
    )
    state = await FleetStateManager().get_driver_state(str(hub_id), str(driver_id))
    assert state.status == "available"


async def test_a_verified_document_that_has_since_expired_blocks_going_online(
    db_session, real_redis_client
):
    """The behaviour that had to survive: a real, reviewed document still lapses."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    await make_driver_compliant(db_session, driver_id, expires_in_days=-1)

    with pytest.raises(HTTPException) as exc_info:
        await update_my_availability(
            DriverAvailabilityUpdate(status="available"), driver=authed, session=db_session
        )
    assert exc_info.value.status_code == 409
    assert "expired" in exc_info.value.detail


async def test_only_going_online_is_gated(db_session, real_redis_client):
    """Going off-shift or on-break must never be blocked by document status - a
    driver with lapsed paperwork still has to be able to end their shift."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    await update_my_availability(
        DriverAvailabilityUpdate(status="on_break"), driver=authed, session=db_session
    )
    await update_my_availability(
        DriverAvailabilityUpdate(status="off_shift"), driver=authed, session=db_session
    )


async def test_the_compliance_endpoint_explains_the_block_before_it_is_hit(db_session):
    """So the app can disable the toggle with a reason, instead of letting a driver
    discover the block by tapping it - and using the SAME computation the gate does,
    rather than the app's own guess."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    before = await my_compliance(driver=authed, session=db_session)
    assert before.can_go_on_shift is False
    assert {p.reason for p in before.problems} == {"missing"}
    assert {p.doc_type for p in before.problems} == {"license", "insurance"}

    await make_driver_compliant(db_session, driver_id)

    after = await my_compliance(driver=authed, session=db_session)
    assert after.can_go_on_shift is True
    assert after.problems == []


async def test_trip_count_reflects_completed_routes(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    profile = await get_my_profile(driver=authed, session=db_session)
    assert profile.trip_count == 0

    db_session.add(Route(hub_id=hub_id, driver_id=driver_id, status="completed", plan_version=1))
    db_session.add(Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1))
    await db_session.commit()

    profile_after = await get_my_profile(driver=authed, session=db_session)
    # Only the completed route counts - the still-active one doesn't.
    assert profile_after.trip_count == 1


async def test_correcting_the_claimed_date_sends_the_document_back_for_review(db_session):
    """If we verified a document against one date and the driver now asserts a
    different one, the verdict no longer matches what they're claiming - so the
    claim can be corrected, but not for free."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await make_driver_compliant(db_session, driver_id)

    before = {d.doc_type: d for d in await list_my_documents(driver=authed, session=db_session)}
    assert before["license"].is_usable is True

    corrected = date.today() + timedelta(days=45)
    updated = await update_my_document(
        "license",
        DriverDocumentUpdate(claimed_expires_at=corrected),
        driver=authed,
        session=db_session,
    )

    assert updated.claimed_expires_at == corrected
    assert updated.review_status == "pending"
    assert updated.verified_expires_at is None
    assert updated.is_usable is False


async def test_restating_the_same_date_is_not_treated_as_a_change(db_session):
    """An idempotent resubmit - the app re-sending the same value on a save - must
    not knock a verified driver off the road."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await make_driver_compliant(db_session, driver_id)

    existing = {d.doc_type: d for d in await list_my_documents(driver=authed, session=db_session)}
    same = existing["license"].claimed_expires_at

    updated = await update_my_document(
        "license",
        DriverDocumentUpdate(claimed_expires_at=same),
        driver=authed,
        session=db_session,
    )

    assert updated.review_status == "verified"
    assert updated.is_usable is True


async def test_correcting_a_document_that_does_not_exist_is_a_404(db_session):
    """Upload first - there is nothing on file to correct, and silently creating a
    row from a bare date would recreate a document with no evidence behind it."""
    hub_id, driver_id = await _seed_driver(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    with pytest.raises(HTTPException) as exc_info:
        await update_my_document(
            "license",
            DriverDocumentUpdate(claimed_expires_at=date.today() + timedelta(days=30)),
            driver=authed,
            session=db_session,
        )
    assert exc_info.value.status_code == 404
