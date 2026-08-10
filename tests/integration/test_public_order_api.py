"""
The public order API and its API keys (docs/ORDER_API.md, LMX Link T5) against real
Postgres + Redis.

T5's exit criterion is *"an external system POSTs an order and receives status
callbacks without LMX assistance"*. F4 shipped the callbacks. This is the POST, and
before it existed the only inbound route sat behind the ops-user middleware - so
wiring a client's POS to LMX meant handing that POS an ops login that can also run
dispatch cycles and reach `/admin`.

**The test that matters most is `test_a_key_cannot_submit_an_order_for_another
_client`.** The pre-existing ingestion endpoint takes `client_id` in the path; the
whole reason this is a new prefix rather than that endpoint with its auth relaxed is
that a path-supplied client id plus a client credential is one client billing another.

Second most important: `test_resubmitting_the_same_reference_does_not_create_a
_second_order`. An external caller whose POST times out cannot know whether we got
it, so it retries - and a duplicate here is a second van to a real address, billed
twice.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.client_routes import (
    create_my_api_key,
    list_my_api_keys,
    revoke_my_api_key,
)
from app.api.public_api_routes import get_order, submit_order
from app.client_api.dependencies import get_api_client
from app.client_auth.dependencies import AuthedClient
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.models.client import Client
from app.models.client_api_key import ClientApiKey, hash_api_key, mint_api_key
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.schemas.public_api import ApiOrderBody
from app.schemas.webhooks import ApiKeyBody

pytestmark = pytest.mark.integration

PICKUP_LAT, PICKUP_LNG = 30.264642, -97.730218


class _FakeGeocoder(BaseGeocoder):
    provider_name = "fake"

    def __init__(self, resolves: bool = True) -> None:
        self.resolves = resolves

    async def geocode(self, address: str) -> GeocodeResult | None:
        if not self.resolves:
            return None
        return GeocodeResult(
            lat=PICKUP_LAT, lng=PICKUP_LNG, display_name=address, provider="fake"
        )


@pytest.fixture(autouse=True)
def _geocoder(monkeypatch):
    """The route calls get_geocoder() itself, so the seam is the module attribute.

    Nominatim would otherwise be hit for real - and geocoding has no stub fallback by
    design (app/geocoding/base.py), so there is nothing to fall back to.
    """
    import app.api.public_api_routes as routes

    monkeypatch.setattr(routes, "get_geocoder", lambda: _FakeGeocoder())


def _admin(client_id) -> AuthedClient:
    return AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="ops@distributor.test",
        name="Dana",
        role="admin",
    )


async def _seed_client(db_session, *, signup_status="active", active=True):
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id,
            hub_id=hub_id,
            name="Design Partner",
            pos_system="client_api",
            signup_status=signup_status,
            active=active,
        )
    )
    await db_session.commit()
    return hub_id, client_id


async def _key_for(db_session, client_id) -> str:
    token, token_hash, prefix = mint_api_key()
    db_session.add(
        ClientApiKey(client_id=client_id, token_hash=token_hash, token_prefix=prefix)
    )
    await db_session.commit()
    return token


def _order_body(**overrides) -> ApiOrderBody:
    payload = dict(
        your_order_ref=f"POS-{uuid.uuid4().hex[:8]}",
        pickup_address="1200 E 6th St, Austin TX",
        delivery_address="900 Congress Ave, Austin TX",
        delivery_contact_phone="+15125550101",
        deliver_by=datetime.now(timezone.utc) + timedelta(hours=3),
    )
    payload.update(overrides)
    return ApiOrderBody(**payload)


async def _authed(db_session, token) -> object:
    return await get_api_client(x_lmx_api_key=token, session=db_session)


# ---------------------------------------------------------------------------
# The scoping property
# ---------------------------------------------------------------------------


async def test_the_client_comes_from_the_key_not_the_request(db_session, real_redis_client):
    """There is deliberately no way to name a client in the request at all."""
    hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)

    api_client = await _authed(db_session, token)

    assert api_client.client_id == str(client_id)
    assert api_client.hub_id == str(hub_id)
    # And the request body has nowhere to put either one.
    assert "client_id" not in ApiOrderBody.model_fields
    assert "hub_id" not in ApiOrderBody.model_fields


async def test_a_key_cannot_submit_an_order_for_another_client(db_session, real_redis_client):
    """**The reason this is a new prefix rather than /ingestion with its auth
    relaxed.** That endpoint takes client_id in the path, so a client credential plus
    a path-supplied client id is one client billing and dispatching for another."""
    _hub_a, client_a = await _seed_client(db_session)
    _hub_b, client_b = await _seed_client(db_session)
    token_a = await _key_for(db_session, client_a)

    result = await submit_order(
        _order_body(),
        api_client=await _authed(db_session, token_a),
        session=db_session,
    )

    order = await db_session.get(Order, uuid.UUID(result.order_id))
    assert order.client_id == client_a
    assert order.client_id != client_b


async def test_an_order_lands_in_the_hold_queue_like_any_other(db_session, real_redis_client):
    """The whole architecture claim: a new adapter needs no core change."""
    from app.batch_queue.store import HoldQueueStore

    hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)

    await submit_order(
        _order_body(), api_client=await _authed(db_session, token), session=db_session
    )

    held = await HoldQueueStore().get_all(str(hub_id))
    assert len(held) == 1
    assert held[0].shop_lat == pytest.approx(PICKUP_LAT)


async def test_api_orders_are_tagged_distinctly(db_session, real_redis_client):
    """So I1/I4's analytics don't treat three intake paths as one."""
    _hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)

    result = await submit_order(
        _order_body(), api_client=await _authed(db_session, token), session=db_session
    )

    order = await db_session.get(Order, uuid.UUID(result.order_id))
    assert order.source_system == "client_api"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_resubmitting_the_same_reference_does_not_create_a_second_order(
    db_session, real_redis_client
):
    """**A duplicate here is a second van to a real address, billed twice.** A caller
    whose POST times out cannot know whether we got it, so without this its only safe
    options are to never retry - silently losing orders - or to reconcile by hand."""
    _hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)
    body = _order_body()

    first = await submit_order(
        body, api_client=await _authed(db_session, token), session=db_session
    )
    second = await submit_order(
        body, api_client=await _authed(db_session, token), session=db_session
    )

    assert first.order_id == second.order_id
    assert first.duplicate is False
    assert second.duplicate is True

    count = (
        await db_session.execute(select(func.count()).select_from(Order))
    ).scalar_one()
    assert count == 1


async def test_two_clients_may_use_the_same_reference(db_session, real_redis_client):
    """Internal numbering is theirs, and it collides across companies. Without the
    client scope on the lookup, one client could read - and idempotently "recover" -
    another's order by guessing a reference."""
    _hub_a, client_a = await _seed_client(db_session)
    _hub_b, client_b = await _seed_client(db_session)
    token_a = await _key_for(db_session, client_a)
    token_b = await _key_for(db_session, client_b)
    body = _order_body(your_order_ref="INVOICE-1001")

    first = await submit_order(
        body, api_client=await _authed(db_session, token_a), session=db_session
    )
    second = await submit_order(
        body, api_client=await _authed(db_session, token_b), session=db_session
    )

    assert first.order_id != second.order_id
    assert second.duplicate is False


async def test_an_order_can_be_looked_up_by_the_callers_own_reference(
    db_session, real_redis_client
):
    """By their reference rather than ours - an API queryable only by an id we
    invented forces them to store a mapping they shouldn't need. Also the
    reconciliation path for a window when their webhook endpoint was paused."""
    _hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)
    body = _order_body(your_order_ref="POS-778")

    created = await submit_order(
        body, api_client=await _authed(db_session, token), session=db_session
    )
    fetched = await get_order(
        "POS-778", api_client=await _authed(db_session, token), session=db_session
    )

    assert fetched.order_id == created.order_id
    assert fetched.status == OrderStatus.held.value


async def test_one_client_cannot_read_anothers_order_by_reference(
    db_session, real_redis_client
):
    _hub_a, client_a = await _seed_client(db_session)
    _hub_b, client_b = await _seed_client(db_session)
    token_a = await _key_for(db_session, client_a)
    token_b = await _key_for(db_session, client_b)

    await submit_order(
        _order_body(your_order_ref="SECRET-1"),
        api_client=await _authed(db_session, token_a),
        session=db_session,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_order(
            "SECRET-1", api_client=await _authed(db_session, token_b), session=db_session
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def test_no_key_is_refused(db_session, real_redis_client):
    with pytest.raises(HTTPException) as exc_info:
        await get_api_client(x_lmx_api_key=None, session=db_session)
    assert exc_info.value.status_code == 401


async def test_an_unknown_key_is_refused(db_session, real_redis_client):
    with pytest.raises(HTTPException) as exc_info:
        await get_api_client(x_lmx_api_key="lmxk_live_nope", session=db_session)
    assert exc_info.value.status_code == 401


async def test_a_revoked_key_stops_working_immediately(db_session, real_redis_client):
    _hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)
    await _authed(db_session, token)  # works

    key = (
        await db_session.execute(
            select(ClientApiKey).where(ClientApiKey.token_hash == hash_api_key(token))
        )
    ).scalar_one()
    key.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _authed(db_session, token)
    assert exc_info.value.status_code == 401


async def test_a_pending_client_cannot_submit_orders(db_session, real_redis_client):
    """A key issued before approval - or an applicant who was never approved - must
    not be able to dispatch a van. Rechecked per request, not captured at key
    creation, so a decision takes effect immediately."""
    _hub_id, client_id = await _seed_client(db_session, signup_status="pending")
    token = await _key_for(db_session, client_id)

    with pytest.raises(HTTPException) as exc_info:
        await _authed(db_session, token)
    assert exc_info.value.status_code == 401


async def test_a_deactivated_client_cannot_submit_orders(db_session, real_redis_client):
    _hub_id, client_id = await _seed_client(db_session, active=False)
    token = await _key_for(db_session, client_id)

    with pytest.raises(HTTPException) as exc_info:
        await _authed(db_session, token)
    assert exc_info.value.status_code == 401


async def test_every_rejection_looks_the_same(db_session, real_redis_client):
    """Distinguishing "unknown" from "revoked" from "your account is suspended" tells
    a prober which of their guesses was once real."""
    _hub_id, pending_client = await _seed_client(db_session, signup_status="pending")
    pending_token = await _key_for(db_session, pending_client)

    details = set()
    for token in (None, "lmxk_live_garbage", pending_token):
        with pytest.raises(HTTPException) as exc_info:
            await get_api_client(x_lmx_api_key=token, session=db_session)
        details.add((exc_info.value.status_code, exc_info.value.detail))

    assert len(details) == 1


async def test_the_key_is_never_stored_in_the_clear(db_session, real_redis_client):
    """Unlike the outbound webhook secret, which we must sign with, this is only ever
    verified - so a database disclosure should leak nothing usable."""
    _hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)

    key = (
        await db_session.execute(
            select(ClientApiKey).where(ClientApiKey.client_id == client_id)
        )
    ).scalar_one()

    assert token not in (key.token_hash, key.token_prefix)
    assert key.token_hash == hash_api_key(token)
    assert len(key.token_hash) == 64
    # The prefix is short enough to be useless as a credential.
    assert len(key.token_prefix) < 20


async def test_using_a_key_records_that_it_was_used(db_session, real_redis_client):
    """The field rotation depends on: which of two keys is my system actually using?"""
    _hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)

    await _authed(db_session, token)
    await db_session.commit()

    key = (
        await db_session.execute(
            select(ClientApiKey).where(ClientApiKey.token_hash == hash_api_key(token))
        )
    ).scalar_one()
    await db_session.refresh(key)
    assert key.last_used_at is not None


async def test_the_key_is_rate_limited_per_key_not_per_ip(
    db_session, real_redis_client, monkeypatch
):
    """Per key because a client's integration runs from one address and several
    clients can share a NAT - and because one client's runaway job must not spend the
    capacity other clients' orders need."""
    from app.client_api import rate_limit

    monkeypatch.setattr(rate_limit, "MAX_REQUESTS", 3)
    _hub_id, client_a = await _seed_client(db_session)
    _hub_b, client_b = await _seed_client(db_session)
    token_a = await _key_for(db_session, client_a)
    token_b = await _key_for(db_session, client_b)

    for _ in range(3):
        await _authed(db_session, token_a)
    with pytest.raises(HTTPException) as exc_info:
        await _authed(db_session, token_a)
    assert exc_info.value.status_code == 429

    # The other client is unaffected.
    await _authed(db_session, token_b)


async def test_the_public_api_is_exempt_from_ops_auth_but_not_from_auth(db_session):
    """The prefix had to be added to EXEMPT_PREFIXES; the point is that it carries its
    own credential check rather than none."""
    from app.ops_auth.middleware import _is_exempt

    assert _is_exempt("/api/v1/orders")
    # And the pre-existing path-based ingestion endpoint is still ops-only, which is
    # exactly why this is a separate prefix.
    assert not _is_exempt("/ingestion/hub/client/epicor")


# ---------------------------------------------------------------------------
# Refusals that are the caller's to fix
# ---------------------------------------------------------------------------


async def test_an_unresolvable_pickup_address_is_a_422_not_a_guess(
    db_session, real_redis_client, monkeypatch
):
    """A wrong coordinate is worse than none - it sends a real van to the wrong
    place (app/geocoding/base.py)."""
    import app.api.public_api_routes as routes

    monkeypatch.setattr(routes, "get_geocoder", lambda: _FakeGeocoder(resolves=False))
    _hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)

    with pytest.raises(HTTPException) as exc_info:
        await submit_order(
            _order_body(pickup_address="asdfghjkl qwerty"),
            api_client=await _authed(db_session, token),
            session=db_session,
        )
    assert exc_info.value.status_code == 422
    assert "couldn't find that pickup address" in exc_info.value.detail


async def test_the_caller_cannot_set_their_own_sla(db_session, real_redis_client):
    """`deliver_by` is advisory. LMX classifies the tier and `collect_by` is the
    commitment - a caller writing a tighter time must not be able to buy a faster tier
    for free, which is `sla_owner` doing its job (§1.3)."""
    _hub_id, client_id = await _seed_client(db_session)
    token = await _key_for(db_session, client_id)

    result = await submit_order(
        _order_body(deliver_by=datetime.now(timezone.utc) + timedelta(minutes=2)),
        api_client=await _authed(db_session, token),
        session=db_session,
    )

    order = await db_session.get(Order, uuid.UUID(result.order_id))
    assert order.sla_owner == "LMX"
    assert result.collect_by is not None


# ---------------------------------------------------------------------------
# Key management from the portal
# ---------------------------------------------------------------------------


async def test_the_token_is_returned_exactly_once(db_session, real_redis_client):
    _hub_id, client_id = await _seed_client(db_session)
    admin = _admin(client_id)

    created = await create_my_api_key(
        ApiKeyBody(description="warehouse system"), client=admin, session=db_session
    )
    assert created.token.startswith("lmxk_live_")

    listed = await list_my_api_keys(client=admin, session=db_session)
    assert len(listed) == 1
    assert not hasattr(listed[0], "token")
    # The prefix is enough to tell two keys apart, which is what makes rotation safe.
    assert created.token.startswith(listed[0].token_prefix)


async def test_a_minted_key_actually_authenticates(db_session, real_redis_client):
    """End to end: the portal mints it, the client's system uses it."""
    _hub_id, client_id = await _seed_client(db_session)

    created = await create_my_api_key(
        ApiKeyBody(), client=_admin(client_id), session=db_session
    )
    api_client = await _authed(db_session, created.token)

    assert api_client.client_id == str(client_id)


async def test_several_live_keys_are_allowed_so_rotation_is_possible(
    db_session, real_redis_client
):
    """A single key cannot be rotated without downtime - the client would have to
    revoke and re-deploy in the same instant."""
    _hub_id, client_id = await _seed_client(db_session)
    admin = _admin(client_id)

    old = await create_my_api_key(ApiKeyBody(description="old"), client=admin, session=db_session)
    new = await create_my_api_key(ApiKeyBody(description="new"), client=admin, session=db_session)

    assert (await _authed(db_session, old.token)).client_id == str(client_id)
    assert (await _authed(db_session, new.token)).client_id == str(client_id)

    await revoke_my_api_key(old.api_key_id, client=admin, session=db_session)

    with pytest.raises(HTTPException):
        await _authed(db_session, old.token)
    assert (await _authed(db_session, new.token)).client_id == str(client_id)


async def test_a_client_cannot_revoke_another_clients_key(db_session, real_redis_client):
    _hub_a, client_a = await _seed_client(db_session)
    _hub_b, client_b = await _seed_client(db_session)
    theirs = await create_my_api_key(
        ApiKeyBody(), client=_admin(client_b), session=db_session
    )

    assert await list_my_api_keys(client=_admin(client_a), session=db_session) == []
    with pytest.raises(HTTPException) as exc_info:
        await revoke_my_api_key(
            theirs.api_key_id, client=_admin(client_a), session=db_session
        )
    assert exc_info.value.status_code == 404


async def test_a_revoked_key_keeps_its_record(db_session, real_redis_client):
    """Deactivated rather than deleted - a revocation is exactly the kind of event
    someone asks about afterwards."""
    _hub_id, client_id = await _seed_client(db_session)
    admin = _admin(client_id)
    created = await create_my_api_key(ApiKeyBody(), client=admin, session=db_session)

    revoked = await revoke_my_api_key(created.api_key_id, client=admin, session=db_session)

    assert revoked.is_active is False
    assert revoked.revoked_at is not None
    assert len(await list_my_api_keys(client=admin, session=db_session)) == 1
