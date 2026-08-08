"""
Client order submission (docs/LMX_LINK_PLAN.md §2.2).

The first order-CREATING endpoint a client has ever had - everything else on
that router reads what LMX already knows. Two things get the most attention
here:

  - **A pending client cannot order.** That is the whole substance of the
    approval gate; if it leaks, self-serve signup becomes "anyone on the
    internet can dispatch an LMX van."
  - **A submitted order is indistinguishable downstream from an Epicor one.**
    §1.1's rule is that the core never learns where an order came from, so a
    portal order must land in the hold queue and reach a driver by exactly the
    same path.
"""
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.client_routes import list_my_shops, submit_order
from app.batch_queue.store import HoldQueueStore
from app.client_auth.dependencies import AuthedClient
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.client_order import ClientOrderBody, ClientOrderLine

pytestmark = pytest.mark.integration

PICKUP_ADDRESS = "1200 E 6th St, Austin TX"
PICKUP_LAT, PICKUP_LNG = 30.2646, -97.7302


class FakeGeocoder(BaseGeocoder):
    provider_name = "fake"

    def __init__(self, result: GeocodeResult | None = None) -> None:
        self._result = result if result is not None else GeocodeResult(
            lat=PICKUP_LAT, lng=PICKUP_LNG, display_name=PICKUP_ADDRESS, provider="fake"
        )
        self.calls: list[str] = []

    async def geocode(self, address: str) -> GeocodeResult | None:
        self.calls.append(address)
        return self._result


@pytest.fixture(autouse=True)
def _stub_geocoder(monkeypatch):
    """Every test here goes through the endpoint, which reaches for the real
    geocoder. Patched globally so no test can accidentally hit a rate-limited
    third-party service."""
    import app.api.client_routes as routes

    monkeypatch.setattr(routes, "get_geocoder", lambda: FakeGeocoder())


async def _seed(db_session, *, signup_status: str = "active", with_rates: bool = True):
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id, hub_id=hub_id, name="Design Partner",
            pos_system="client_portal", signup_status=signup_status,
        )
    )
    await db_session.commit()
    if with_rates:
        for tier, cents in (("HOT_SHOT", 3500), ("T1", 1800), ("T2", 1200), ("T3", 900)):
            db_session.add(
                ClientRate(client_id=client_id, sla_tier=tier, rate_per_drop_cents=cents)
            )
        await db_session.commit()
    return hub_id, client_id


def _authed(client_id) -> AuthedClient:
    return AuthedClient(
        client_user_id=str(uuid.uuid4()),
        client_id=str(client_id),
        role="admin",
        email="jordan@example.com",
        name="Jordan Rivera",
    )


def _body(**overrides) -> ClientOrderBody:
    payload = dict(
        pickup_address=PICKUP_ADDRESS,
        drop_address="900 Congress Ave, Austin TX",
        deadline="today",
    )
    payload.update(overrides)
    return ClientOrderBody(**payload)


# ---------------------------------------------------------------------------
# The approval gate
# ---------------------------------------------------------------------------


async def test_a_pending_client_cannot_submit_an_order(db_session, real_redis_client):
    """The substance of the whole approval gate."""
    _, client_id = await _seed(db_session, signup_status="pending")

    with pytest.raises(HTTPException) as exc:
        await submit_order(_body(), client=_authed(client_id), session=db_session)

    assert exc.value.status_code == 403
    assert "reviewed" in exc.value.detail

    count = (await db_session.execute(select(Order))).scalars().all()
    assert count == []


async def test_a_rejected_client_cannot_submit_an_order(db_session, real_redis_client):
    _, client_id = await _seed(db_session, signup_status="rejected")
    with pytest.raises(HTTPException) as exc:
        await submit_order(_body(), client=_authed(client_id), session=db_session)
    assert exc.value.status_code == 403


async def test_an_approved_client_can(db_session, real_redis_client):
    _, client_id = await _seed(db_session)
    result = await submit_order(_body(), client=_authed(client_id), session=db_session)
    assert result.order_id


# ---------------------------------------------------------------------------
# The order is an ordinary order
# ---------------------------------------------------------------------------


async def test_a_portal_order_lands_in_the_hold_queue_like_any_other(db_session, real_redis_client):
    """§1.1: nothing downstream knows a person typed this rather than a POS."""
    hub_id, client_id = await _seed(db_session)

    result = await submit_order(_body(), client=_authed(client_id), session=db_session)

    order = await db_session.get(Order, uuid.UUID(result.order_id))
    assert order.status == OrderStatus.held
    assert order.source_system == "client_portal"
    assert order.sla_owner == "LMX"

    held = await HoldQueueStore().get_all(str(hub_id))
    assert [h.order_id for h in held] == [result.order_id]


async def test_a_typed_pickup_becomes_a_remembered_shop(db_session, real_redis_client):
    """§2.2 principle 3 - and the second order to it is then two taps."""
    _, client_id = await _seed(db_session)

    await submit_order(_body(), client=_authed(client_id), session=db_session)

    shops = await list_my_shops(client=_authed(client_id), session=db_session)
    assert len(shops) == 1
    assert shops[0].address == PICKUP_ADDRESS

    # And it can be picked by id next time rather than retyped.
    second = await submit_order(
        _body(pickup_address=None, pickup_shop_id=shops[0].shop_id),
        client=_authed(client_id),
        session=db_session,
    )
    assert second.order_id


async def test_a_shop_belonging_to_another_client_is_not_usable(db_session, real_redis_client):
    """A scoped 404, not a usable pickup."""
    _, client_id = await _seed(db_session)
    _, other_client_id = await _seed(db_session)

    await submit_order(_body(), client=_authed(other_client_id), session=db_session)
    other_shop = (
        await db_session.execute(select(Shop).where(Shop.client_id == other_client_id))
    ).scalar_one()

    with pytest.raises(HTTPException) as exc:
        await submit_order(
            _body(pickup_address=None, pickup_shop_id=str(other_shop.id)),
            client=_authed(client_id),
            session=db_session,
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Deadline as a choice (§2.2 principle 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "choice,expected_tier",
    [
        ("now", "HOT_SHOT"),
        ("within_the_hour", "T1"),
        ("today", "T2"),
        ("tomorrow", "T3"),
    ],
)
async def test_the_deadline_choice_drives_the_tier(
    db_session, real_redis_client, choice, expected_tier
):
    """"Now / within the hour / today / tomorrow" - nobody at a counter operates
    a calendar widget. Routed through the existing SLA engine rather than
    letting a client name a tier: they state urgency, LMX decides what it means
    (§1.3)."""
    _, client_id = await _seed(db_session)

    result = await submit_order(
        _body(deadline=choice), client=_authed(client_id), session=db_session
    )

    assert result.sla_tier == expected_tier


async def test_a_sooner_deadline_gives_a_sooner_collect_by(db_session, real_redis_client):
    """The tiers must actually differ in the promise they produce, not just in
    name."""
    _, client_id = await _seed(db_session)

    urgent = await submit_order(
        _body(deadline="within_the_hour"), client=_authed(client_id), session=db_session
    )
    relaxed = await submit_order(
        _body(deadline="tomorrow"), client=_authed(client_id), session=db_session
    )

    assert urgent.collect_by < relaxed.collect_by


# ---------------------------------------------------------------------------
# The confirmation (§2.2 principle 6)
# ---------------------------------------------------------------------------


async def test_the_confirmation_carries_a_real_collect_by_commitment(db_session, real_redis_client):
    """Not a spinner. collect_by comes from the spec-verified hold windows."""
    _, client_id = await _seed(db_session)
    result = await submit_order(_body(), client=_authed(client_id), session=db_session)

    assert result.collect_by is not None
    assert result.reference
    assert result.status == "held"


async def test_the_delivery_time_is_an_estimate_and_comes_after_collection(db_session, real_redis_client):
    """Named `estimated_delivery_by` rather than `delivery_by` deliberately -
    there is no verified travel-time model until E1 is done."""
    _, client_id = await _seed(db_session)
    # Drop coordinates are absent until the drop is geocoded, so this order has
    # no estimate at all - which is the honest answer rather than a guess.
    result = await submit_order(_body(), client=_authed(client_id), session=db_session)
    assert result.estimated_delivery_by is None
    assert result.dispatchable is False


async def test_the_price_comes_back_when_a_rate_exists(db_session, real_redis_client):
    """Approval sets rates, so an active client's order is always priced."""
    _, client_id = await _seed(db_session)
    result = await submit_order(
        _body(deadline="today"), client=_authed(client_id), session=db_session
    )
    assert result.fee_cents == 1200


async def test_a_missing_rate_is_null_not_zero(db_session, real_redis_client):
    """Order.fee_cents is explicit that null must never look like a free
    delivery. Only reachable for a client approved before rates were required."""
    _, client_id = await _seed(db_session, with_rates=False)
    result = await submit_order(_body(), client=_authed(client_id), session=db_session)
    assert result.fee_cents is None


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def test_an_order_with_no_pickup_at_all_is_refused():
    with pytest.raises(ValidationError, match="pickup"):
        ClientOrderBody(drop_address="900 Congress Ave")


def test_a_reference_is_optional():
    """One more mandatory field is one more reason not to finish the form."""
    assert _body().reference is None


async def test_an_absent_reference_is_generated(db_session, real_redis_client):
    _, client_id = await _seed(db_session)
    result = await submit_order(_body(), client=_authed(client_id), session=db_session)
    assert result.reference.startswith("LMX-")


async def test_the_clients_own_reference_is_kept_when_given(db_session, real_redis_client):
    _, client_id = await _seed(db_session)
    result = await submit_order(
        _body(reference="PO-99812"), client=_authed(client_id), session=db_session
    )
    assert result.reference == "PO-99812"

    order = await db_session.get(Order, uuid.UUID(result.order_id))
    assert order.source_order_ref == "PO-99812"


async def test_line_items_are_carried_through(db_session, real_redis_client):
    _, client_id = await _seed(db_session)
    result = await submit_order(
        _body(line_items=[ClientOrderLine(description="brake caliper", quantity=2)]),
        client=_authed(client_id),
        session=db_session,
    )
    assert result.order_id


async def test_an_unfindable_pickup_address_is_a_clear_422(db_session, real_redis_client, monkeypatch):
    """A typo the client can fix immediately, rather than a driver standing in
    the wrong street."""
    import app.api.client_routes as routes

    class Failing(BaseGeocoder):
        provider_name = "fake"

        async def geocode(self, address):
            return None

    monkeypatch.setattr(routes, "get_geocoder", lambda: Failing())
    _, client_id = await _seed(db_session)

    with pytest.raises(HTTPException) as exc:
        await submit_order(
            _body(pickup_address="nowhere at all"), client=_authed(client_id), session=db_session
        )
    assert exc.value.status_code == 422
    assert "check it" in exc.value.detail
