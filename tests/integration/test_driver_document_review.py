"""
Ops review of driver compliance documents (docs/ROADMAP.md R4) against real
Postgres + Redis.

The review step is what makes the availability gate mean anything. Before it, a
driver typed their own document expiry and the gate read it back to them - so
"documents on file, none expired" was a claim the driver made about themselves,
presented as a check the system had performed.

**The test that matters most is `test_verifying_does_not_accept_the_drivers_own
_date`.** If approval defaulted to the claimed date, this whole review would be a
rubber stamp and the hole would still be open, just one step further along.
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.api.admin_routes import list_pending_driver_documents, review_driver_document
from app.api.driver_routes import create_document_upload_url, list_my_documents
from app.compliance.driver_documents import evaluate_driver_documents
from app.driver_auth.dependencies import AuthedDriver
from app.models.driver import Driver
from app.models.driver_document import DriverDocument
from app.models.hub import Hub
from app.models.ops_user import OpsUser
from app.ops_auth.dependencies import AuthedOpsUser
from app.schemas.admin import DriverDocumentReviewBody
from app.schemas.driver_app import DriverDocumentUploadBody

pytestmark = pytest.mark.integration

FUTURE = date.today() + timedelta(days=180)


async def _seed(db_session):
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Driver(
            id=driver_id,
            hub_id=hub_id,
            name="Rich B.",
            phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=5,
        )
    )
    await db_session.commit()
    return hub_id, driver_id


async def _seed_reviewer(db_session) -> AuthedOpsUser:
    ops_id = uuid.uuid4()
    db_session.add(
        OpsUser(
            id=ops_id,
            email=f"ops-{ops_id.hex[:6]}@lmxit.com",
            password_hash="x",
            name="Ops Reviewer",
            role="admin",
        )
    )
    await db_session.commit()
    return AuthedOpsUser(
        ops_user_id=str(ops_id), email="ops@lmxit.com", name="Ops Reviewer", role="admin"
    )


async def _upload(db_session, hub_id, driver_id, doc_type, claimed=FUTURE):
    authed = AuthedDriver(
        driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device"
    )
    await create_document_upload_url(
        doc_type,
        DriverDocumentUploadBody(content_type="image/jpeg", claimed_expires_at=claimed),
        driver=authed,
        session=db_session,
    )
    return authed


async def _document(db_session, driver_id, doc_type) -> DriverDocument:
    from sqlalchemy import select

    result = await db_session.execute(
        select(DriverDocument).where(
            DriverDocument.driver_id == driver_id, DriverDocument.doc_type == doc_type
        )
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


async def test_an_uploaded_document_appears_in_the_review_queue(db_session, real_redis_client):
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    await _upload(db_session, hub_id, driver_id, "license")

    pending = await list_pending_driver_documents(session=db_session, _admin=admin)

    assert len(pending) == 1
    # The driver's name and their claimed date, because the review IS that
    # comparison - a queue showing only a file link would send the reviewer off to
    # look the driver up.
    assert pending[0].driver_name == "Rich B."
    assert pending[0].claimed_expires_at == FUTURE
    assert pending[0].file_url


async def test_a_document_with_nothing_uploaded_is_not_queued(db_session, real_redis_client):
    """There is nothing to review, so listing it would put items in the queue a
    reviewer can only skip."""
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    db_session.add(
        DriverDocument(driver_id=driver_id, doc_type="license", claimed_expires_at=FUTURE)
    )
    await db_session.commit()

    assert await list_pending_driver_documents(session=db_session, _admin=admin) == []


async def test_a_decided_document_leaves_the_queue(db_session, real_redis_client):
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    await _upload(db_session, hub_id, driver_id, "license")
    doc = await _document(db_session, driver_id, "license")

    await review_driver_document(
        str(doc.id),
        DriverDocumentReviewBody(decision="verify", verified_expires_at=FUTURE),
        session=db_session,
        admin=admin,
    )

    assert await list_pending_driver_documents(session=db_session, _admin=admin) == []


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


async def test_verifying_does_not_accept_the_drivers_own_date(db_session, real_redis_client):
    """**The property the whole review exists for.** The reviewer supplies the date
    they read off the document, and it is allowed to contradict the driver. If
    approval copied the claim, this step would be a rubber stamp on self-attested
    data."""
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    driver_says = date.today() + timedelta(days=365)
    document_says = date.today() + timedelta(days=20)
    await _upload(db_session, hub_id, driver_id, "license", claimed=driver_says)
    doc = await _document(db_session, driver_id, "license")

    result = await review_driver_document(
        str(doc.id),
        DriverDocumentReviewBody(decision="verify", verified_expires_at=document_says),
        session=db_session,
        admin=admin,
    )

    assert result.verified_expires_at == document_says
    await db_session.refresh(doc)
    assert doc.verified_expires_at == document_says
    assert doc.claimed_expires_at == driver_says, "the claim is kept, as context"


async def test_verifying_without_a_date_is_refused(db_session, real_redis_client):
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    await _upload(db_session, hub_id, driver_id, "license")
    doc = await _document(db_session, driver_id, "license")

    with pytest.raises(HTTPException) as exc_info:
        await review_driver_document(
            str(doc.id),
            DriverDocumentReviewBody(decision="verify"),
            session=db_session,
            admin=admin,
        )
    assert exc_info.value.status_code == 422


async def test_rejecting_requires_a_reason(db_session, real_redis_client):
    """A driver can't fix a rejection they can't read."""
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    await _upload(db_session, hub_id, driver_id, "license")
    doc = await _document(db_session, driver_id, "license")

    with pytest.raises(HTTPException) as exc_info:
        await review_driver_document(
            str(doc.id),
            DriverDocumentReviewBody(decision="reject"),
            session=db_session,
            admin=admin,
        )
    assert exc_info.value.status_code == 422


async def test_a_rejection_reaches_the_driver(db_session, real_redis_client):
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    authed = await _upload(db_session, hub_id, driver_id, "license")
    doc = await _document(db_session, driver_id, "license")

    await review_driver_document(
        str(doc.id),
        DriverDocumentReviewBody(
            decision="reject", rejection_reason="The photo cuts off the expiry date"
        ),
        session=db_session,
        admin=admin,
    )

    visible = (await list_my_documents(driver=authed, session=db_session))[0]
    assert visible.review_status == "rejected"
    assert "cuts off" in visible.rejection_reason
    assert visible.is_usable is False


async def test_the_verdict_is_attributed(db_session, real_redis_client):
    """If a driver turns out to have been cleared on a bad document, "who cleared
    it" has to have an answer."""
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    await _upload(db_session, hub_id, driver_id, "license")
    doc = await _document(db_session, driver_id, "license")

    await review_driver_document(
        str(doc.id),
        DriverDocumentReviewBody(decision="verify", verified_expires_at=FUTURE),
        session=db_session,
        admin=admin,
    )

    await db_session.refresh(doc)
    assert str(doc.reviewed_by_ops_user_id) == admin.ops_user_id
    assert doc.reviewed_at is not None


async def test_reviewing_twice_is_refused(db_session, real_redis_client):
    """A second verdict on the same evidence is either a mistake or a disagreement
    to be resolved by a re-upload, not by silently overwriting the first reviewer."""
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    await _upload(db_session, hub_id, driver_id, "license")
    doc = await _document(db_session, driver_id, "license")
    body = DriverDocumentReviewBody(decision="verify", verified_expires_at=FUTURE)

    await review_driver_document(str(doc.id), body, session=db_session, admin=admin)

    with pytest.raises(HTTPException) as exc_info:
        await review_driver_document(str(doc.id), body, session=db_session, admin=admin)
    assert exc_info.value.status_code == 409


async def test_an_unknown_document_id_is_a_404(db_session, real_redis_client):
    admin = await _seed_reviewer(db_session)
    for bad in (str(uuid.uuid4()), "not-a-uuid"):
        with pytest.raises(HTTPException) as exc_info:
            await review_driver_document(
                bad,
                DriverDocumentReviewBody(decision="verify", verified_expires_at=FUTURE),
                session=db_session,
                admin=admin,
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Re-uploading resets the verdict
# ---------------------------------------------------------------------------


async def test_replacing_a_verified_document_reopens_the_review(
    db_session, real_redis_client
):
    """A document that was verified, then replaced, is not still verified - the old
    verdict is about a file this row no longer points at."""
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    await _upload(db_session, hub_id, driver_id, "license")
    doc = await _document(db_session, driver_id, "license")
    await review_driver_document(
        str(doc.id),
        DriverDocumentReviewBody(decision="verify", verified_expires_at=FUTURE),
        session=db_session,
        admin=admin,
    )

    await _upload(db_session, hub_id, driver_id, "license", claimed=FUTURE)

    await db_session.refresh(doc)
    assert doc.review_status == "pending"
    assert doc.verified_expires_at is None
    assert doc.reviewed_by_ops_user_id is None
    assert len(await list_pending_driver_documents(session=db_session, _admin=admin)) == 1


async def test_a_re_upload_does_not_overwrite_the_reviewed_object(
    db_session, real_redis_client
):
    """A fresh key per upload, so a driver re-uploading after a rejection can't
    overwrite the evidence a reviewer already looked at."""
    hub_id, driver_id = await _seed(db_session)
    await _upload(db_session, hub_id, driver_id, "license")
    first = (await _document(db_session, driver_id, "license")).file_url

    await _upload(db_session, hub_id, driver_id, "license")
    second = (await _document(db_session, driver_id, "license")).file_url

    assert first != second


# ---------------------------------------------------------------------------
# The reviewer can see whether they actually unblocked anyone
# ---------------------------------------------------------------------------


async def test_clearing_the_last_document_reports_the_driver_as_unblocked(
    db_session, real_redis_client
):
    """Clearing the second of two documents is what actually puts a driver on the
    road, and a reviewer working a queue should see that rather than go and check."""
    hub_id, driver_id = await _seed(db_session)
    admin = await _seed_reviewer(db_session)
    body = DriverDocumentReviewBody(decision="verify", verified_expires_at=FUTURE)

    await _upload(db_session, hub_id, driver_id, "license")
    first = await review_driver_document(
        str((await _document(db_session, driver_id, "license")).id),
        body,
        session=db_session,
        admin=admin,
    )
    assert first.driver_can_go_on_shift is False
    assert any("insurance" in problem for problem in first.outstanding_problems)

    await _upload(db_session, hub_id, driver_id, "insurance")
    second = await review_driver_document(
        str((await _document(db_session, driver_id, "insurance")).id),
        body,
        session=db_session,
        admin=admin,
    )
    assert second.driver_can_go_on_shift is True
    assert second.outstanding_problems == []

    assert (await evaluate_driver_documents(db_session, str(driver_id))).can_go_on_shift
