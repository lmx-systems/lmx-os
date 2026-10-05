"""A client's webhook hears about an order from the moment it lands.

Status changes reach the webhook sink through `advance_orders`. Three writes went
around it, so the events they stand for were never sent:

  - intake wrote `held` directly, so no client heard that its order had arrived;
  - the dispatch cycle marked orders `assigned` with a plain UPDATE, so nobody
    ever got ASSIGNED;
  - a declined or lapsed offer put its orders back to `held` with another UPDATE,
    so a client told ASSIGNED was never told otherwise.

"Status write-back gets equal weight to intake" (CLAUDE.md): a carrier that
accepts orders and goes quiet is a favour, not a carrier.
"""
import pytest
from sqlalchemy import select

from app.api.driver_routes import decline_offer, list_my_offers
from app.batch_queue.store import HoldQueueStore
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.ingestion.service import ingest_lmx_order
from app.models.client_webhook import ClientWebhookEndpoint, WebhookDelivery, new_webhook_secret
from app.models.order import INTAKE_BACKFILL
from app.optimizer.event_trigger import dispatch_event_bus
from app.optimizer.service import DispatchOptimizerService
from app.schemas.driver_app import DeclineOfferBody
from tests.integration import test_adhoc_pickup as adhoc
from tests.integration import test_driver_app_integration as driver_app
from tests.integration.queue_helpers import let_the_hold_run_out

pytestmark = pytest.mark.integration


async def _endpoint(db_session, client_id) -> ClientWebhookEndpoint:
    endpoint = ClientWebhookEndpoint(
        client_id=client_id, url="https://consumer.example.com/lmx", secret=new_webhook_secret()
    )
    db_session.add(endpoint)
    await db_session.commit()
    return endpoint


async def _events(db_session) -> list[tuple[str, str]]:
    rows = (
        await db_session.execute(select(WebhookDelivery).order_by(WebhookDelivery.sequence))
    ).scalars()
    return [(row.payload["previous_status"], row.payload["status"]) for row in rows]


async def test_a_new_order_tells_the_client_it_is_held(db_session, real_redis_client):
    hub_id, client_id = await adhoc._seed(db_session)
    await _endpoint(db_session, client_id)

    await ingest_lmx_order(
        db_session, HoldQueueStore(), adhoc._order(hub_id, client_id), geocoder=adhoc.FakeGeocoder()
    )

    assert await _events(db_session) == [("RECEIVED", "HELD")]


async def test_a_backfilled_order_tells_the_client_nothing(db_session, real_redis_client):
    """History. The client has no use for news of a delivery made weeks ago."""
    hub_id, client_id = await adhoc._seed(db_session)
    await _endpoint(db_session, client_id)

    await ingest_lmx_order(
        db_session,
        HoldQueueStore(),
        adhoc._order(hub_id, client_id),
        geocoder=adhoc.FakeGeocoder(),
        mode=INTAKE_BACKFILL,
    )

    assert await _events(db_session) == []


async def test_dispatch_tells_the_client_it_is_assigned(db_session, real_redis_client):
    hub_id, client_id, _shop_id, _driver_id, _order = await driver_app._seed(db_session)
    await _endpoint(db_session, client_id)

    await let_the_hold_run_out(hub_id)
    await DispatchOptimizerService().run_cycle(str(hub_id))

    assert await _events(db_session) == [("HELD", "ASSIGNED")]


async def test_a_declined_offer_tells_the_client_it_is_held_again(db_session, real_redis_client):
    hub_id, client_id, _shop_id, driver_id, _order = await driver_app._seed(db_session)
    await _endpoint(db_session, client_id)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await let_the_hold_run_out(hub_id)
    await DispatchOptimizerService().run_cycle(str(hub_id))
    [offer] = await list_my_offers(driver=authed, session=db_session)

    # Off shift first, so the decline's own cycle can't offer it straight back.
    fleet = FleetStateManager()
    state = await fleet.get_driver_state(str(hub_id), str(driver_id))
    state.status = "on_break"
    await fleet.upsert_driver_state(state)
    await decline_offer(
        offer.offer_id, driver=authed, session=db_session, body=DeclineOfferBody(reason="too_far")
    )
    await dispatch_event_bus.wait_idle()

    assert await _events(db_session) == [("HELD", "ASSIGNED"), ("ASSIGNED", "HELD")]


async def test_an_order_never_assigned_is_not_told_it_was(db_session, real_redis_client):
    """The cycle used to overwrite any status with `assigned`. Through the state
    machine, an order already cancelled stays cancelled and nobody hears
    otherwise."""
    from app.models.order import Order, OrderStatus

    hub_id, client_id, _shop_id, _driver_id, order = await driver_app._seed(db_session)
    await _endpoint(db_session, client_id)
    order.status = OrderStatus.cancelled
    await db_session.commit()

    await let_the_hold_run_out(hub_id)
    await DispatchOptimizerService().run_cycle(str(hub_id))

    refreshed = await db_session.scalar(select(Order.status).where(Order.id == order.id))
    assert refreshed == OrderStatus.cancelled
    assert await _events(db_session) == []
