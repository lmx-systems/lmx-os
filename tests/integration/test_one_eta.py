"""One ETA: the driver, the recipient and the client see the same arrival time.

The recipient's tracking page and the client portal answered "when will it arrive"
two ways. The page drew a straight line from the driver's live position while the
drop was the current stop; the portal read the route walk (`Stop.eta`). For the
very drop a recipient is waiting on, the two disagreed, and the page could show a
time already gone behind a stale ping. The driver app received the route ETA for
every stop and showed none of them.

Now one function answers (`app/delivery/eta.py::order_arrival_estimate`), and the
route walk is refreshed by the driver's pings at most once a minute, so following
a driver stuck in traffic no longer needs the live-position shortcut.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.client_routes import get_my_order
from app.api.driver_routes import get_my_route, report_my_location
from app.batch_queue.clustering import miles_between
from app.client_auth.dependencies import AuthedClient
from app.db import session_scope
from app.delivery.eta import PING_REFRESH_SECONDS, refresh_after_ping, refresh_route_etas
from app.driver_auth.dependencies import AuthedDriver
from app.models.client_user import CLIENT_MEMBER_ROLE
from app.models.order import OrderStatus
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.redis_client import get_client
from app.schemas.driver_app import DriverLocationPingBody
from app.tracking.service import resolve_tracking
from app.travel import minutes_for_miles
from tests.integration.test_customer_tracking import (
    MY_DROP,
    _driver_reports_position,
    _order,
    _route_with_stops,
    _seed,
)

pytestmark = pytest.mark.integration

# Twenty-odd miles from the drop. A straight line from here gives an arrival an
# hour or more out, nowhere near the route walk's - so a surface still using the
# live position cannot agree with one using the walk by accident.
FAR_AWAY = (30.55, -97.95)


def _client(client_id) -> AuthedClient:
    return AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="counter@example.com",
        name="Alex at the counter",
        role=CLIENT_MEMBER_ROLE,
    )


def _driver(hub_id, driver_id) -> AuthedDriver:
    return AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")


async def _dropoff(db_session, order) -> Stop:
    """`order` may be an Order or its id - an id when a rollback in the code under
    test has expired the instance."""
    order_id = order if isinstance(order, uuid.UUID) else order.id
    stop = (
        await db_session.execute(
            select(Stop)
            .join(StopOrder, StopOrder.stop_id == Stop.id)
            .where(StopOrder.order_id == order_id, Stop.stop_type == "dropoff")
        )
    ).scalar_one()
    await db_session.refresh(stop)
    return stop


async def _portal_estimate(db_session, client_id, order) -> datetime | None:
    view = await get_my_order(str(order.id), client=_client(client_id), session=db_session)
    value = view.estimated_delivery_by
    if value is None:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


async def _on_a_route_as_the_current_drop(db_session):
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    route = await _route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")],
    )
    await refresh_route_etas(db_session, route.id)
    await db_session.commit()
    return hub_id, client_id, driver_id, order, route


# ---------------------------------------------------------------------------
# The same number everywhere
# ---------------------------------------------------------------------------


async def test_the_recipient_the_client_and_the_driver_see_one_eta(db_session, real_redis_client):
    """The drop is the driver's current stop and the van's live position is on
    file - the case where the page used to draw its own straight line."""
    hub_id, client_id, driver_id, order, route = await _on_a_route_as_the_current_drop(db_session)
    await _driver_reports_position(hub_id, driver_id, at=FAR_AWAY)
    dropoff = await _dropoff(db_session, order)
    assert dropoff.eta is not None

    tracking = await resolve_tracking(db_session, order.tracking_token)
    portal = await _portal_estimate(db_session, client_id, order)
    driver_view = await get_my_route(driver=_driver(hub_id, driver_id), session=db_session)
    on_the_drivers_list = next(s for s in driver_view.stops if s.stop_id == str(dropoff.id)).eta

    # The position is still shown - the map is unchanged - but it no longer makes
    # its own estimate.
    assert tracking.driver_position is not None
    assert tracking.estimated_arrival == dropoff.eta
    assert portal == dropoff.eta
    assert on_the_drivers_list == dropoff.eta


async def test_a_stale_position_no_longer_shows_an_arrival_already_gone(db_session, real_redis_client):
    """A ping from two hours ago plus a short straight line is a time in the past,
    and the page used to print it as the estimated arrival."""
    hub_id, client_id, driver_id, order, route = await _on_a_route_as_the_current_drop(db_session)
    from app.fleet_state.manager import FleetStateManager
    from app.schemas.fleet import DriverLocation

    await FleetStateManager().update_driver_location(
        DriverLocation(
            driver_id=str(driver_id),
            lat=MY_DROP[0] + 0.01,
            lng=MY_DROP[1],
            recorded_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        ),
        hub_id=str(hub_id),
    )

    tracking = await resolve_tracking(db_session, order.tracking_token)

    assert tracking.estimated_arrival is not None
    assert tracking.estimated_arrival > datetime.now(timezone.utc) - timedelta(minutes=1)


async def test_before_a_route_the_page_shows_the_estimate_the_portal_shows(db_session, real_redis_client):
    """Not the promise. Before a driver has the order both surfaces give the
    straight-line estimate from the collection deadline; the page used to show
    `promised_at` instead, a different number with a different meaning."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    order = await _order(
        db_session,
        hub_id,
        client_id,
        shop_id,
        status=OrderStatus.held,
        promised_at=datetime.now(timezone.utc) + timedelta(hours=6),
    )

    tracking = await resolve_tracking(db_session, order.tracking_token)
    portal = await _portal_estimate(db_session, client_id, order)

    assert portal is not None
    assert tracking.estimated_arrival == portal
    assert tracking.estimated_arrival != order.promised_at


async def test_nothing_is_estimated_once_nothing_is_arriving(db_session, real_redis_client):
    """A cancelled order's drop kept its last forecast, and the portal and the page
    both quoted it."""
    hub_id, client_id, driver_id, order, route = await _on_a_route_as_the_current_drop(db_session)
    order.status = OrderStatus.cancelled
    await db_session.commit()

    tracking = await resolve_tracking(db_session, order.tracking_token)
    portal = await _portal_estimate(db_session, client_id, order)

    assert tracking.estimated_arrival is None
    assert portal is None


# ---------------------------------------------------------------------------
# Moved by where the driver is
# ---------------------------------------------------------------------------


async def _ping(db_session, hub_id, driver_id, at):
    await report_my_location(
        DriverLocationPingBody(lat=at[0], lng=at[1], recorded_at=datetime.now(timezone.utc)),
        driver=_driver(hub_id, driver_id),
        session=db_session,
    )


async def test_a_ping_moves_the_eta_at_most_once_a_minute(db_session, real_redis_client):
    """A driver stuck in traffic moved nobody's ETA until they next tapped
    something. A ping now re-walks the route from where they are - once a minute,
    not thirty times."""
    hub_id, client_id, driver_id, order, route = await _on_a_route_as_the_current_drop(db_session)
    before = await _dropoff(db_session, order)
    planned, first_eta = before.planned_eta, before.eta

    await _ping(db_session, hub_id, driver_id, FAR_AWAY)
    moved = await _dropoff(db_session, order)

    far_leg = minutes_for_miles(miles_between(*FAR_AWAY, *MY_DROP))
    assert moved.eta != first_eta
    assert abs((moved.eta - datetime.now(timezone.utc)).total_seconds() / 60 - far_leg) < 1
    # The prediction made at acceptance is the accuracy measure's; never moved.
    assert moved.planned_eta == planned

    # Thirty seconds later, from somewhere else entirely: inside the minute, so
    # nothing is re-walked.
    await _ping(db_session, hub_id, driver_id, MY_DROP)
    held = await _dropoff(db_session, order)
    assert held.eta == moved.eta
    assert 0 < await real_redis_client.ttl(f"eta_refresh:{route.id}") <= PING_REFRESH_SECONDS


async def test_a_ping_never_waits_for_a_route_somebody_else_is_changing(db_session, real_redis_client):
    """The ping refresh is the one that can always be skipped. One that waited on
    a route a driver or a dispatcher was finishing could deadlock against them; this
    one gives up, and leaves the next ping free to try."""
    hub_id, client_id, driver_id, order, route = await _on_a_route_as_the_current_drop(db_session)
    # Captured now: the refresh that gives up rolls the session back, which
    # expires every instance it holds.
    route_id, order_id = route.id, order.id
    before = (await _dropoff(db_session, order_id)).eta

    async with session_scope() as other:
        await other.execute(select(Route.id).where(Route.id == route_id).with_for_update())
        assert await refresh_after_ping(db_session, uuid.UUID(str(driver_id))) is None
        await other.rollback()

    assert await get_client().get(f"eta_refresh:{route_id}") is None
    assert (await _dropoff(db_session, order_id)).eta == before

    # And with the route free again, the next ping does the work.
    assert (await refresh_after_ping(db_session, uuid.UUID(str(driver_id))))["reason"] == "ok"


async def test_a_driver_with_no_route_pings_as_before(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)

    await _ping(db_session, hub_id, driver_id, FAR_AWAY)

    assert await refresh_after_ping(db_session, uuid.UUID(str(driver_id))) is None
