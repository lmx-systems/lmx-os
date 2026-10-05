"""Intake gives the delivery address a dock of its own (IDN-1).

A drop-off had no dock: its stop carries no shop, and reaching through the
order's shop finds the pickup. So the dock survey, taken at the delivery door,
was filed under the distributor's yard, and the receiving door had no row.
"""
import pytest

from app.batch_queue.store import HoldQueueStore
from app.ingestion.service import ingest_lmx_order
from app.models.location import Location
from app.models.shop import Shop
from tests.integration import test_adhoc_pickup as adhoc

pytestmark = pytest.mark.integration


async def test_two_orders_to_one_door_share_its_dock(db_session, real_redis_client):
    hub_id, client_id = await adhoc._seed(db_session)
    geocoder = adhoc.FakeGeocoder()

    first = await ingest_lmx_order(
        db_session, HoldQueueStore(), adhoc._order(hub_id, client_id), geocoder=geocoder
    )
    second = await ingest_lmx_order(
        db_session, HoldQueueStore(), adhoc._order(hub_id, client_id), geocoder=geocoder
    )

    assert first.delivery_location_id is not None
    assert second.delivery_location_id == first.delivery_location_id
    dock = await db_session.get(Location, first.delivery_location_id)
    assert dock.address == "900 Congress Ave, Austin TX"
    assert (float(dock.lat), float(dock.lng)) == (adhoc.DROP_LAT, adhoc.DROP_LNG)


async def test_the_delivery_dock_is_not_the_pickups(db_session, real_redis_client):
    hub_id, client_id = await adhoc._seed(db_session)

    order = await ingest_lmx_order(
        db_session, HoldQueueStore(), adhoc._order(hub_id, client_id), geocoder=adhoc.FakeGeocoder()
    )

    shop = await db_session.get(Shop, order.shop_id)
    assert shop.location_id is not None
    assert order.delivery_location_id is not None
    assert order.delivery_location_id != shop.location_id


async def test_an_address_that_names_no_place_gets_no_dock(db_session, real_redis_client):
    """IDN-1 leaves it unresolved rather than pooling every such address into
    one shared fictional dock. The order is still taken."""
    hub_id, client_id = await adhoc._seed(db_session)

    order = await ingest_lmx_order(
        db_session,
        HoldQueueStore(),
        adhoc._order(hub_id, client_id, drop_address_raw="N/A"),
        geocoder=adhoc.FakeGeocoder(),
    )

    assert order.delivery_location_id is None
    assert order.delivery_address == "N/A"
