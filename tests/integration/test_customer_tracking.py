"""
The customer-facing tracking page (docs/ROADMAP.md F3) against real Postgres +
Redis.

**Most of this file is about what the page refuses to show.** The feature is a
public URL, and the naive implementation - render whatever Redis holds for the
assigned driver - hands a member of the public a continuous GPS feed for one of our
employees. So the tests that matter are the negative ones:

  - a driver mid-route, delivering to somebody else, must not be on this
    recipient's map. Otherwise recipient A learns roughly where recipient B lives
    and both can watch the driver's whole working day.
  - the link must stop working after the delivery, or it stays a permanent window
    onto whichever driver is carrying that route next week.
  - an unknown token and an expired one must be indistinguishable, or the endpoint
    confirms guesses.

The positive case is one test. The rules around it are seven.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.delivery.eta import refresh_route_etas
from app.api.public_routes import rate_delivery, track_delivery
from app.config import settings
from app.fleet_state.manager import FleetStateManager
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.schemas.fleet import DriverLocation
from app.schemas.tracking import SubmitRatingBody
from app.tracking.service import (
    TrackingTokenInvalid,
    ensure_tracking_token,
    new_tracking_token,
    resolve_tracking,
    tracking_url,
)

pytestmark = pytest.mark.integration

DRIVER_AT = (30.2600, -97.7300)
MY_DROP = (30.2745, -97.7403)
SOMEONE_ELSES_DROP = (30.3100, -97.8000)


class _Request:
    """Enough of Request for the rate limiter's client_ip lookup."""

    def __init__(self, peer: str = "203.0.113.9") -> None:
        self.client = type("C", (), {"host": peer})()
        self.headers = {}


async def _seed(db_session):
    hub_id, client_id, shop_id, driver_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file")
    )
    db_session.add(
        Driver(
            id=driver_id,
            hub_id=hub_id,
            name="Sam O.",
            phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=5,
        )
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address="220 Harbor St",
            lat=30.26,
            lng=-97.74,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()
    return hub_id, client_id, shop_id, driver_id


async def _order(
    db_session,
    hub_id,
    client_id,
    shop_id,
    *,
    status: OrderStatus = OrderStatus.picked_up,
    drop=MY_DROP,
    phone: str | None = "+15125550101",
    delivered_at: datetime | None = None,
    promised_at: datetime | None = None,
) -> Order:
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_system="flat_file",
        raw_payload={},
        sla_tier="T2",
        hold_deadline=now + timedelta(minutes=30),
        weight_units=1,
        status=status,
        requested_at=now,
        promised_at=promised_at,
        delivered_at=delivered_at,
        delivery_address="1100 Congress Ave, Austin TX",
        delivery_lat=drop[0],
        delivery_lng=drop[1],
        delivery_contact_phone=phone,
        tracking_token=new_tracking_token(),
    )
    db_session.add(order)
    await db_session.commit()
    return order


async def _route_with_stops(
    db_session, hub_id, driver_id, *, stops: list[tuple[Order, str, str]]
) -> Route:
    """`stops` is (order, stop_type, status) in sequence order."""
    route = Route(hub_id=hub_id, driver_id=driver_id, status="active")
    db_session.add(route)
    await db_session.commit()

    for sequence, (order, stop_type, status) in enumerate(stops, start=1):
        stop = Stop(
            route_id=route.id,
            stop_type=stop_type,
            status=status,
            sequence=sequence,
            shop_id=order.shop_id if stop_type == "pickup" else None,
        )
        db_session.add(stop)
        await db_session.commit()
        db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
        await db_session.commit()
    return route


async def _driver_reports_position(hub_id, driver_id, at=DRIVER_AT):
    await FleetStateManager().update_driver_location(
        DriverLocation(
            driver_id=str(driver_id),
            lat=at[0],
            lng=at[1],
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        hub_id=str(hub_id),
    )


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


async def test_a_token_is_minted_once_and_reused(db_session, real_redis_client):
    """Minted lazily so legacy orders and orders nobody tracks are handled by the
    same path - see ensure_tracking_token."""
    hub_id, client_id, shop_id, _ = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    order.tracking_token = None
    await db_session.commit()

    first = await ensure_tracking_token(db_session, order)
    second = await ensure_tracking_token(db_session, order)

    assert first == second
    assert len(first) > 30, "the token is this page's only credential"


async def test_tokens_are_not_predictable(db_session, real_redis_client):
    assert len({new_tracking_token() for _ in range(200)}) == 200


def test_the_link_points_at_the_portals_public_track_route():
    assert tracking_url("abc123").endswith("/track?token=abc123")


# ---------------------------------------------------------------------------
# Rule 1: a driver's position is only for the recipient they're driving to
# ---------------------------------------------------------------------------


async def test_the_position_shows_when_this_drop_is_the_drivers_current_stop(
    db_session, real_redis_client
):
    """The one positive case. Everything below is a refusal."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    route = await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")],
    )
    # Walked, as accepting a route walks it: the drop's ETA is what the page shows.
    await refresh_route_etas(db_session, route.id)
    await db_session.commit()
    await _driver_reports_position(hub_id, driver_id)

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.driver_position is not None
    assert view.driver_position.lat == pytest.approx(DRIVER_AT[0])
    assert view.headline == "On the way"
    # And an ETA - the drop's route ETA, the same one the client portal shows for
    # this order, not a second straight line from the position
    # (tests/integration/test_one_eta.py).
    assert view.estimated_arrival is not None


async def test_the_position_is_hidden_while_the_driver_delivers_to_someone_else(
    db_session, real_redis_client
):
    """**The leak this feature would otherwise ship.** A driver mid-route carries
    other people's parcels. Showing their position between drops tells this
    recipient roughly where the other one lives, and shows both of them the shape
    of the driver's whole day. "Order is picked up" is NOT sufficient grounds to
    put a van on someone's map."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    mine = await _order(db_session, hub_id, client_id, shop_id, drop=MY_DROP)
    theirs = await _order(
        db_session, hub_id, client_id, shop_id, drop=SOMEONE_ELSES_DROP
    )
    # The driver is going to their drop first; mine is later in the sequence.
    await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[
            (mine, "pickup", "completed"),
            (theirs, "dropoff", "pending"),
            (mine, "dropoff", "pending"),
        ],
    )
    await _driver_reports_position(hub_id, driver_id)

    view = await resolve_tracking(db_session, mine.tracking_token)

    assert view.driver_position is None, "this driver is not coming to this recipient yet"
    # Still a truthful status - the recipient isn't left with nothing.
    assert view.headline == "Collected"
    assert view.is_live


async def test_the_position_is_hidden_before_anything_has_been_collected(
    db_session, real_redis_client
):
    """A driver on their way to a shop is doing work unrelated to this recipient's
    address, and showing it would start the GPS feed early for no benefit."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id, status=OrderStatus.assigned)
    await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "pending"), (order, "dropoff", "pending")],
    )
    await _driver_reports_position(hub_id, driver_id)

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.driver_position is None
    assert view.headline == "Driver assigned"


async def test_the_position_is_hidden_once_delivered(db_session, real_redis_client):
    """Otherwise the recipient keeps watching the driver's next several hours."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    delivered = datetime.now(timezone.utc) - timedelta(minutes=5)
    order = await _order(
        db_session,
        hub_id,
        client_id,
        shop_id,
        status=OrderStatus.delivered,
        delivered_at=delivered,
    )
    await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "completed")],
    )
    await _driver_reports_position(hub_id, driver_id)

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.driver_position is None
    assert view.headline == "Delivered"
    assert view.delivered_at is not None
    assert not view.is_live, "a finished delivery must stop the page polling"


async def test_a_driver_who_has_never_reported_a_position_shows_no_map(
    db_session, real_redis_client
):
    """F1 gave drivers a write path but a driver whose app hasn't pinged has no
    position. Showing status without a map beats placing them at 0.0/0.0 - the
    same silent failure the geocoding work exists to prevent."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")],
    )
    # Deliberately no position ping.

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.driver_position is None
    assert view.headline == "Collected"


async def test_an_order_with_no_route_yet_still_tracks(db_session, real_redis_client):
    """The link is sent at pickup, but a recipient may hold one from a previous
    order; a token whose order has no stops must not raise."""
    hub_id, client_id, shop_id, _ = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id, status=OrderStatus.held)

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.driver_position is None
    assert view.headline == "Order received"


# ---------------------------------------------------------------------------
# Rule 2: the link stops working
# ---------------------------------------------------------------------------


async def test_the_link_survives_delivery_long_enough_to_show_the_outcome(
    db_session, real_redis_client
):
    """A recipient who checks the next morning should see the confirmation, not a
    dead link."""
    hub_id, client_id, shop_id, _ = await _seed(db_session)
    order = await _order(
        db_session,
        hub_id,
        client_id,
        shop_id,
        status=OrderStatus.delivered,
        delivered_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )

    view = await resolve_tracking(db_session, order.tracking_token)
    assert view.headline == "Delivered"


async def test_the_link_dies_after_the_grace_window(db_session, real_redis_client):
    """**A tracking URL with no end date is a permanent window onto whichever
    driver is carrying that route.** Links get forwarded and screenshotted; this is
    what bounds the damage."""
    hub_id, client_id, shop_id, _ = await _seed(db_session)
    order = await _order(
        db_session,
        hub_id,
        client_id,
        shop_id,
        status=OrderStatus.delivered,
        delivered_at=datetime.now(timezone.utc)
        - timedelta(hours=settings.tracking_link_grace_hours + 1),
    )

    with pytest.raises(TrackingTokenInvalid):
        await resolve_tracking(db_session, order.tracking_token)


async def test_the_grace_window_is_configurable(db_session, real_redis_client, monkeypatch):
    hub_id, client_id, shop_id, _ = await _seed(db_session)
    order = await _order(
        db_session,
        hub_id,
        client_id,
        shop_id,
        status=OrderStatus.delivered,
        delivered_at=datetime.now(timezone.utc) - timedelta(hours=6),
    )

    monkeypatch.setattr(settings, "tracking_link_grace_hours", 1)
    with pytest.raises(TrackingTokenInvalid):
        await resolve_tracking(db_session, order.tracking_token)

    monkeypatch.setattr(settings, "tracking_link_grace_hours", 48)
    assert (await resolve_tracking(db_session, order.tracking_token)).headline == "Delivered"


# ---------------------------------------------------------------------------
# Rule 3: it says as little as it can
# ---------------------------------------------------------------------------


async def test_the_destination_is_hinted_not_disclosed(db_session, real_redis_client):
    """Enough to recognise ("yes, this is mine"), not the full street address -
    tracking URLs get forwarded and pasted into group chats."""
    hub_id, client_id, shop_id, _ = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.destination_hint == "Congress Ave"
    assert "1100" not in (view.destination_hint or "")


async def test_the_payload_carries_no_driver_or_client_identity(
    db_session, real_redis_client
):
    """The schema is the privacy boundary: if a field isn't on TrackingView, a
    stranger with the URL cannot learn it."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")],
    )
    await _driver_reports_position(hub_id, driver_id)

    view = await resolve_tracking(db_session, order.tracking_token)
    fields = set(vars(view))

    for leak in ("driver_id", "driver_name", "driver_phone", "client_id", "order_id", "shop_name"):
        assert leak not in fields, f"{leak} must not be visible to a public caller"
    # And the rendered payload doesn't smuggle them in either.
    rendered = str(vars(view))
    assert str(driver_id) not in rendered
    assert str(client_id) not in rendered


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


async def test_an_unknown_token_is_a_404(db_session, real_redis_client):
    with pytest.raises(HTTPException) as exc:
        await track_delivery("not-a-real-token", _Request(), session=db_session)
    assert exc.value.status_code == 404


async def test_an_expired_token_looks_exactly_like_an_unknown_one(
    db_session, real_redis_client
):
    """**The enumeration property.** An "expired" response confirms the guesser
    found a real order, leaving the rate limiter as the only thing between them and
    a working guess."""
    hub_id, client_id, shop_id, _ = await _seed(db_session)
    order = await _order(
        db_session,
        hub_id,
        client_id,
        shop_id,
        status=OrderStatus.delivered,
        delivered_at=datetime.now(timezone.utc)
        - timedelta(hours=settings.tracking_link_grace_hours + 1),
    )

    with pytest.raises(HTTPException) as expired:
        await track_delivery(order.tracking_token, _Request(), session=db_session)
    with pytest.raises(HTTPException) as unknown:
        await track_delivery("no-such-token", _Request(), session=db_session)

    assert expired.value.status_code == unknown.value.status_code == 404
    assert expired.value.detail == unknown.value.detail


async def test_an_empty_token_is_refused(db_session, real_redis_client):
    with pytest.raises(HTTPException):
        await track_delivery("", _Request(), session=db_session)


async def test_the_endpoint_is_rate_limited(db_session, real_redis_client, monkeypatch):
    """A read endpoint whose only credential is in the URL is a guessing target,
    and there is no account to lock."""
    from app.tracking import rate_limit

    monkeypatch.setattr(rate_limit, "MAX_TRACKING_REQUESTS", 3)
    request = _Request(peer="198.51.100.77")

    for _ in range(3):
        with pytest.raises(HTTPException) as exc:
            await track_delivery("guess", request, session=db_session)
        assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await track_delivery("guess", request, session=db_session)
    assert exc.value.status_code == 429


async def test_the_limit_tolerates_a_page_that_polls(db_session, real_redis_client):
    """A ceiling tight enough to catch a guesser would break the feature for a
    family refreshing on two devices while a part is inbound."""
    hub_id, client_id, shop_id, _ = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    request = _Request(peer="198.51.100.88")

    for _ in range(30):
        view = await track_delivery(order.tracking_token, request, session=db_session)
    assert view.status == OrderStatus.picked_up.value


async def test_the_endpoint_returns_the_position_when_the_rules_allow_it(
    db_session, real_redis_client
):
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")],
    )
    await _driver_reports_position(hub_id, driver_id)

    view = await track_delivery(order.tracking_token, _Request(), session=db_session)

    assert view.driver_position is not None
    # Timestamped, so the page can say "updated 20 seconds ago" - a stale dot with
    # no time on it reads as a live one, which is worse than showing nothing.
    assert view.driver_position.recorded_at is not None
    assert view.is_live


# ---------------------------------------------------------------------------
# Getting the link to the recipient
# ---------------------------------------------------------------------------
#
# LMX sends no texts. The client sees the link in the portal and the order API
# and forwards it to their customer; the page carries the delivery PIN.


async def test_the_client_is_given_a_working_link(db_session, real_redis_client):
    """The portal's order detail carries the link, and the link resolves."""
    from app.api.client_routes import get_my_order
    from app.client_auth.dependencies import AuthedClient
    from app.tracking.service import ensure_tracking_token, tracking_url

    hub_id, client_id, shop_id, _driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id, phone=None)
    token = await ensure_tracking_token(db_session, order)
    await db_session.commit()

    detail = await get_my_order(
        str(order.id),
        client=AuthedClient(client_id=str(client_id), client_user_id=str(uuid.uuid4()), email="c@example.com", name="Counter", role="admin"),
        session=db_session,
    )

    assert detail.tracking_url == tracking_url(token)
    view = await resolve_tracking(db_session, token)
    assert view.headline == "Collected"


async def test_before_pickup_the_client_has_no_link(db_session, real_redis_client):
    from app.api.client_routes import get_my_order
    from app.client_auth.dependencies import AuthedClient

    hub_id, client_id, shop_id, _driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id, status=OrderStatus.assigned)
    order.tracking_token = None
    await db_session.commit()

    detail = await get_my_order(
        str(order.id),
        client=AuthedClient(client_id=str(client_id), client_user_id=str(uuid.uuid4()), email="c@example.com", name="Counter", role="admin"),
        session=db_session,
    )

    assert detail.tracking_url is None


async def test_the_page_shows_the_delivery_pin_until_the_delivery_is_made(
    db_session, real_redis_client
):
    """The code the recipient gives the driver. It used to be texted; now it is on
    the page whose link the client forwards. Gone once delivered."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await _route_with_stops(
        db_session, hub_id, driver_id, stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")]
    )
    dropoff = (
        await db_session.execute(
            select(Stop).join(StopOrder, StopOrder.stop_id == Stop.id)
            .where(StopOrder.order_id == order.id, Stop.stop_type == "dropoff")
        )
    ).scalar_one()
    dropoff.delivery_pin = "4821"
    await db_session.commit()

    assert (await resolve_tracking(db_session, order.tracking_token)).delivery_pin == "4821"

    dropoff.status = "completed"
    order.status = OrderStatus.delivered
    order.delivered_at = datetime.now(timezone.utc)
    await db_session.commit()

    assert (await resolve_tracking(db_session, order.tracking_token)).delivery_pin is None


class TestEveryStatusSaysSomethingTrue:
    """A recipient is never told a finished delivery is still coming.

    `OrderStatus.returned` is terminal and produced by `app/delivery/resolution.py`,
    and it had no entry here - so it fell through to *"In progress. Your delivery
    is being handled"*, permanently, for a parcel that had gone back to the hub.
    The machine-facing map in `app/orders/state_machine.py` carried
    `RETURNED_TO_HUB` all along and even annotates it "already terminal": the
    integration told the truth and the person did not.

    Found by auditing the client-facing layer the way the record layer was
    audited (`docs/ROADMAP_AUDIT_2026-09.md`).
    """

    def test_every_status_has_its_own_words(self):
        from app.models.order import OrderStatus
        from app.tracking.service import _RECIPIENT_STATUS

        missing = [s.value for s in OrderStatus if s not in _RECIPIENT_STATUS]

        assert missing == [], (
            "these render as the generic fallback, which claims a delivery is in "
            f"progress: {missing}"
        )

    def test_no_terminal_state_reads_as_in_progress(self):
        """The sharp version. `delivered` and `cancelled` were always covered;
        `returned` was not, and it is the one where a recipient waits."""
        from app.orders.state_machine import TERMINAL
        from app.tracking.service import _FALLBACK_STATUS, _RECIPIENT_STATUS

        for status in TERMINAL:
            headline, _detail = _RECIPIENT_STATUS.get(status, _FALLBACK_STATUS)
            assert (headline, _detail) != _FALLBACK_STATUS, (
                f"{status.value} is terminal and would read as 'In progress'"
            )

    def test_returned_says_it_went_back(self):
        from app.models.order import OrderStatus
        from app.tracking.service import _RECIPIENT_STATUS

        headline, detail = _RECIPIENT_STATUS[OrderStatus.returned]

        assert headline == "Returned"
        assert "gone back" in detail
        assert "in progress" not in detail.lower()

    def test_the_fallback_still_exists_for_a_status_nobody_has_written_yet(self):
        """Kept deliberately. A new status added without a line above would
        otherwise render an empty page - "in progress" is the safe thing to say
        when we genuinely do not know, and the wrong thing when we do."""
        from app.tracking.service import _FALLBACK_STATUS

        assert _FALLBACK_STATUS == ("In progress", "Your delivery is being handled.")


# ---------------------------------------------------------------------------
# Proof of delivery, shown to the person it is proof for
# ---------------------------------------------------------------------------
#
# `Stop.pod_photo_url` has been written since the driver app got a camera, and
# the only thing in the backend that ever read it was an idempotency comparison
# in `complete_stop`. So proof of delivery existed as a row and never as
# something a human could look at - not in the ops console, not in the client
# portal, and not here, on the page belonging to the person it is proof for.


async def _delivered_with_photo(
    db_session, hub_id, client_id, shop_id, driver_id, photo, signature=None
):
    order = await _order(db_session, hub_id, client_id, shop_id, status=OrderStatus.delivered)
    order.delivered_at = datetime.now(timezone.utc)
    route = await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "completed")],
    )
    dropoff = (
        await db_session.execute(
            select(Stop).where(Stop.route_id == route.id, Stop.stop_type == "dropoff")
        )
    ).scalar_one()
    dropoff.pod_photo_url = photo
    dropoff.pod_signature_url = signature
    await db_session.commit()
    return order


async def test_a_delivered_order_shows_the_photo(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _delivered_with_photo(
        db_session, hub_id, client_id, shop_id, driver_id,
        "http://localhost:8000/media/pod/a/b/photo-c.jpg",
    )

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.pod_photo_url == "http://localhost:8000/media/pod/a/b/photo-c.jpg"


async def test_an_undelivered_order_shows_no_photo(db_session, real_redis_client):
    # There cannot be one yet, and a field that is sometimes-null-sometimes-
    # withheld is one the page has to reason about twice.
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id, status=OrderStatus.en_route_drop)
    await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")],
    )

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.pod_photo_url is None


async def test_a_delivery_proved_another_way_has_no_photo(db_session, real_redis_client):
    # A signature, a PIN and a left-with note are all valid proof. The page
    # renders nothing rather than an empty frame, so this must be None and not
    # an empty string.
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _delivered_with_photo(
        db_session, hub_id, client_id, shop_id, driver_id, None
    )

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.pod_photo_url is None


async def test_a_delivery_signed_for_shows_the_signature(db_session, real_redis_client):
    """The other half of the same gap.

    `pod_signature_url` has exactly the history `pod_photo_url` had - written
    since the app got a signature pad, read by one idempotency comparison - so a
    delivery *signed* for rather than photographed was proved to nobody at all.
    """
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _delivered_with_photo(
        db_session, hub_id, client_id, shop_id, driver_id,
        None, signature="http://localhost:8000/public/media/pod/a/b/signature-c.png",
    )

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.pod_photo_url is None
    assert view.pod_signature_url.endswith("signature-c.png")


async def test_both_kinds_of_proof_survive_together(db_session, real_redis_client):
    # A client can require a photo *and* a signature (`ProofRequirements`), so
    # the page has to be able to show both rather than picking one.
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _delivered_with_photo(
        db_session, hub_id, client_id, shop_id, driver_id,
        "http://localhost:8000/public/media/pod/a/b/photo-c.jpg",
        signature="http://localhost:8000/public/media/pod/a/b/signature-c.png",
    )

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.pod_photo_url and view.pod_signature_url


async def test_the_page_gets_proof_it_can_load(db_session, real_redis_client, photo_bucket):
    """The bucket is private, so the object URLs stored at delivery opened for
    nobody, the receiver included: the page drew broken images. The endpoint
    signs both on the way out."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    photo = photo_bucket + "pod/a/b/photo-c.jpg"
    signature = photo_bucket + "pod/a/b/signature-c.png"
    order = await _delivered_with_photo(
        db_session, hub_id, client_id, shop_id, driver_id, photo, signature=signature
    )

    view = await track_delivery(order.tracking_token, _Request(), session=db_session)

    for stored, served in ((photo, view.pod_photo_url), (signature, view.pod_signature_url)):
        assert served.startswith(stored + "?")
        assert "X-Amz-Signature=" in served


async def test_rating_keeps_the_proof_on_the_page(db_session, real_redis_client, photo_bucket):
    """The page swaps in the rating's reply as its new view, and that reply left
    the proof out: the photo and signature vanished the moment the recipient
    rated."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    photo = photo_bucket + "pod/a/b/photo-c.jpg"
    signature = photo_bucket + "pod/a/b/signature-c.png"
    order = await _delivered_with_photo(
        db_session, hub_id, client_id, shop_id, driver_id, photo, signature=signature
    )

    view = await rate_delivery(
        order.tracking_token, SubmitRatingBody(score=5), _Request(), session=db_session
    )

    assert view.rating.score == 5
    for stored, served in ((photo, view.pod_photo_url), (signature, view.pod_signature_url)):
        assert served.startswith(stored + "?")
        assert "X-Amz-Signature=" in served


async def test_an_undelivered_order_shows_no_signature(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id, status=OrderStatus.en_route_drop)
    await _route_with_stops(
        db_session, hub_id, driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")],
    )

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.pod_signature_url is None

