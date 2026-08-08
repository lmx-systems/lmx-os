"""
Bulk paste (docs/LMX_LINK_PLAN.md §2.2 principle 5).

"A dispatcher with six orders pastes six lines. Parse them, show what was
understood, let them fix it."

The behaviour that matters most is **partial success**. One unfindable address
among six must not discard the five that were fine - the CSV adapter states the
same rule as "never silently drop a row", and this is that rule a step earlier in
the funnel. Almost every test here is some form of that.
"""
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.client_routes import submit_orders_batch
from app.client_auth.dependencies import AuthedClient
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.hub import Hub
from app.models.order import Order
from app.models.shop import Shop
from app.schemas.client_order import (
    MAX_BATCH_ROWS,
    ClientOrderBatchBody,
    ClientOrderBatchRow,
)

pytestmark = pytest.mark.integration

PICKUP = "1200 E 6th St, Austin TX"
KNOWN = {
    PICKUP: (30.2646, -97.7302),
    "900 Congress Ave, Austin TX": (30.2729, -97.7414),
    "500 W 2nd St, Austin TX": (30.2650, -97.7500),
    "1100 Red River St, Austin TX": (30.2700, -97.7350),
}


class MapGeocoder(BaseGeocoder):
    """Resolves only the addresses it knows - anything else is unfindable,
    which is exactly the case bulk paste has to survive."""

    provider_name = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def geocode(self, address: str) -> GeocodeResult | None:
        self.calls.append(address)
        for known, (lat, lng) in KNOWN.items():
            if address.strip().casefold() == known.casefold():
                return GeocodeResult(lat=lat, lng=lng, display_name=known, provider="fake")
        return None


@pytest.fixture(autouse=True)
def _stub_geocoder(monkeypatch):
    import app.api.client_routes as routes

    monkeypatch.setattr(routes, "get_geocoder", lambda: MapGeocoder())


async def _seed(db_session, *, signup_status: str = "active"):
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
    for tier, cents in (("HOT_SHOT", 3500), ("T1", 1800), ("T2", 1200), ("T3", 900)):
        db_session.add(ClientRate(client_id=client_id, sla_tier=tier, rate_per_drop_cents=cents))
    await db_session.commit()
    return hub_id, client_id


def _authed(client_id) -> AuthedClient:
    return AuthedClient(
        client_user_id=str(uuid.uuid4()), client_id=str(client_id), role="admin",
        email="jordan@example.com", name="Jordan Rivera",
    )


def _batch(*drops, **overrides) -> ClientOrderBatchBody:
    payload = dict(
        pickup_address=PICKUP,
        deadline="today",
        rows=[ClientOrderBatchRow(drop_address=d) for d in drops],
    )
    payload.update(overrides)
    return ClientOrderBatchBody(**payload)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_three_pasted_lines_become_three_orders(db_session, real_redis_client):
    _, client_id = await _seed(db_session)

    result = await submit_orders_batch(
        _batch(
            "900 Congress Ave, Austin TX",
            "500 W 2nd St, Austin TX",
            "1100 Red River St, Austin TX",
        ),
        client=_authed(client_id),
        session=db_session,
    )

    assert result.accepted == 3
    assert result.failed == 0
    assert all(r.order is not None for r in result.results)

    count = (await db_session.execute(select(func.count()).select_from(Order))).scalar_one()
    assert count == 3


async def test_every_row_shares_the_pickup_and_deadline(db_session, real_redis_client):
    """The sharing is what makes paste fast - asked once, not six times."""
    _, client_id = await _seed(db_session)

    result = await submit_orders_batch(
        _batch("900 Congress Ave, Austin TX", "500 W 2nd St, Austin TX", deadline="within_the_hour"),
        client=_authed(client_id),
        session=db_session,
    )

    assert {r.order.sla_tier for r in result.results} == {"T1"}
    # One shop, created once from the shared pickup and reused.
    shops = (await db_session.execute(select(func.count()).select_from(Shop))).scalar_one()
    assert shops == 1


async def test_each_row_gets_its_own_reference(db_session, real_redis_client):
    _, client_id = await _seed(db_session)
    result = await submit_orders_batch(
        _batch("900 Congress Ave, Austin TX", "500 W 2nd St, Austin TX"),
        client=_authed(client_id),
        session=db_session,
    )
    refs = {r.order.reference for r in result.results}
    assert len(refs) == 2


async def test_a_pasted_reference_is_kept(db_session, real_redis_client):
    """A dispatcher pasting from a spreadsheet brings their own order numbers."""
    _, client_id = await _seed(db_session)

    result = await submit_orders_batch(
        ClientOrderBatchBody(
            pickup_address=PICKUP,
            rows=[
                ClientOrderBatchRow(drop_address="900 Congress Ave, Austin TX", reference="PO-1001"),
                ClientOrderBatchRow(drop_address="500 W 2nd St, Austin TX", reference="PO-1002"),
            ],
        ),
        client=_authed(client_id),
        session=db_session,
    )

    assert [r.order.reference for r in result.results] == ["PO-1001", "PO-1002"]


# ---------------------------------------------------------------------------
# Partial success - the point of the whole endpoint
# ---------------------------------------------------------------------------


async def test_one_bad_line_does_not_discard_the_good_ones(db_session, real_redis_client):
    """The behaviour this endpoint exists for. Five good rows must survive a
    sixth that nobody can geocode."""
    _, client_id = await _seed(db_session)

    result = await submit_orders_batch(
        _batch(
            "900 Congress Ave, Austin TX",
            "somewhere that does not exist at all",
            "500 W 2nd St, Austin TX",
        ),
        client=_authed(client_id),
        session=db_session,
    )

    assert result.accepted == 2
    assert result.failed == 1

    orders = (await db_session.execute(select(func.count()).select_from(Order))).scalar_one()
    assert orders == 2, "the good rows are committed, not rolled back with the bad one"


async def test_a_failed_row_is_reported_against_the_line_it_came_from(db_session, real_redis_client):
    """Reported by index and address, not as a count - so the dispatcher knows
    which line to fix rather than re-reading all six."""
    _, client_id = await _seed(db_session)

    result = await submit_orders_batch(
        _batch("900 Congress Ave, Austin TX", "nowhere at all", "500 W 2nd St, Austin TX"),
        client=_authed(client_id),
        session=db_session,
    )

    failed = [r for r in result.results if r.error]
    assert len(failed) == 1
    assert failed[0].index == 1
    assert failed[0].drop_address == "nowhere at all"
    assert "delivery address" in failed[0].error


async def test_results_come_back_in_the_order_they_were_pasted(db_session, real_redis_client):
    """So the report lines up with what the dispatcher is looking at."""
    _, client_id = await _seed(db_session)
    drops = [
        "900 Congress Ave, Austin TX",
        "nowhere at all",
        "500 W 2nd St, Austin TX",
        "also nowhere",
    ]

    result = await submit_orders_batch(
        _batch(*drops), client=_authed(client_id), session=db_session
    )

    assert [r.index for r in result.results] == [0, 1, 2, 3]
    assert [r.drop_address for r in result.results] == drops


async def test_a_bad_shared_pickup_fails_every_row(db_session, real_redis_client):
    """Correct rather than unfortunate: the pickup is shared, so it is wrong for
    all of them - and the dispatcher sees it on every line instead of having to
    infer that the common factor was the pickup."""
    _, client_id = await _seed(db_session)

    result = await submit_orders_batch(
        _batch(
            "900 Congress Ave, Austin TX",
            "500 W 2nd St, Austin TX",
            pickup_address="a pickup nobody can find",
        ),
        client=_authed(client_id),
        session=db_session,
    )

    assert result.accepted == 0
    assert result.failed == 2
    assert all("pickup address" in r.error for r in result.results)


async def test_every_row_failing_is_still_a_200_with_a_report(db_session, real_redis_client):
    """Not an error response. The dispatcher needs the per-line detail, and a
    4xx with no body would tell them nothing about which lines were wrong."""
    _, client_id = await _seed(db_session)

    result = await submit_orders_batch(
        _batch("nowhere", "also nowhere"), client=_authed(client_id), session=db_session
    )

    assert result.accepted == 0
    assert result.failed == 2
    assert len(result.results) == 2


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


async def test_a_pending_client_cannot_paste_either(db_session, real_redis_client):
    """The approval gate applies to every order-creating path, not just the
    single-order one."""
    _, client_id = await _seed(db_session, signup_status="pending")

    with pytest.raises(HTTPException) as exc:
        await submit_orders_batch(
            _batch("900 Congress Ave, Austin TX"), client=_authed(client_id), session=db_session
        )
    assert exc.value.status_code == 403


async def test_a_shop_from_another_client_is_refused(db_session, real_redis_client):
    """Same scoped check as the single-order path - shared helper, so it can't
    drift between them."""
    _, client_id = await _seed(db_session)
    _, other_id = await _seed(db_session)

    await submit_orders_batch(
        _batch("900 Congress Ave, Austin TX"), client=_authed(other_id), session=db_session
    )
    other_shop = (
        await db_session.execute(select(Shop).where(Shop.client_id == other_id))
    ).scalar_one()

    with pytest.raises(HTTPException) as exc:
        await submit_orders_batch(
            _batch("500 W 2nd St, Austin TX", pickup_address=None, pickup_shop_id=str(other_shop.id)),
            client=_authed(client_id),
            session=db_session,
        )
    assert exc.value.status_code == 404


def test_an_empty_paste_is_refused():
    with pytest.raises(ValidationError):
        ClientOrderBatchBody(pickup_address=PICKUP, rows=[])


def test_a_paste_larger_than_the_cap_is_refused():
    """Capped because every new address costs a geocoder call and the pilot
    provider allows one per second - an unbounded paste would hold the request
    open for minutes."""
    rows = [ClientOrderBatchRow(drop_address=f"{i} Some St") for i in range(MAX_BATCH_ROWS + 1)]
    with pytest.raises(ValidationError):
        ClientOrderBatchBody(pickup_address=PICKUP, rows=rows)


def test_a_paste_at_exactly_the_cap_is_allowed():
    rows = [ClientOrderBatchRow(drop_address=f"{i} Some St") for i in range(MAX_BATCH_ROWS)]
    assert len(ClientOrderBatchBody(pickup_address=PICKUP, rows=rows).rows) == MAX_BATCH_ROWS


def test_a_batch_with_no_pickup_is_refused():
    with pytest.raises(ValidationError, match="pickup"):
        ClientOrderBatchBody(rows=[ClientOrderBatchRow(drop_address="900 Congress Ave")])


# ---------------------------------------------------------------------------
# The geocoder cost this endpoint makes visible
# ---------------------------------------------------------------------------


async def test_repeat_addresses_across_a_paste_are_geocoded_once(db_session, real_redis_client, monkeypatch):
    """The address cache is what keeps paste viable against a 1-req/sec
    provider. Two lines to the same place must not cost two calls."""
    import app.api.client_routes as routes

    geocoder = MapGeocoder()
    monkeypatch.setattr(routes, "get_geocoder", lambda: geocoder)
    _, client_id = await _seed(db_session)

    await submit_orders_batch(
        _batch("900 Congress Ave, Austin TX", "900 Congress Ave, Austin TX"),
        client=_authed(client_id),
        session=db_session,
    )

    # Pickup once, drop once - not twice each.
    assert geocoder.calls.count("900 Congress Ave, Austin TX") == 1
    assert geocoder.calls.count(PICKUP) == 1
