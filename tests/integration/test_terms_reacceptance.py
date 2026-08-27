"""
Re-accepting the terms after a version bump (docs/ROADMAP.md L8).

`POST /public/signup` has always compared an applicant's version against the current
one, so publishing a new version closed the front door until people accepted it.
**Nothing did the equivalent for clients already through it** - so a bump left existing
clients placing orders under terms they had never seen, which is the single case the
version column exists to make impossible.

Clause 11 of the terms is the authority for gating rather than merely prompting: *"we
may ask you to accept the new version before you place further orders."*

The tests that carry the design:

  - **`test_reads_keep_working_when_terms_go_stale`.** The gate is on order *creation*.
    A client whose terms went stale mid-shift must still watch deliveries already in
    flight and find an order a customer is ringing about; withholding that punishes
    them for our change.
  - **`test_a_member_cannot_accept`.** Binding the company to a contract is not the same
    act as reading an order, and clause 1 has the accepting party agreeing "on behalf of
    your business".
  - **`test_a_pending_client_still_gets_the_uniform_401`.** The regression I introduced
    and then fixed: the API dependency answers an inactive client with the same 401 as an
    unknown key, and a distinguishable 409 ahead of that check would confirm to a
    key-holder that their key is real.
  - **`test_drafts_oblige_nobody`.** Demanding assent to an unpublished document is the
    same defect as recording it.
"""
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.legal.documents as legal
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.api.client_routes import (
    accept_current_terms,
    get_my_order,
    list_my_orders,
    my_terms_acceptance,
    submit_order,
)
from app.client_auth.dependencies import AuthedClient
from app.models.client import Client
from app.models.client_user import CLIENT_ADMIN_ROLE, CLIENT_MEMBER_ROLE
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop

pytestmark = pytest.mark.integration


class _FakeGeocoder(BaseGeocoder):
    provider_name = "fake"

    async def geocode(self, address: str) -> GeocodeResult | None:
        return GeocodeResult(lat=30.30, lng=-97.80, display_name=address, provider="fake")


@pytest.fixture(autouse=True)
def _stub_geocoder(monkeypatch):
    """Every order submitted here would otherwise reach the real geocoder.

    Two reasons, and the second is the one that bit. It is a rate-limited third-party
    service, and its client is a module-level singleton holding an httpx transport bound
    to whichever event loop first built it - so a second test in the same file submitting
    a second order tears down a transport on a closed loop and fails with "Event loop is
    closed", nowhere near the code that caused it. Every order-submitting test file here
    patches it for the same reason.
    """
    import app.api.client_routes as routes

    monkeypatch.setattr(routes, "get_geocoder", lambda: _FakeGeocoder())


@pytest.fixture
def published_v2(monkeypatch):
    """Terms published at v2, so a client sitting on v1 owes an acceptance.

    Two moves, both needed: the documents have to be published at all (a draft obliges
    nobody), and the version has to differ from what the client accepted.
    """
    for name in ("terms", "privacy"):
        bumped = replace(
            legal.DOCUMENTS[name], version="v2", status="published", effective=date(2026, 8, 27)
        )
        monkeypatch.setitem(legal.DOCUMENTS, name, bumped)
        monkeypatch.setattr(legal, name.upper(), bumped)
    return "v2"


async def _client_on(db_session, accepted_version: str | None):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id,
            hub_id=hub_id,
            name="Design Partner",
            pos_system="client_portal",
            signup_status="active",
            terms_accepted_version=accepted_version,
            terms_accepted_at=(
                datetime.now(timezone.utc) - timedelta(days=90) if accepted_version else None
            ),
        )
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address="220 Harbor St",
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()
    return client_id, shop_id


def _authed(client_id, role=CLIENT_ADMIN_ROLE) -> AuthedClient:
    return AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="owner@example.com",
        name="Jordan Rivera",
        role=role,
    )


async def _try_order(db_session, client_id, shop_id, role=CLIENT_ADMIN_ROLE):
    from app.schemas.client_order import ClientOrderBody

    return await submit_order(
        body=ClientOrderBody(
            drop_address="900 Congress Ave, Austin TX",
            pickup_shop_id=str(shop_id),
            deadline="today",
        ),
        client=_authed(client_id, role),
        session=db_session,
    )


# ---------------------------------------------------------------------------
# The gap this closes
# ---------------------------------------------------------------------------


async def test_a_stale_client_cannot_send_a_new_order(
    db_session, real_redis_client, published_v2
):
    """The defect, directly. Before this, a version bump changed nothing for them."""
    client_id, shop_id = await _client_on(db_session, "v1")

    with pytest.raises(HTTPException) as exc:
        await _try_order(db_session, client_id, shop_id)
    assert exc.value.status_code == 409
    assert "accept the new version" in exc.value.detail

    # And nothing landed in the queue.
    assert (await db_session.execute(select(Order))).scalars().all() == []


async def test_a_current_client_is_unaffected(db_session, real_redis_client, published_v2):
    client_id, shop_id = await _client_on(db_session, "v2")
    result = await _try_order(db_session, client_id, shop_id)
    assert result.order_id


async def test_a_client_who_never_accepted_anything_owes_acceptance(
    db_session, real_redis_client, published_v2
):
    """`POST /admin/clients` records no version, so an ops-onboarded client has NULL.

    Treating an absent record as satisfied would let exactly those clients order under
    no terms at all - the opposite of what the column is for.
    """
    client_id, shop_id = await _client_on(db_session, None)
    with pytest.raises(HTTPException) as exc:
        await _try_order(db_session, client_id, shop_id)
    assert exc.value.status_code == 409


async def test_reads_keep_working_when_terms_go_stale(
    db_session, real_redis_client, published_v2
):
    """The gate is on creation, not on looking.

    A client mid-shift must still see the deliveries already moving and find the order a
    customer is ringing about. Withholding that punishes them for our change, and the
    order is the transaction the terms govern - not the invoice they already owe.
    """
    client_id, shop_id = await _client_on(db_session, "v1")

    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=(await db_session.get(Client, client_id)).hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
        source_system="client_portal",
        raw_payload={},
        sla_tier="T2",
        hold_deadline=now,
        weight_units=1,
        status=OrderStatus.en_route_drop,
        requested_at=now,
        delivery_address="900 Congress Ave",
    )
    db_session.add(order)
    await db_session.commit()

    page = await list_my_orders(client=_authed(client_id), session=db_session)
    assert page.total == 1
    detail = await get_my_order(
        order_id=str(order.id), client=_authed(client_id), session=db_session
    )
    assert detail.order_id == str(order.id)


# ---------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------


async def test_an_admin_can_accept_and_ordering_resumes(
    db_session, real_redis_client, published_v2
):
    client_id, shop_id = await _client_on(db_session, "v1")

    view = await accept_current_terms(client=_authed(client_id), session=db_session)
    assert view.acceptance_required is False
    assert view.accepted_version == "v2"

    row = await db_session.get(Client, client_id)
    assert row.terms_accepted_version == "v2"
    assert row.terms_accepted_at is not None

    # The gate lifts in the same session.
    assert (await _try_order(db_session, client_id, shop_id)).order_id


async def test_a_member_cannot_accept():
    """Binding the company to a contract is not the same act as reading an order.

    Clause 1 has the accepting party agreeing "on behalf of your business", and a
    `member` is a counter person. The role line C4 already draws is that distinction.

    **Tested through the dependency, not the endpoint.** Every test in this repo calls
    endpoint functions directly, which skips FastAPI's `Depends` entirely - so the first
    version of this test called `accept_current_terms` with a member and passed while
    enforcing nothing. Calling the guard is what actually asserts the rule.
    """
    from app.client_auth.dependencies import require_client_admin

    with pytest.raises(HTTPException) as exc:
        await require_client_admin(client=_authed(uuid.uuid4(), CLIENT_MEMBER_ROLE))
    assert exc.value.status_code == 403

    # And an admin passes, so the guard is not simply refusing everyone.
    admin = _authed(uuid.uuid4(), CLIENT_ADMIN_ROLE)
    assert await require_client_admin(client=admin) is admin


def test_the_accept_endpoint_declares_the_admin_guard():
    """The other half, and the half a direct call can never cover.

    Whether the gate is *wired* is a property of the route rather than of the function
    body, and it is exactly what a refactor silently drops. This reads the declared
    dependency off the app's own routing table.
    """
    import app.api.client_routes as client_routes
    from app.client_auth.dependencies import require_client_admin

    # Read off the router rather than `app.routes`, which wraps each include in a
    # single object and never exposes the individual paths.
    route = next(
        r
        for r in client_routes.router.routes
        if r.path == "/client/terms-acceptance" and "POST" in r.methods
    )
    guards = [d.call for d in route.dependant.dependencies]
    assert require_client_admin in guards, (
        "POST /client/terms-acceptance must require a client admin - a member must not "
        "be able to bind the company to new terms"
    )


async def test_a_member_can_still_see_why_they_are_blocked(
    db_session, real_redis_client, published_v2
):
    """Knowing is not acting.

    A counter person whose order was refused needs to be told why and who can fix it -
    otherwise they are left with a 409 and no route out.
    """
    client_id, _ = await _client_on(db_session, "v1")
    view = await my_terms_acceptance(
        client=_authed(client_id, CLIENT_MEMBER_ROLE), session=db_session
    )
    assert view.acceptance_required is True
    assert view.can_accept is False
    assert view.terms_path == "/terms"


async def test_the_recorded_version_is_the_servers(
    db_session, real_redis_client, published_v2
):
    """Same rule as signup: an acceptance record a caller can write is not evidence.

    There is deliberately no request body on this endpoint, so there is nothing for a
    caller to assert - this asserts the absence held.
    """
    client_id, _ = await _client_on(db_session, "v1")
    await accept_current_terms(client=_authed(client_id), session=db_session)
    assert (await db_session.get(Client, client_id)).terms_accepted_version == "v2"


# ---------------------------------------------------------------------------
# Drafts, and the machine path
# ---------------------------------------------------------------------------


async def test_drafts_oblige_nobody(db_session, real_redis_client):
    """Shipped state: both documents are drafts, so nobody is prompted.

    Demanding assent to an unpublished document is the same defect as recording it,
    which `documents_are_published` already refuses at signup.
    """
    client_id, shop_id = await _client_on(db_session, "v0")
    assert legal.documents_are_published() is False

    view = await my_terms_acceptance(client=_authed(client_id), session=db_session)
    assert view.acceptance_required is False
    assert (await _try_order(db_session, client_id, shop_id)).order_id


async def test_accepting_a_draft_is_refused(db_session, real_redis_client):
    """There is nothing legitimate to accept, so the endpoint says so rather than
    recording assent to a document nobody approved."""
    client_id, _ = await _client_on(db_session, "v1")
    with pytest.raises(HTTPException) as exc:
        await accept_current_terms(client=_authed(client_id), session=db_session)
    assert exc.value.status_code == 409


async def test_the_api_key_path_is_gated_too(db_session, real_redis_client, published_v2):
    """Clause 11 says "further orders" without distinguishing how they arrive.

    Gating only the portal would leave the exposure open for exactly the highest-volume
    clients - the ones whose own systems post orders.
    """
    from app.client_api.dependencies import get_api_client
    from app.models.client_api_key import ClientApiKey, hash_api_key

    client_id, _ = await _client_on(db_session, "v1")
    plaintext = "lmxk_live_" + uuid.uuid4().hex
    db_session.add(
        ClientApiKey(
            client_id=client_id,
            token_hash=hash_api_key(plaintext),
            token_prefix=plaintext[:18],
            description="test",
            is_active=True,
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await get_api_client(x_lmx_api_key=plaintext, session=db_session)
    assert exc.value.status_code == 409
    assert "client portal" in exc.value.detail


async def test_a_pending_client_still_gets_the_uniform_401(
    db_session, real_redis_client, published_v2
):
    """The regression I introduced and then fixed.

    The inactive branch answers with the same 401 as an unknown key on purpose, so a
    live key for a pending account is indistinguishable from a fake one. My first
    version put the terms check *before* it, which would have returned a distinguishable
    409 and confirmed to a key-holder that their key is real and the account exists.
    """
    from app.client_api.dependencies import get_api_client
    from app.models.client_api_key import ClientApiKey, hash_api_key

    client_id, _ = await _client_on(db_session, None)
    pending = await db_session.get(Client, client_id)
    pending.signup_status = "pending"
    plaintext = "lmxk_live_" + uuid.uuid4().hex
    db_session.add(
        ClientApiKey(
            client_id=client_id,
            token_hash=hash_api_key(plaintext),
            token_prefix=plaintext[:18],
            description="test",
            is_active=True,
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await get_api_client(x_lmx_api_key=plaintext, session=db_session)
    assert exc.value.status_code == 401, "a pending client must not be told about terms"
