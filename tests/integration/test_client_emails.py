"""
Transactional email for the signup funnel (docs/LMX_LINK_PLAN.md).

Without these, LMX Link is not self-serve: the signup page promises "we'll be in
touch" and nothing is, and approval flips a login active that nobody tells the
client about. An activated account nobody knows exists is the same as no account.

Three properties get the most attention, because each is a way the feature fails
quietly rather than loudly:

  - **A duplicate signup must not email.** Mailing the real owner of an address
    "we got your signup" when they didn't submit one both alarms them and
    confirms to whoever did submit it that the address is registered - the exact
    disclosure the pre-charged rate limiter exists to prevent.
  - **A mail failure must not fail the request.** Blocking an approval because
    SMTP is down is worse than a client who has to be phoned.
  - **The approval email has to say where to sign in.** One that doesn't is
    barely a notification.
"""
import uuid

import pytest
from sqlalchemy import select

from app.api.admin_routes import approve_signup
from app.api.public_routes import client_signup
from app.messaging.email_client import EmailClient, StubEmailClient, get_email_client
from app.models.client import Client
from app.models.client_user import ClientUser
from app.models.hub import Hub
from app.schemas.signup import ApproveRateInput, ApproveSignupBody, ClientSignupBody

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _signup_open(published_terms):
    """Every signup in this module runs against published legal documents.

    Without this the endpoint 503s: it will not record assent to a draft. See
    tests/integration/conftest.py::published_terms, and test_legal_documents.py for
    the tests of the closed door itself.
    """


class RecordingEmailClient(EmailClient):
    engine_name = "recording"

    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        self.sent.append({"to": to, "subject": subject, "body": body})
        return self.succeed


class ExplodingEmailClient(EmailClient):
    """A provider that is genuinely broken, not merely unconfigured."""

    engine_name = "exploding"

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        raise RuntimeError("SMTP is down")


class _Request:
    def __init__(self, ip: str) -> None:
        self.client = type("C", (), {"host": ip})()


def _ip() -> str:
    return f"203.0.113.{uuid.uuid4().int % 250}"


def _signup(**overrides) -> ClientSignupBody:
    payload = dict(
        company_name="Midtown Auto Parts",
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


@pytest.fixture
def mailer(monkeypatch):
    """Patch both call sites - the two flows live in different route modules."""
    recorder = RecordingEmailClient()
    import app.messaging.client_emails as emails

    monkeypatch.setattr(emails, "get_email_client", lambda: recorder)
    return recorder


async def _seed_hub(db_session) -> uuid.UUID:
    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    return hub_id


# ---------------------------------------------------------------------------
# Application received
# ---------------------------------------------------------------------------


async def test_a_new_signup_is_acknowledged_by_email(db_session, real_redis_client, mailer):
    await _seed_hub(db_session)
    body = _signup()

    await client_signup(body, _Request(_ip()), session=db_session)

    assert len(mailer.sent) == 1
    assert mailer.sent[0]["to"] == body.contact_email
    assert "Midtown Auto Parts" in mailer.sent[0]["body"]
    assert "Jordan Rivera" in mailer.sent[0]["body"]


async def test_a_duplicate_signup_sends_no_email(db_session, real_redis_client, mailer):
    """The disclosure this avoids: telling the real owner of an address that
    somebody tried to sign up as them, and telling the submitter that the
    address is already registered."""
    await _seed_hub(db_session)
    body = _signup()

    await client_signup(body, _Request(_ip()), session=db_session)
    mailer.sent.clear()

    await client_signup(body, _Request(_ip()), session=db_session)

    assert mailer.sent == [], "a duplicate must be silent"


async def test_a_signup_still_succeeds_when_mail_is_broken(db_session, real_redis_client, monkeypatch):
    """A mail outage must not lose a real application."""
    import app.messaging.client_emails as emails

    monkeypatch.setattr(emails, "get_email_client", lambda: ExplodingEmailClient())
    await _seed_hub(db_session)

    result = await client_signup(_signup(), _Request(_ip()), session=db_session)

    assert result.status == "pending"
    client = (await db_session.execute(select(Client))).scalar_one()
    assert client.signup_status == "pending", "the application is recorded regardless"


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


async def test_approval_emails_the_client_with_a_sign_in_link(db_session, real_redis_client, mailer):
    """The most important email here - approval activates their login, and an
    active account nobody knows about is the same as no account."""
    hub_id = await _seed_hub(db_session)
    await client_signup(_signup(), _Request(_ip()), session=db_session)
    client = (await db_session.execute(select(Client))).scalar_one()
    mailer.sent.clear()

    await approve_signup(
        str(client.id),
        ApproveSignupBody(
            rates=[ApproveRateInput(sla_tier="T2", rate_per_drop_cents=1200)], hub_id=str(hub_id)
        ),
        session=db_session,
        _admin=None,
    )

    assert len(mailer.sent) == 1
    sent = mailer.sent[0]
    assert "approved" in sent["subject"].lower()
    # A notification that doesn't say where to go is barely a notification.
    assert "http" in sent["body"]
    assert "Midtown Auto Parts" in sent["body"]


async def test_approval_goes_to_the_person_who_signed_up(db_session, real_redis_client, mailer):
    hub_id = await _seed_hub(db_session)
    body = _signup()
    await client_signup(body, _Request(_ip()), session=db_session)
    client = (await db_session.execute(select(Client))).scalar_one()
    mailer.sent.clear()

    await approve_signup(
        str(client.id),
        ApproveSignupBody(
            rates=[ApproveRateInput(sla_tier="T2", rate_per_drop_cents=1200)], hub_id=str(hub_id)
        ),
        session=db_session,
        _admin=None,
    )

    assert mailer.sent[0]["to"] == body.contact_email


async def test_approval_stands_even_when_mail_fails(db_session, real_redis_client, monkeypatch):
    """Blocking onboarding on a mail outage would be worse than a client who has
    to be phoned - and the ops panel still shows them active, which is what lets
    someone notice."""
    import app.messaging.client_emails as emails

    hub_id = await _seed_hub(db_session)
    await client_signup(_signup(), _Request(_ip()), session=db_session)
    client = (await db_session.execute(select(Client))).scalar_one()

    monkeypatch.setattr(emails, "get_email_client", lambda: ExplodingEmailClient())

    result = await approve_signup(
        str(client.id),
        ApproveSignupBody(
            rates=[ApproveRateInput(sla_tier="T2", rate_per_drop_cents=1200)], hub_id=str(hub_id)
        ),
        session=db_session,
        _admin=None,
    )

    assert result.signup_status == "active"
    await db_session.refresh(client)
    assert client.signup_status == "active"
    user = (await db_session.execute(select(ClientUser))).scalar_one()
    await db_session.refresh(user)
    assert user.is_active is True, "they can still sign in - they just weren't told"


async def test_a_rejected_applicant_is_not_emailed(db_session, real_redis_client, mailer):
    """Deliberately silent. Whether and how to tell someone they were declined is
    a business decision, not an engineering default - and a templated rejection is
    the wrong way to have it made for you."""
    from app.api.admin_routes import reject_signup
    from app.schemas.signup import RejectSignupBody

    await _seed_hub(db_session)
    await client_signup(_signup(), _Request(_ip()), session=db_session)
    client = (await db_session.execute(select(Client))).scalar_one()
    mailer.sent.clear()

    await reject_signup(
        str(client.id), RejectSignupBody(reason="outside service area"),
        session=db_session, _admin=None,
    )

    assert mailer.sent == []


# ---------------------------------------------------------------------------
# The client itself
# ---------------------------------------------------------------------------


def test_unconfigured_smtp_falls_back_to_the_stub():
    """Same unconfigured-to-stub convention as Twilio - no SMTP account exists
    yet, and every flow has to remain testable without one."""
    assert isinstance(get_email_client(), StubEmailClient)


async def test_the_stub_reports_that_nothing_was_sent():
    """Returning True would let a caller believe a client was notified when
    nobody was."""
    assert await StubEmailClient().send(to="a@b.com", subject="s", body="b") is False
