"""A driver who closed the app stops holding orders.

A driver stays `available` in the fleet state until they go off duty, and plenty
of drivers close the app without doing that. Two things followed:

  - dispatch kept offering them work, since nothing looked at how old their last
    position was;
  - an offer expires only when the driver's app next asks about offers, so one
    sent to a closed app never lapsed, and its orders stayed assigned to someone
    who wasn't there until they opened it again.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.driver_routes import list_my_offers
from app.api.internal_routes import run_dispatch_for_all_hubs
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.models.order import Order, OrderStatus
from app.models.route_offer import RouteOffer
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation
from tests.integration import test_driver_app_integration as driver_app
from tests.integration.queue_helpers import let_the_hold_run_out

pytestmark = pytest.mark.integration


async def _last_reported(hub_id, driver_id, *, ago: timedelta) -> None:
    await FleetStateManager().update_driver_location(
        DriverLocation(
            driver_id=str(driver_id),
            lat=34.0511,
            lng=-118.2511,
            recorded_at=(datetime.now(timezone.utc) - ago).isoformat(),
        ),
        str(hub_id),
    )


STALE = timedelta(seconds=settings.driver_position_stale_after_seconds + 60)


async def test_a_driver_who_stopped_reporting_is_not_offered_work(db_session, real_redis_client):
    hub_id, _client_id, _shop_id, driver_id, _order = await driver_app._seed(db_session)
    await _last_reported(hub_id, driver_id, ago=STALE)
    await let_the_hold_run_out(hub_id)

    plan = await DispatchOptimizerService().plan_cycle(str(hub_id))

    assert plan.drivers == []
    assert plan.assignments == []


async def test_a_driver_still_reporting_is(db_session, real_redis_client):
    hub_id, _client_id, _shop_id, driver_id, _order = await driver_app._seed(db_session)
    await _last_reported(hub_id, driver_id, ago=timedelta(seconds=30))
    await let_the_hold_run_out(hub_id)

    plan = await DispatchOptimizerService().plan_cycle(str(hub_id))

    assert [d.driver_id for d in plan.drivers] == [str(driver_id)]
    assert len(plan.assignments) == 1


async def test_the_sweep_expires_an_offer_nobody_answered(db_session, real_redis_client):
    """The driver's app never asked again, so only the sweep can lapse it."""
    hub_id, _client_id, _shop_id, driver_id, order = await driver_app._seed(db_session)
    await let_the_hold_run_out(hub_id)
    await DispatchOptimizerService().run_cycle(str(hub_id))
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="d")
    [offer] = await list_my_offers(driver=authed, session=db_session)

    # The app was closed: no reports since, and the offer's two minutes are up.
    await _last_reported(hub_id, driver_id, ago=STALE)
    row = await db_session.get(RouteOffer, uuid.UUID(offer.offer_id))
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    result = await run_dispatch_for_all_hubs(session=db_session)

    assert result["offers_expired"] == 1
    status = await db_session.scalar(select(RouteOffer.status).where(RouteOffer.id == row.id))
    assert status == "expired"
    order_status = await db_session.scalar(select(Order.status).where(Order.id == order.id))
    assert order_status == OrderStatus.held
    assert [held.order_id for held in await HoldQueueStore().get_all(str(hub_id))] == [str(order.id)]
