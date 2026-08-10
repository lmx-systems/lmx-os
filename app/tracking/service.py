"""
What a delivery recipient is allowed to see (docs/ROADMAP.md F3).

The page itself is the easy half. **The substance of this module is deciding when
a stranger holding a URL may see a live GPS position, because the obvious
implementation - render whatever Redis has for the assigned driver - hands a
member of the public a continuous location feed for one of our employees.**

Three rules, each closing a different leak:

1. **A driver's position is visible only while that driver's CURRENT stop is this
   recipient's delivery.** Not "while the order is picked up", which is the
   tempting version: a driver mid-route has other people's parcels on the van, so
   showing their position between drops tells recipient A roughly where recipient
   B lives, and shows both of them the whole shape of the driver's working day.
   Deriving it from the stop sequence rather than `Order.status` is what makes the
   rule precise - status says "collected", the stop rows say "and you're next".

2. **The link stops working.** A tracking URL with no end date is a permanent
   window onto whoever is carrying that route. It survives delivery only long
   enough for the recipient to see the confirmation
   (`settings.tracking_link_grace_hours`), then answers exactly as it would for a
   token that never existed.

3. **It says as little as it can.** No driver name or phone, no other stops, no
   client identity, no internal ids, and never the count of what else is on the
   van. The recipient already knows their own address, so echoing a truncated form
   of it is the only personal data here - and it is what makes the page trustworthy
   ("yes, this is my delivery") rather than a bare status word.

An unauthenticated GET is a small surface, but it is a READ of real operational
data, which is the opposite trade-off from `app/api/public_routes.py`'s signup
write. So the protections are aimed differently: not "what can this create" but
"what can this reveal, and to whom, for how long".
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.clustering import miles_between
from app.config import settings
from app.fleet_state.manager import FleetStateManager
from app.gig_platform.economics import minutes_for_miles
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.stop import Stop, StopOrder

logger = structlog.get_logger(__name__)

# 32 bytes of entropy, url-safe. The token IS the credential for this page, so it
# has to be unguessable on its own - there is no second factor and no account
# behind it. 43 characters is long enough that brute force is hopeless even
# without the rate limiter in front of it.
_TOKEN_BYTES = 32

# Stop rows that are done with, one way or the other. Mirrors
# app/api/driver_routes.py's _TERMINAL_STOP_STATUSES - duplicated rather than
# imported to avoid this module depending on the API layer.
_TERMINAL_STOP_STATUSES = ("completed", "failed")

# Order states where nothing is moving yet and there is nothing to place on a map.
_PRE_DISPATCH_STATUSES = (
    OrderStatus.received,
    OrderStatus.classified,
    OrderStatus.held,
    OrderStatus.queued,
)

# What the recipient is told, per order state. Deliberately in the recipient's
# language rather than ours: they do not know what "queued" or "assigned" means,
# and "EN_ROUTE_DROP" is a sink's vocabulary, not a person's.
_RECIPIENT_STATUS = {
    OrderStatus.received: ("Order received", "We have your delivery and are getting it scheduled."),
    OrderStatus.classified: ("Order received", "We have your delivery and are getting it scheduled."),
    OrderStatus.held: ("Order received", "We have your delivery and are getting it scheduled."),
    OrderStatus.queued: ("Scheduling", "We're assigning a driver now."),
    OrderStatus.assigned: ("Driver assigned", "A driver is scheduled to collect your delivery."),
    OrderStatus.accepted: ("Driver assigned", "A driver is on the way to collect your delivery."),
    OrderStatus.en_route_pickup: (
        "Collecting",
        "Your driver is on the way to collect your delivery.",
    ),
    OrderStatus.picked_up: ("Collected", "Your delivery is with the driver."),
    OrderStatus.en_route_drop: ("On the way", "Your driver is heading to you now."),
    OrderStatus.delivered: ("Delivered", "Your delivery has arrived."),
    OrderStatus.cancelled: ("Cancelled", "This delivery was cancelled."),
    OrderStatus.delivery_failed: (
        "Attempted",
        "We couldn't complete this delivery. Our team is in touch with the sender.",
    ),
}
_FALLBACK_STATUS = ("In progress", "Your delivery is being handled.")


@dataclass(frozen=True)
class DriverPosition:
    lat: float
    lng: float
    recorded_at: datetime


@dataclass(frozen=True)
class TrackingView:
    """Everything the public page gets. If a field isn't here, it isn't visible."""

    status: str
    headline: str
    detail: str
    # Truncated - enough for "yes, this is mine", not enough to be an address
    # leak if the link is forwarded or ends up in a log.
    destination_hint: str | None
    estimated_arrival: datetime | None
    delivered_at: datetime | None
    # None whenever rule 1 above says the position must not be shown, which is
    # most of the delivery's life.
    driver_position: DriverPosition | None
    # Tells the page whether to keep polling, so a delivered order stops hitting
    # the endpoint every few seconds forever.
    is_live: bool


class TrackingTokenInvalid(Exception):
    """Unknown, or past its grace window. Callers MUST NOT distinguish these.

    An "expired" response tells a token-guesser that they found a real one, which
    turns the rate limiter into the only thing standing between them and a working
    guess. Same reasoning as the uniform responses on signup and password reset.
    """


def new_tracking_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


async def ensure_tracking_token(session: AsyncSession, order: Order) -> str:
    """This order's tracking token, minting one if it has none.

    Lazy rather than at ingestion on purpose: every order that predates this
    feature has no token, and there are two ingestion entry points plus the
    returns and resolution paths that create orders. Minting on first use means
    one code path handles new and legacy orders identically, and an order nobody
    ever tracks never gets a credential it doesn't need.

    Does not commit - the caller owns the transaction.
    """
    if order.tracking_token:
        return order.tracking_token
    order.tracking_token = new_tracking_token()
    await session.flush()
    return order.tracking_token


def tracking_url(token: str) -> str:
    base = settings.portal_base_url.rstrip("/")
    return f"{base}/track?token={token}"


def _destination_hint(order: Order) -> str | None:
    """Enough of the address to recognise, not enough to disclose.

    The recipient knows where they live, so this exists to build confidence that
    the link is about the right delivery. But a tracking URL gets forwarded,
    screenshotted and pasted into group chats, so the full street address does not
    belong on a page whose only credential is in the URL bar.
    """
    if not order.delivery_address:
        return None
    first_line = order.delivery_address.split(",")[0].strip()
    if len(first_line) <= 6:
        return first_line
    # Keep the street, drop the house number: "…Congress Ave" rather than
    # "1100 Congress Ave".
    parts = first_line.split()
    if parts and any(char.isdigit() for char in parts[0]):
        return " ".join(parts[1:]) or first_line
    return first_line


async def _dropoff_stop_for(session: AsyncSession, order: Order) -> Stop | None:
    result = await session.execute(
        select(Stop)
        .join(StopOrder, StopOrder.stop_id == Stop.id)
        .where(StopOrder.order_id == order.id, Stop.stop_type == "dropoff")
        .order_by(Stop.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _is_the_drivers_current_stop(session: AsyncSession, stop: Stop) -> bool:
    """Whether this stop is the next thing the driver does.

    **This is rule 1, and it is the whole privacy model.** The earliest
    non-terminal stop on the route is where the driver is headed; if that is this
    recipient's drop, the driver is coming to them and their position is
    information about this delivery. If it is anything else, the driver's position
    is information about somebody else's.
    """
    result = await session.execute(
        select(Stop.id)
        .where(
            Stop.route_id == stop.route_id,
            Stop.status.notin_(_TERMINAL_STOP_STATUSES),
        )
        .order_by(Stop.sequence)
        .limit(1)
    )
    current_stop_id = result.scalar_one_or_none()
    return current_stop_id is not None and current_stop_id == stop.id


async def _driver_position(
    session: AsyncSession, stop: Stop, fleet: FleetStateManager
) -> DriverPosition | None:
    route = await session.get(Route, stop.route_id)
    if route is None or route.driver_id is None:
        return None
    location = await fleet.get_driver_location(str(route.hub_id), str(route.driver_id))
    if location is None:
        # F1 gave drivers a way to report position, but a driver whose app hasn't
        # pinged yet has none. The page shows status without a map rather than
        # placing them at 0.0/0.0 - the same Gulf-of-Guinea failure the geocoding
        # work exists to prevent.
        return None
    return DriverPosition(
        lat=location.lat,
        lng=location.lng,
        recorded_at=datetime.fromisoformat(location.recorded_at),
    )


def _estimated_arrival(
    order: Order, position: DriverPosition | None
) -> datetime | None:
    """When the recipient should expect the driver.

    Two sources, in order of how much they actually know:

      - a live position, which gives straight-line distance to the drop at the
        same placeholder speed the gig accept-gate and the client-portal estimate
        use. Consistent by construction: the recipient must not see a different
        number from the one their sender was quoted.
      - `promised_at`, what we committed to, when there is no position.

    Straight-line rather than road-network because there is still no verified
    travel-time model (E1). Named an estimate everywhere it surfaces, and the page
    presents it as one.
    """
    if position is not None and order.delivery_lat is not None and order.delivery_lng is not None:
        miles = miles_between(
            position.lat, position.lng, float(order.delivery_lat), float(order.delivery_lng)
        )
        return position.recorded_at + timedelta(minutes=minutes_for_miles(miles))
    return order.promised_at


async def resolve_tracking(session: AsyncSession, token: str) -> TrackingView:
    """The public page's whole payload, or `TrackingTokenInvalid`.

    Raises the same exception for an unknown token and an expired one - see
    `TrackingTokenInvalid`.
    """
    if not token:
        raise TrackingTokenInvalid("no token")

    result = await session.execute(select(Order).where(Order.tracking_token == token))
    order = result.scalar_one_or_none()
    if order is None:
        raise TrackingTokenInvalid("unknown token")

    now = datetime.now(timezone.utc)
    grace = timedelta(hours=settings.tracking_link_grace_hours)
    finished_at = order.delivered_at or (
        order.updated_at if order.status in (OrderStatus.cancelled,) else None
    )
    if finished_at is not None and now - finished_at > grace:
        # Rule 2. A recipient checking the next morning still sees the
        # confirmation; a link resurfacing weeks later does not.
        logger.info("tracking_link_expired", order_id=str(order.id))
        raise TrackingTokenInvalid("expired")

    headline, detail = _RECIPIENT_STATUS.get(order.status, _FALLBACK_STATUS)

    position: DriverPosition | None = None
    if order.status not in _PRE_DISPATCH_STATUSES and order.status != OrderStatus.delivered:
        stop = await _dropoff_stop_for(session, order)
        if stop is not None and await _is_the_drivers_current_stop(session, stop):
            position = await _driver_position(session, stop, FleetStateManager())
            if position is not None:
                # The status vocabulary above is derived from Order.status, which
                # nothing ever advances to en_route_drop today. The stop sequence
                # is the more truthful source, so when it says the driver is
                # inbound, say so.
                headline, detail = _RECIPIENT_STATUS[OrderStatus.en_route_drop]

    return TrackingView(
        status=order.status.value,
        headline=headline,
        detail=detail,
        destination_hint=_destination_hint(order),
        estimated_arrival=_estimated_arrival(order, position),
        delivered_at=order.delivered_at,
        driver_position=position,
        is_live=order.status
        not in (OrderStatus.delivered, OrderStatus.cancelled, OrderStatus.delivery_failed),
    )
