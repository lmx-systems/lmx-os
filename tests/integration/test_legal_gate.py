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
from app.legal.retention import prune_location_pings
from app.models.client import Client
from app.models.driver_location_ping import DriverLocationPing
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
    assert result["deleted"] == 1
    assert result["retention_days"] == 30
    assert result["cutoff"]
