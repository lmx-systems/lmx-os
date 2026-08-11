"""
The front door, and what it is gated on.

Signup used to accept an application, record `terms_accepted_version` from the
request body, and tell nobody that the document named on the checkbox did not exist.
Three separate failures in one flow:

  1. **The evidence was written by the applicant.** `clients.terms_accepted_version`
     is the only record of what was agreed to, and any caller could put any string
     in it.
  2. **Nothing checked that the version was current.** A form left open across a
     terms change would record assent to text the applicant never saw.
  3. **Nothing checked the document existed.** A draft was accepted as readily as a
     published one, and the only warning was a comment.

These tests are the three corresponding guarantees. They matter more than they look:
the acceptance record is the artifact a dispute turns on, and a record that can be
forged, staled, or point at nothing is worse than no record - it looks like one.
"""
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.legal.documents as legal
from app.api.internal_routes import prune_retained_data
from app.api.public_routes import client_signup, legal_document_body, legal_documents
from app.config import settings
from app.legal.retention import (
    prune_all,
    prune_communications,
    prune_declined_applications,
    prune_location_pings,
)
from app.models.client import Client
from app.models.driver_location_ping import DriverLocationPing
from app.models.client_user import CLIENT_ADMIN_ROLE, ClientUser
from app.models.hub import Hub
from app.schemas.signup import ClientSignupBody

pytestmark = pytest.mark.integration


class _FakeRequest:
    def __init__(self, ip: str) -> None:
        self.client = type("C", (), {"host": ip})()


def _ip() -> str:
    return f"203.0.113.{uuid.uuid4().int % 250}"


def _signup(**overrides) -> ClientSignupBody:
    payload = dict(
        company_name="Design Partner Auto Parts",
        contact_name="Jordan Rivera",
        contact_email=f"jordan-{uuid.uuid4().hex[:8]}@example.com",
        contact_phone="+15125550142",
        service_area="Austin metro",
        password="a-long-enough-password",
        terms_version=legal.current_terms_version(),
        accepted_terms=True,
    )
    payload.update(overrides)
    return ClientSignupBody(**payload)


async def _seed_hub(db_session) -> uuid.UUID:
    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin", lat=30.26, lng=-97.74))
    await db_session.commit()
    return hub_id


# ---------------------------------------------------------------------------
# 1. The closed door
# ---------------------------------------------------------------------------


async def test_signup_is_refused_while_the_documents_are_drafts(db_session, monkeypatch):
    """The shipped state. Nothing is written, and the applicant is not told why.

    503 rather than 403: this is our problem, it is temporary, and the wording does
    not invite anyone to conclude something about their own application.
    """
    await _seed_hub(db_session)
    monkeypatch.setattr(settings, "allow_unpublished_terms", False)
    assert legal.documents_are_published() is False

    with pytest.raises(HTTPException) as exc:
        await client_signup(body=_signup(), request=_FakeRequest(_ip()), session=db_session)
    assert exc.value.status_code == 503

    # And nothing landed. A refused signup that still created a pending client would
    # be the worst of both: an applicant in the queue with no valid assent recorded.
    count = (await db_session.execute(select(Client))).scalars().all()
    assert count == []


async def test_the_escape_hatch_opens_the_door_but_records_the_real_version(
    db_session, monkeypatch, real_redis_client
):
    """`allow_unpublished_terms` exists for demos and is honest about what it did.

    The version recorded is still the document's own, so a signup taken this way is
    identifiable afterwards rather than indistinguishable from a proper one.
    """
    await _seed_hub(db_session)
    monkeypatch.setattr(settings, "allow_unpublished_terms", True)

    result = await client_signup(
        body=_signup(), request=_FakeRequest(_ip()), session=db_session
    )
    assert result.status == "pending"

    client = (await db_session.execute(select(Client))).scalars().one()
    assert client.terms_accepted_version == legal.current_terms_version()
    assert client.terms_accepted_at is not None


# ---------------------------------------------------------------------------
# 2. The version is the server's, and it must be current
# ---------------------------------------------------------------------------


async def test_a_stale_version_is_refused_rather_than_recorded(
    db_session, monkeypatch, published_terms, real_redis_client
):
    """The form was open when the terms changed.

    409, and nothing written. The alternative - accepting it and storing the current
    version - would record that this applicant agreed to text they never saw, which
    is precisely the thing the version column exists to prevent.
    """
    await _seed_hub(db_session)

    with pytest.raises(HTTPException) as exc:
        await client_signup(
            body=_signup(terms_version="v0"),
            request=_FakeRequest(_ip()),
            session=db_session,
        )
    assert exc.value.status_code == 409
    assert "updated" in exc.value.detail

    assert (await db_session.execute(select(Client))).scalars().all() == []


async def test_the_recorded_version_never_comes_from_the_request(
    db_session, monkeypatch, published_terms, real_redis_client
):
    """The forgery case, made explicit.

    The endpoint compares the submitted version and then writes the document's, so
    there is no path by which a caller's string reaches the evidence column. Here the
    document is v9 and the caller agrees - and the stored value is still read from the
    document rather than echoed from the body.
    """
    await _seed_hub(db_session)
    for name in ("terms", "privacy"):
        bumped = replace(
            legal.DOCUMENTS[name], version="v9", status="published", effective=date(2026, 8, 11)
        )
        monkeypatch.setitem(legal.DOCUMENTS, name, bumped)
        monkeypatch.setattr(legal, name.upper(), bumped)

    await client_signup(
        body=_signup(terms_version="v9"), request=_FakeRequest(_ip()), session=db_session
    )
    client = (await db_session.execute(select(Client))).scalars().one()
    assert client.terms_accepted_version == "v9"


# ---------------------------------------------------------------------------
# 3. The documents are readable, which is what makes the checkbox mean anything
# ---------------------------------------------------------------------------


async def test_the_legal_endpoint_tells_the_form_what_to_present():
    view = await legal_documents()
    assert view.terms.version == legal.TERMS.version
    assert view.terms.path == "/terms"
    assert view.privacy.path == "/privacy"
    # Shipped as drafts, so the form is told not to collect anything.
    assert view.terms.published is False
    assert view.signup_open is False


async def test_signup_open_follows_the_escape_hatch(monkeypatch):
    """The portal asks one question and gets one answer.

    The documents still report themselves as unpublished - the flag opens the form, it
    does not make a draft into a published document.
    """
    monkeypatch.setattr(settings, "allow_unpublished_terms", True)
    view = await legal_documents()
    assert view.signup_open is True
    assert view.terms.published is False


async def test_a_draft_document_is_still_served_and_says_it_is_a_draft():
    """Someone followed the link from the checkbox. Show them the text.

    Refusing would leave the page blank, and a reader told "this is not final" is
    better informed than one shown nothing.
    """
    doc = await legal_document_body("terms")
    assert doc.published is False
    assert doc.version == legal.TERMS.version
    assert "## 1. These terms" in doc.body

    privacy = await legal_document_body("privacy")
    assert "delivery" in privacy.body.lower()


async def test_an_unknown_document_is_a_404():
    with pytest.raises(HTTPException) as exc:
        await legal_document_body("cookies")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 4. Retention. The policy states ninety days, so something has to delete.
# ---------------------------------------------------------------------------


async def _seed_driver_with_pings(db_session, ages_in_days: list[int]) -> uuid.UUID:
    from app.models.driver import Driver

    hub_id = await _seed_hub(db_session)
    driver_id = uuid.uuid4()
    db_session.add(
        Driver(id=driver_id, hub_id=hub_id, name="Sam Okafor", phone=f"+1512555{uuid.uuid4().int % 9000:04d}")
    )
    await db_session.flush()

    now = datetime.now(timezone.utc)
    for age in ages_in_days:
        db_session.add(
            DriverLocationPing(
                driver_id=driver_id,
                hub_id=hub_id,
                lat=30.26,
                lng=-97.74,
                recorded_at=now - timedelta(days=age),
            )
        )
    await db_session.commit()
    return driver_id


async def test_pruning_deletes_only_what_is_past_the_retention_period(db_session, monkeypatch):
    """The number in the privacy policy is the specification.

    91 days old goes; 89 stays. The boundary is the whole point - a prune that took
    everything would destroy the mileage and replay records a live dispute needs.
    """
    monkeypatch.setattr(settings, "location_ping_retention_days", 90)
    await _seed_driver_with_pings(db_session, [120, 91, 89, 1])

    result = await prune_location_pings(db_session)
    assert result["deleted"] == 2
    assert result["retention_days"] == 90

    remaining = (await db_session.execute(select(DriverLocationPing))).scalars().all()
    assert len(remaining) == 2


async def test_pruning_is_safe_to_over_call(db_session, monkeypatch):
    """It wants a daily schedule, so a second run in the same minute must be a
    no-op rather than an error or a second deletion."""
    monkeypatch.setattr(settings, "location_ping_retention_days", 90)
    await _seed_driver_with_pings(db_session, [200, 5])

    first = await prune_location_pings(db_session)
    second = await prune_location_pings(db_session)
    assert first["deleted"] == 1
    assert second["deleted"] == 0
    assert len((await db_session.execute(select(DriverLocationPing))).scalars().all()) == 1


async def test_pruning_with_nothing_due_reports_zero(db_session, monkeypatch):
    monkeypatch.setattr(settings, "location_ping_retention_days", 90)
    await _seed_driver_with_pings(db_session, [3])
    assert (await prune_location_pings(db_session))["deleted"] == 0


async def test_the_retention_route_returns_what_it_deleted(db_session, monkeypatch):
    """The endpoint reports the count rather than 'ok'.

    "The sweep ran" and "the sweep deleted the rows it should have" are different
    claims, and a scheduler that only ever sees 200 cannot tell them apart.
    """
    monkeypatch.setattr(settings, "location_ping_retention_days", 30)
    await _seed_driver_with_pings(db_session, [45, 2])

    result = await prune_retained_data(session=db_session)
    assert result["location_pings"]["deleted"] == 1
    assert result["location_pings"]["retention_days"] == 30
    assert result["location_pings"]["cutoff"]
    # Every category reports, so an operator can tell which sweep actually ran.
    assert set(result) == {"location_pings", "communications", "declined_applications"}


# ---------------------------------------------------------------------------
# 5. The other two retention sweeps
# ---------------------------------------------------------------------------


async def _seed_message(db_session, *, age_days: int):
    from app.models.driver import Driver
    from app.models.message import Message

    hub_id = await _seed_hub(db_session)
    driver_id = uuid.uuid4()
    db_session.add(
        Driver(
            id=driver_id,
            hub_id=hub_id,
            name="Sam Okafor",
            phone=f"+1512555{uuid.uuid4().int % 9000:04d}",
        )
    )
    await db_session.flush()
    msg = Message(
        hub_id=hub_id,
        driver_id=driver_id,
        channel="driver",
        direction="outbound",
        body="Your code is 123456",
        counterparty_phone="+15125550142",
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    db_session.add(msg)
    await db_session.commit()
    return msg


async def test_old_messages_are_deleted_and_recent_ones_are_not(db_session, monkeypatch):
    """The policy says two years for what we texted and to which number."""
    from app.models.message import Message

    monkeypatch.setattr(settings, "communication_retention_days", 730)
    await _seed_message(db_session, age_days=800)
    await _seed_message(db_session, age_days=10)

    result = await prune_communications(db_session)
    assert result["messages_deleted"] == 1
    remaining = (await db_session.execute(select(Message))).scalars().all()
    assert len(remaining) == 1


async def test_a_message_that_never_sent_is_still_pruned(db_session, monkeypatch):
    """Keyed on `created_at`, not a send timestamp.

    A message that failed to send has no send timestamp. Keying on one would keep
    exactly the rows least worth keeping, forever.
    """
    from app.models.message import Message

    monkeypatch.setattr(settings, "communication_retention_days", 30)
    msg = await _seed_message(db_session, age_days=90)
    msg.sent_at = None
    await db_session.commit()

    assert (await prune_communications(db_session))["messages_deleted"] == 1
    assert (await db_session.execute(select(Message))).scalars().all() == []


async def _seed_declined(db_session, *, rejected_days_ago: int | None):
    """A declined application, exactly as signup + rejection leaves it."""
    hub_id = await _seed_hub(db_session)
    client = Client(
        hub_id=hub_id,
        name="Turned Down Auto",
        pos_system="client_portal",
        signup_status="rejected",
        service_area="Austin metro",
        rejected_at=(
            None
            if rejected_days_ago is None
            else datetime.now(timezone.utc) - timedelta(days=rejected_days_ago)
        ),
    )
    db_session.add(client)
    await db_session.flush()
    # The inactive user signup created. This is the only owned row a real declined
    # applicant has, and it has to go with the client or the delete fails on its FK.
    db_session.add(
        ClientUser(
            client_id=client.id,
            email=f"declined-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x" * 60,
            name="Jordan Rivera",
            role=CLIENT_ADMIN_ROLE,
            is_active=False,
        )
    )
    await db_session.commit()
    return client


async def test_an_old_declined_application_is_deleted_with_its_user(db_session, monkeypatch):
    monkeypatch.setattr(settings, "declined_application_retention_days", 365)
    client = await _seed_declined(db_session, rejected_days_ago=400)

    result = await prune_declined_applications(db_session)
    assert result["deleted"] == 1
    assert (await db_session.execute(select(Client))).scalars().all() == []
    # The user goes too - a login row for a company we deleted is the worst of both.
    assert (await db_session.execute(select(ClientUser))).scalars().all() == []
    assert await db_session.get(Client, client.id) is None


async def test_a_recent_rejection_is_kept(db_session, monkeypatch):
    """Long enough to recognise a second application from the same company."""
    monkeypatch.setattr(settings, "declined_application_retention_days", 365)
    await _seed_declined(db_session, rejected_days_ago=30)
    assert (await prune_declined_applications(db_session))["deleted"] == 0
    assert len((await db_session.execute(select(Client))).scalars().all()) == 1


async def test_an_undated_rejection_is_never_deleted_but_is_counted(db_session, monkeypatch):
    """Rejections recorded before migration 0041 have no date.

    The sweep will not invent one - deleting on a guess destroys a record early, and
    keeping it silently hides a stuck row. It reports the count instead.
    """
    monkeypatch.setattr(settings, "declined_application_retention_days", 365)
    await _seed_declined(db_session, rejected_days_ago=None)

    result = await prune_declined_applications(db_session)
    assert result["deleted"] == 0
    assert result["skipped_undated"] == 1
    assert len((await db_session.execute(select(Client))).scalars().all()) == 1


async def test_a_declined_applicant_holding_records_is_left_alone(db_session, monkeypatch):
    """The last line of defence.

    A rejected client cannot order - `POST /client/orders` is gated on active status - so
    an order here means a bug or a hand-edited status. Deleting is the wrong response to a
    surprise, and it would destroy a business record we are meant to keep for seven years.
    """
    from app.models.order import Order, OrderStatus

    monkeypatch.setattr(settings, "declined_application_retention_days", 365)
    client = await _seed_declined(db_session, rejected_days_ago=400)
    db_session.add(
        Order(
            hub_id=client.hub_id,
            client_id=client.id,
            external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
            source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
            source_system="client_portal",
            raw_payload={},
            weight_units=1,
            status=OrderStatus.received,
            requested_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    result = await prune_declined_applications(db_session)
    assert result["deleted"] == 0
    assert result["skipped_with_records"] == 1
    assert await db_session.get(Client, client.id) is not None


async def test_an_approved_client_is_never_touched(db_session, monkeypatch):
    """Only `rejected` is in scope. An active customer with an old signup date is not a
    declined application, and this sweep must not confuse the two."""
    monkeypatch.setattr(settings, "declined_application_retention_days", 1)
    hub_id = await _seed_hub(db_session)
    db_session.add(
        Client(
            hub_id=hub_id,
            name="Design Partner",
            pos_system="client_portal",
            signup_status="active",
            rejected_at=datetime.now(timezone.utc) - timedelta(days=900),
        )
    )
    await db_session.commit()

    assert (await prune_declined_applications(db_session))["deleted"] == 0
    assert len((await db_session.execute(select(Client))).scalars().all()) == 1


async def test_prune_all_runs_every_sweep(db_session, monkeypatch):
    monkeypatch.setattr(settings, "location_ping_retention_days", 30)
    monkeypatch.setattr(settings, "communication_retention_days", 30)
    monkeypatch.setattr(settings, "declined_application_retention_days", 30)
    await _seed_driver_with_pings(db_session, [90])
    await _seed_message(db_session, age_days=90)
    await _seed_declined(db_session, rejected_days_ago=90)

    result = await prune_all(db_session)
    assert result["location_pings"]["deleted"] == 1
    assert result["communications"]["messages_deleted"] == 1
    assert result["declined_applications"]["deleted"] == 1
