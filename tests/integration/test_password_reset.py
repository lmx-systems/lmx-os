"""
Client password reset (docs/ROADMAP.md L14).

Before this, a client who forgot their password had no way back in - only an
admin at their own company could reset it, so a company with one admin (which is
every company on its first day) was locked out until someone at LMX ran a script
by hand. That is an outage, not a support process.

Most of what follows is security rather than feature. The properties under test:

  - **Enumeration safety.** An unknown address, a pending applicant, a
    deactivated user and a real reset are indistinguishable from outside. If they
    weren't, this endpoint would tell anyone which businesses bank with LMX.
  - **Single use.** A forwarded or logged link cannot be replayed.
  - **The raw token is never stored.** Redis holds a hash, so a dump yields
    nothing usable.
  - **A pending applicant gets nothing** - a link would confirm their application
    exists, and would grant no access anyway.
"""
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.public_routes import (
    confirm_password_reset,
    request_password_reset,
)
from app.client_auth.password_reset import (
    MAX_RESET_REQUESTS,
    PasswordResetStore,
    _token_key,
)
from app.client_auth.passwords import verify_password
from app.messaging.email_client import EmailClient
from app.models.client import Client
from app.models.client_user import CLIENT_ADMIN_ROLE, ClientUser
from app.models.hub import Hub
from app.redis_client import get_client as get_redis
from app.schemas.signup import (
    PasswordResetConfirmBody,
    PasswordResetRequestBody,
)

pytestmark = pytest.mark.integration


class RecordingEmailClient(EmailClient):
    engine_name = "recording"

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        self.sent.append({"to": to, "subject": subject, "body": body})
        return True


class _Request:
    def __init__(self, ip: str) -> None:
        self.client = type("C", (), {"host": ip})()


def _ip() -> str:
    return f"192.0.2.{uuid.uuid4().int % 250}"


@pytest.fixture
def mailer(monkeypatch):
    recorder = RecordingEmailClient()
    import app.messaging.client_emails as emails

    monkeypatch.setattr(emails, "get_email_client", lambda: recorder)
    return recorder


async def _seed_user(db_session, *, is_active: bool = True) -> ClientUser:
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", signup_status="active")
    )
    await db_session.commit()
    user = ClientUser(
        client_id=client_id,
        email=f"jordan-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="not-a-real-hash",
        name="Jordan Rivera",
        role=CLIENT_ADMIN_ROLE,
        is_active=is_active,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _token_from(mailer: RecordingEmailClient) -> str:
    body = mailer.sent[-1]["body"]
    marker = "token="
    start = body.index(marker) + len(marker)
    return body[start:].split()[0].strip()


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_a_reset_link_lets_a_locked_out_user_set_a_new_password(
    db_session, real_redis_client, mailer
):
    user = await _seed_user(db_session)

    await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )
    token = _token_from(mailer)

    result = await confirm_password_reset(
        PasswordResetConfirmBody(token=token, new_password="a-brand-new-password"),
        session=db_session,
    )

    assert "changed" in result.message
    await db_session.refresh(user)
    assert verify_password("a-brand-new-password", user.password_hash)


async def test_the_email_carries_a_link_and_states_the_expiry(db_session, real_redis_client, mailer):
    """Stating the hour stops a support call from someone using a stale link."""
    user = await _seed_user(db_session)
    await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )

    body = mailer.sent[-1]["body"]
    assert "reset-password?token=" in body
    assert "hour" in body
    # What to do if you didn't ask for it - the sentence someone actually needs
    # when this arrives because an attacker typed their address.
    assert "wasn't you" in body


# ---------------------------------------------------------------------------
# Enumeration safety
# ---------------------------------------------------------------------------


async def test_an_unknown_address_gets_the_same_answer_as_a_real_one(
    db_session, real_redis_client, mailer
):
    """Otherwise this endpoint tells anyone which businesses are LMX clients."""
    user = await _seed_user(db_session)

    real = await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )
    unknown = await request_password_reset(
        PasswordResetRequestBody(email="nobody-here@example.com"),
        _Request(_ip()),
        session=db_session,
    )

    assert real.message == unknown.message


async def test_an_unknown_address_is_not_emailed(db_session, real_redis_client, mailer):
    await request_password_reset(
        PasswordResetRequestBody(email="nobody-here@example.com"),
        _Request(_ip()),
        session=db_session,
    )
    assert mailer.sent == []


async def test_a_pending_applicant_gets_no_link_but_the_same_answer(
    db_session, real_redis_client, mailer
):
    """A link would confirm their application exists - and would grant nothing
    anyway, since C4 re-checks is_active on every request."""
    user = await _seed_user(db_session, is_active=False)

    result = await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )

    assert mailer.sent == []
    assert "If that address has an LMX account" in result.message


async def test_hitting_the_per_email_limit_looks_identical_from_outside(
    db_session, real_redis_client, mailer
):
    """A 429 here would leak that this address has been asked about repeatedly,
    which is itself a signal about whether it exists."""
    user = await _seed_user(db_session)

    for _ in range(MAX_RESET_REQUESTS):
        await request_password_reset(
            PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
        )
    sent_before = len(mailer.sent)

    result = await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )

    assert "If that address has an LMX account" in result.message
    assert len(mailer.sent) == sent_before, "over the cap, nothing more is sent"


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


async def test_a_token_cannot_be_used_twice(db_session, real_redis_client, mailer):
    """A forwarded link, or one sitting in a proxy log, must not be replayable."""
    user = await _seed_user(db_session)
    await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )
    token = _token_from(mailer)

    await confirm_password_reset(
        PasswordResetConfirmBody(token=token, new_password="first-new-password"),
        session=db_session,
    )

    with pytest.raises(HTTPException) as exc:
        await confirm_password_reset(
            PasswordResetConfirmBody(token=token, new_password="second-new-password"),
            session=db_session,
        )
    assert exc.value.status_code == 400

    await db_session.refresh(user)
    assert verify_password("first-new-password", user.password_hash)


async def test_the_raw_token_is_never_stored_in_redis(db_session, real_redis_client, mailer):
    """Redis holds a hash, so a dump or a stray KEYS * yields nothing usable."""
    user = await _seed_user(db_session)
    await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )
    token = _token_from(mailer)

    redis = get_redis()
    # The hashed key exists...
    assert await redis.get(_token_key(token)) == str(user.id)
    # ...and no key anywhere contains the raw token.
    keys = await redis.keys("client_auth:pwreset:*")
    assert all(token not in key for key in keys)


@pytest.mark.parametrize("bad", ["x" * 20, "not-a-real-token-value-here"])
async def test_a_bogus_token_is_refused(db_session, real_redis_client, bad):
    with pytest.raises(HTTPException) as exc:
        await confirm_password_reset(
            PasswordResetConfirmBody(token=bad, new_password="a-brand-new-password"),
            session=db_session,
        )
    assert exc.value.status_code == 400


async def test_a_wrong_token_and_a_used_token_are_indistinguishable(
    db_session, real_redis_client, mailer
):
    """Nothing a legitimate user does differs on knowing which it was - they
    request another link either way."""
    user = await _seed_user(db_session)
    await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )
    token = _token_from(mailer)
    await confirm_password_reset(
        PasswordResetConfirmBody(token=token, new_password="first-new-password"),
        session=db_session,
    )

    used, wrong = None, None
    try:
        await confirm_password_reset(
            PasswordResetConfirmBody(token=token, new_password="x" * 12), session=db_session
        )
    except HTTPException as exc:
        used = (exc.status_code, exc.detail)
    try:
        await confirm_password_reset(
            PasswordResetConfirmBody(token="y" * 40, new_password="x" * 12), session=db_session
        )
    except HTTPException as exc:
        wrong = (exc.status_code, exc.detail)

    assert used == wrong


async def test_a_user_deactivated_after_the_link_was_sent_cannot_reset(
    db_session, real_redis_client, mailer
):
    """The token outlives the state it was issued against, so is_active has to be
    rechecked at redemption rather than only at issuance."""
    user = await _seed_user(db_session)
    await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )
    token = _token_from(mailer)

    user.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await confirm_password_reset(
            PasswordResetConfirmBody(token=token, new_password="a-brand-new-password"),
            session=db_session,
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Getting back in afterwards
# ---------------------------------------------------------------------------


async def test_a_reset_clears_the_login_lockout(db_session, real_redis_client, mailer):
    """They were probably locked out by failed attempts on the way here -
    otherwise a correct new password still bounces."""
    from app.client_auth.login_rate_limit import MAX_LOGIN_ATTEMPTS, LoginRateLimiter

    user = await _seed_user(db_session)
    limiter = LoginRateLimiter()
    for _ in range(MAX_LOGIN_ATTEMPTS):
        await limiter.check_and_increment(user.email)

    await request_password_reset(
        PasswordResetRequestBody(email=user.email), _Request(_ip()), session=db_session
    )
    await confirm_password_reset(
        PasswordResetConfirmBody(token=_token_from(mailer), new_password="a-brand-new-password"),
        session=db_session,
    )

    # No longer locked out - this would raise if the counter had survived.
    await limiter.check_and_increment(user.email)


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def test_a_short_new_password_is_refused():
    """A reset is not the moment to relax the rule applied at signup."""
    with pytest.raises(ValidationError):
        PasswordResetConfirmBody(token="x" * 20, new_password="short")


def test_a_malformed_email_is_refused():
    with pytest.raises(ValidationError):
        PasswordResetRequestBody(email="not-an-email")


async def test_the_store_issues_distinct_tokens(real_redis_client):
    store = PasswordResetStore()
    a = await store.issue("user-a")
    b = await store.issue("user-b")
    assert a != b
    assert await store.consume(a) == "user-a"
    assert await store.consume(b) == "user-b"
