"""
Public client signup and the LMX approval gate (docs/LMX_LINK_PLAN.md).

This reverses roadmap item `C5` ("no client-initiated signup, by design"). The
approval gate is what keeps that decision's substance - anyone can apply, nobody
dispatches an LMX van until a human says so - so the tests that matter most here
are the ones proving a pending applicant genuinely cannot act.

Two of them are security properties rather than features:

  - a pending client's user cannot log in, enforced by C4's existing per-request
    `is_active` check rather than by anything new in the auth path;
  - a duplicate email gets the same 202 and the same message as a fresh signup,
    so the endpoint can't be used to discover which companies are already LMX
    customers.
"""
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.admin_routes import approve_signup, list_signups, reject_signup
from app.api.public_routes import client_signup
from app.client_auth.signup_rate_limit import MAX_SIGNUP_ATTEMPTS
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.client_user import ClientUser
from app.models.hub import Hub
from app.schemas.signup import (
    ApproveRateInput,
    ApproveSignupBody,
    ClientSignupBody,
    RejectSignupBody,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _signup_open(published_terms):
    """Every signup in this module runs against published legal documents.

    Without this the endpoint 503s: it will not record assent to a draft. See
    tests/integration/conftest.py::published_terms, and test_legal_documents.py for
    the tests of the closed door itself.
    """


class _FakeRequest:
    """Minimal stand-in for the bits of Request the endpoint reads."""

    def __init__(self, ip: str) -> None:
        self.client = type("C", (), {"host": ip})()


def _signup(**overrides) -> ClientSignupBody:
    payload = dict(
        company_name="Design Partner Auto Parts",
        contact_name="Jordan Rivera",
        contact_email=f"jordan-{uuid.uuid4().hex[:8]}@example.com",
        contact_phone="+15125550142",
        service_area="Austin metro",
        password="a-long-enough-password",
        terms_version="v1",
        accepted_terms=True,
    )
    payload.update(overrides)
    return ClientSignupBody(**payload)


def _ip() -> str:
    """A fresh IP per test - the limiter is real Redis and would otherwise
    carry state between tests."""
    return f"198.51.100.{uuid.uuid4().int % 250}"


async def _seed_hub(db_session) -> uuid.UUID:
    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    return hub_id


# ---------------------------------------------------------------------------
# Signing up
# ---------------------------------------------------------------------------


async def test_a_signup_creates_a_pending_client_and_an_inactive_user(db_session, real_redis_client):
    """The core guarantee: an applicant exists but can do nothing."""
    await _seed_hub(db_session)

    result = await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)
    assert result.status == "pending"

    client = (await db_session.execute(select(Client))).scalar_one()
    assert client.signup_status == "pending"
    assert client.service_area == "Austin metro"
    assert client.terms_accepted_version == "v1"
    assert client.terms_accepted_at is not None

    user = (await db_session.execute(select(ClientUser))).scalar_one()
    # THE GATE. C4 re-checks this every request, so it alone prevents login.
    assert user.is_active is False
    assert user.role == "admin"


async def test_signup_response_leaks_no_internal_identifiers(db_session, real_redis_client):
    """An unauthenticated caller has no business learning our client ids."""
    await _seed_hub(db_session)
    result = await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)

    assert not hasattr(result, "client_id")
    assert set(result.model_dump()) == {"status", "message"}


async def test_a_pending_client_has_no_rates(db_session, real_redis_client):
    """Rates are set at approval, not signup - which is what makes an *active*
    client always billable."""
    await _seed_hub(db_session)
    await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)

    count = (
        await db_session.execute(select(func.count()).select_from(ClientRate))
    ).scalar_one()
    assert count == 0


async def test_signup_is_refused_when_no_hub_exists(db_session, real_redis_client):
    """A deployment problem, surfaced as unavailable rather than as the
    applicant's fault."""
    with pytest.raises(HTTPException) as exc:
        await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def test_terms_must_actually_be_accepted():
    """Refused rather than stored with accepted_terms=False - a client whose
    terms were never agreed looks real to ops and is one boolean from being
    missed at approval."""
    with pytest.raises(ValidationError, match="terms must be accepted"):
        _signup(accepted_terms=False)


@pytest.mark.parametrize(
    "bad", ["nope", "no at sign.com", "a b@example.com", "jordan@", "jordan@nodot"]
)
def test_an_obviously_bad_email_is_refused(bad):
    with pytest.raises(ValidationError):
        _signup(contact_email=bad)


def test_a_short_password_is_refused():
    with pytest.raises(ValidationError):
        _signup(password="short")


# ---------------------------------------------------------------------------
# The two security properties
# ---------------------------------------------------------------------------


async def test_a_duplicate_email_is_indistinguishable_from_a_fresh_signup(db_session, real_redis_client):
    """Otherwise this endpoint becomes a way to test whether a given company is
    already an LMX customer. Same status, same message, and no second client."""
    await _seed_hub(db_session)
    body = _signup()

    first = await client_signup(body, _FakeRequest(_ip()), session=db_session)
    second = await client_signup(body, _FakeRequest(_ip()), session=db_session)

    assert (first.status, first.message) == (second.status, second.message)

    clients = (await db_session.execute(select(func.count()).select_from(Client))).scalar_one()
    assert clients == 1, "the duplicate must not create a second client"


async def test_the_endpoint_is_rate_limited_per_address(db_session, real_redis_client):
    """A public write surface that anyone can reach. Unthrottled it is a way to
    bury real applicants in the ops review queue - the queue that gates whether
    anyone can dispatch our vans."""
    await _seed_hub(db_session)
    ip = _ip()

    for _ in range(MAX_SIGNUP_ATTEMPTS):
        await client_signup(_signup(), _FakeRequest(ip), session=db_session)

    with pytest.raises(HTTPException) as exc:
        await client_signup(_signup(), _FakeRequest(ip), session=db_session)
    assert exc.value.status_code == 429


async def test_the_limit_is_per_address_not_global(db_session, real_redis_client):
    """One noisy applicant must not lock out everyone else."""
    await _seed_hub(db_session)
    noisy = _ip()
    for _ in range(MAX_SIGNUP_ATTEMPTS):
        await client_signup(_signup(), _FakeRequest(noisy), session=db_session)

    result = await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)
    assert result.status == "pending"


# ---------------------------------------------------------------------------
# The approval gate
# ---------------------------------------------------------------------------


async def test_approval_sets_rates_and_activates_the_login(db_session, real_redis_client):
    """The whole point of approving here rather than at signup: an active client
    always has rates, so their orders can never price as null."""
    hub_id = await _seed_hub(db_session)
    await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)
    client = (await db_session.execute(select(Client))).scalar_one()

    result = await approve_signup(
        str(client.id),
        ApproveSignupBody(
            rates=[
                ApproveRateInput(sla_tier="T1", rate_per_drop_cents=1800),
                ApproveRateInput(sla_tier="T2", rate_per_drop_cents=1200),
            ],
            hub_id=str(hub_id),
        ),
        session=db_session,
        _admin=None,
    )

    assert result.signup_status == "active"
    assert result.rates_created == 2

    await db_session.refresh(client)
    assert client.signup_status == "active"

    user = (await db_session.execute(select(ClientUser))).scalar_one()
    await db_session.refresh(user)
    assert user.is_active is True, "approval must let them log in"

    rates = (await db_session.execute(select(ClientRate))).scalars().all()
    assert {r.sla_tier for r in rates} == {"T1", "T2"}


async def test_approval_cannot_happen_without_rates(db_session, real_redis_client):
    """Approving a client we cannot bill is the failure this endpoint exists to
    prevent, so it is refused at the schema."""
    with pytest.raises(ValidationError):
        ApproveSignupBody(rates=[])


async def test_an_unknown_tier_is_refused(db_session, real_redis_client):
    hub_id = await _seed_hub(db_session)
    await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)
    client = (await db_session.execute(select(Client))).scalar_one()

    with pytest.raises(HTTPException) as exc:
        await approve_signup(
            str(client.id),
            ApproveSignupBody(
                rates=[ApproveRateInput(sla_tier="T9", rate_per_drop_cents=100)],
                hub_id=str(hub_id),
            ),
            session=db_session,
            _admin=None,
        )
    assert exc.value.status_code == 422


async def test_approving_twice_is_idempotent(db_session, real_redis_client):
    """Two ops users working the same queue is an ordinary race, not an error -
    and it must not create a second set of rates."""
    hub_id = await _seed_hub(db_session)
    await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)
    client = (await db_session.execute(select(Client))).scalar_one()
    body = ApproveSignupBody(
        rates=[ApproveRateInput(sla_tier="T2", rate_per_drop_cents=1200)], hub_id=str(hub_id)
    )

    await approve_signup(str(client.id), body, session=db_session, _admin=None)
    again = await approve_signup(str(client.id), body, session=db_session, _admin=None)

    assert again.signup_status == "active"
    assert again.rates_created == 0
    count = (await db_session.execute(select(func.count()).select_from(ClientRate))).scalar_one()
    assert count == 1


async def test_a_rejected_applicant_stays_locked_out_and_on_record(db_session, real_redis_client):
    """Kept rather than deleted so a second application is recognisable."""
    await _seed_hub(db_session)
    await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)
    client = (await db_session.execute(select(Client))).scalar_one()

    result = await reject_signup(
        str(client.id), RejectSignupBody(reason="outside service area"),
        session=db_session, _admin=None,
    )
    assert result.signup_status == "rejected"

    await db_session.refresh(client)
    assert client.signup_status == "rejected"

    user = (await db_session.execute(select(ClientUser))).scalar_one()
    await db_session.refresh(user)
    assert user.is_active is False


async def test_a_rejected_applicant_cannot_then_be_approved(db_session, real_redis_client):
    await _seed_hub(db_session)
    await client_signup(_signup(), _FakeRequest(_ip()), session=db_session)
    client = (await db_session.execute(select(Client))).scalar_one()
    await reject_signup(str(client.id), RejectSignupBody(), session=db_session, _admin=None)

    with pytest.raises(HTTPException) as exc:
        await approve_signup(
            str(client.id),
            ApproveSignupBody(rates=[ApproveRateInput(sla_tier="T2", rate_per_drop_cents=1200)]),
            session=db_session,
            _admin=None,
        )
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# The ops queue
# ---------------------------------------------------------------------------


async def test_the_queue_shows_applicants_oldest_first(db_session, real_redis_client):
    """Nobody should be left behind a newer application."""
    await _seed_hub(db_session)
    for name in ("First Co", "Second Co", "Third Co"):
        await client_signup(_signup(company_name=name), _FakeRequest(_ip()), session=db_session)

    queue = await list_signups(session=db_session, _admin=None)
    assert [s.company_name for s in queue] == ["First Co", "Second Co", "Third Co"]


async def test_the_queue_carries_what_ops_needs_to_decide(db_session, real_redis_client):
    """Service area and a contact are the whole basis for approving or not."""
    await _seed_hub(db_session)
    await client_signup(
        _signup(contact_email="jordan@designpartner.example", contact_name="Jordan Rivera"),
        _FakeRequest(_ip()),
        session=db_session,
    )

    entry = (await list_signups(session=db_session, _admin=None))[0]
    assert entry.service_area == "Austin metro"
    assert entry.contact_email == "jordan@designpartner.example"
    assert entry.contact_name == "Jordan Rivera"
    assert entry.contact_phone == "+15125550142"
    assert entry.terms_version == "v1"


async def test_the_queue_defaults_to_pending_only(db_session, real_redis_client):
    hub_id = await _seed_hub(db_session)
    await client_signup(_signup(company_name="Approved Co"), _FakeRequest(_ip()), session=db_session)
    await client_signup(_signup(company_name="Waiting Co"), _FakeRequest(_ip()), session=db_session)

    approved = (
        await db_session.execute(select(Client).where(Client.name == "Approved Co"))
    ).scalar_one()
    await approve_signup(
        str(approved.id),
        ApproveSignupBody(
            rates=[ApproveRateInput(sla_tier="T2", rate_per_drop_cents=1200)], hub_id=str(hub_id)
        ),
        session=db_session,
        _admin=None,
    )

    queue = await list_signups(session=db_session, _admin=None)
    assert [s.company_name for s in queue] == ["Waiting Co"]

    active = await list_signups(status="active", session=db_session, _admin=None)
    assert [s.company_name for s in active] == ["Approved Co"]
