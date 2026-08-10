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

from app.api.public_routes import track_delivery
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
    await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")],
    )
    await _driver_reports_position(hub_id, driver_id)

    view = await resolve_tracking(db_session, order.tracking_token)

    assert view.driver_position is not None
    assert view.driver_position.lat == pytest.approx(DRIVER_AT[0])
    assert view.headline == "On the way"
    # And an ETA derived from where the driver actually is.
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


async def test_the_recipient_is_texted_a_working_link_on_pickup(
    db_session, real_redis_client
):
    """Pickup is the trigger because it is the first moment the link is worth
    opening - there is now a van with their parts on it. Sent earlier it shows
    "scheduling" for an hour, which teaches people not to click it."""
    from app.messaging.tracking_notifications import notify_recipient_picked_up
    from app.models.message import Message
    from sqlalchemy import select

    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    order.tracking_token = None
    await db_session.commit()

    await notify_recipient_picked_up(
        db_session,
        hub_id=hub_id,
        driver_id=driver_id,
        stop_id=None,
        order=order,
    )
    await db_session.commit()

    message = (
        await db_session.execute(select(Message).where(Message.channel == "recipient"))
    ).scalar_one()
    assert order.tracking_token, "the token is minted at the moment it is disclosed"
    assert order.tracking_token in message.body
    assert message.counterparty_phone == order.delivery_contact_phone

    # And the link in that text actually resolves.
    view = await resolve_tracking(db_session, order.tracking_token)
    assert view.headline == "Collected"


async def test_an_order_with_no_recipient_phone_is_not_an_error(
    db_session, real_redis_client
):
    """The common case for source systems that never captured one. An order
    without a recipient phone is a delivery the shop fields questions about
    themselves, exactly as before this feature existed."""
    from app.messaging.tracking_notifications import notify_recipient_picked_up
    from app.models.message import Message
    from sqlalchemy import func, select

    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id, phone=None)

    await notify_recipient_picked_up(
        db_session, hub_id=hub_id, driver_id=driver_id, stop_id=None, order=order
    )
    await db_session.commit()

    count = (
        await db_session.execute(select(func.count()).select_from(Message))
    ).scalar_one()
    assert count == 0


async def test_a_failing_sms_client_cannot_unwind_a_completed_pickup(
    db_session, real_redis_client, monkeypatch
):
    """The exact assumption that bit app/messaging/client_emails.py once already:
    SmsClient.send documents a None return for failure, but a client that RAISES
    must not take down a delivery the driver has already made."""
    from app.messaging import tracking_notifications
    from app.messaging.tracking_notifications import notify_recipient_picked_up

    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)

    class _Exploding:
        async def send(self, to, body):
            raise RuntimeError("twilio is down")

    monkeypatch.setattr(tracking_notifications, "get_sms_client", lambda: _Exploding())

    # Must not raise.
    await notify_recipient_picked_up(
        db_session, hub_id=hub_id, driver_id=driver_id, stop_id=None, order=order
    )
    await db_session.commit()


async def test_a_hot_shot_recipient_gets_the_tier_specific_copy(
    db_session, real_redis_client
):
    from app.messaging.tracking_notifications import notify_recipient_picked_up
    from app.models.message import Message
    from sqlalchemy import select

    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    order.sla_tier = "HOT_SHOT"
    await db_session.commit()

    await notify_recipient_picked_up(
        db_session, hub_id=hub_id, driver_id=driver_id, stop_id=None, order=order
    )
    await db_session.commit()

    message = (
        await db_session.execute(select(Message).where(Message.channel == "recipient"))
    ).scalar_one()
    assert "Hot Shot" in message.body
