"""
Ad-hoc pickup: an order for a place nobody registered
(docs/LMX_LINK_PLAN.md §1.2 "Origin", §2.2 principle 3).

This is the change that makes LMX Link possible - a brand-new client can send
their first order by typing an address, with no shop set up in advance.

The last test in this file is the one that matters most. Pickup location is
Shop-dependent through four layers, and the last of them
(`app/api/driver_routes.py`) falls back to `lat=0.0, lng=0.0` when a pickup stop
has no shop - which renders a driver's stop in the Gulf of Guinea rather than
raising. It is a silent failure, so it needs a loud test.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.api.driver_routes import accept_offer, get_my_route, list_my_offers
from app.batch_queue.store import HoldQueueStore
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.ingestion.service import (
    OriginUnresolvableError,
    ShopNotFoundError,
    ingest_lmx_order,
)
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import OrderStatus
from app.models.shop import Shop
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.lmx_order import LMXOrder

pytestmark = pytest.mark.integration

# A real Austin address and its real coordinates, so a wrong result is obvious.
PICKUP_ADDRESS = "1200 E 6th St, Austin TX"
PICKUP_LAT, PICKUP_LNG = 30.2646, -97.7302
DROP_LAT, DROP_LNG = 30.2729, -97.7414


class FakeGeocoder(BaseGeocoder):
    provider_name = "fake"

    def __init__(self, result: GeocodeResult | None = None) -> None:
        self.result = (
            result
            if result is not None
            else GeocodeResult(
                lat=PICKUP_LAT, lng=PICKUP_LNG, display_name=PICKUP_ADDRESS, provider="fake"
            )
        )
        self.calls: list[str] = []

    async def geocode(self, address: str) -> GeocodeResult | None:
        self.calls.append(address)
        return self.result


class FailingGeocoder(BaseGeocoder):
    provider_name = "fake"

    async def geocode(self, address: str) -> GeocodeResult | None:
        return None


async def _seed(db_session):
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()
    return hub_id, client_id


def _order(hub_id, client_id, **overrides) -> LMXOrder:
    payload = dict(
        source_system="client_portal",
        source_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        hub_id=str(hub_id),
        client_id=str(client_id),
        pickup_address=PICKUP_ADDRESS,
        drop_address_raw="900 Congress Ave, Austin TX",
        drop_lat=DROP_LAT,
        drop_lng=DROP_LNG,
        received_at=datetime.now(timezone.utc),
    )
    payload.update(overrides)
    return LMXOrder(**payload)


# ---------------------------------------------------------------------------
# Creating a shop from a typed address
# ---------------------------------------------------------------------------


async def test_a_typed_address_becomes_a_real_shop_with_real_coordinates(db_session, real_redis_client):
    hub_id, client_id = await _seed(db_session)
    geocoder = FakeGeocoder()

    order = await ingest_lmx_order(
        db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=geocoder
    )

    shop = await db_session.get(Shop, order.shop_id)
    assert shop is not None
    assert shop.lat == pytest.approx(PICKUP_LAT)
    assert shop.lng == pytest.approx(PICKUP_LNG)
    # Marked as system-created so ops can tell these apart in shop_profiles.
    assert shop.external_ref.startswith("lmxlink:")
    # The raw typed address stays on the order too - it is what the customer
    # actually wrote, and the shop name should read the way they think of it.
    assert order.pickup_address == PICKUP_ADDRESS
    assert shop.name == PICKUP_ADDRESS


async def test_the_second_order_to_the_same_address_reuses_the_shop(db_session, real_redis_client):
    """§2.2 principle 3: "second order to the same shop is two taps". Also what
    keeps a 1-req/sec geocoder viable."""
    hub_id, client_id = await _seed(db_session)
    geocoder = FakeGeocoder()
    queue = HoldQueueStore()

    first = await ingest_lmx_order(db_session, queue, _order(hub_id, client_id), geocoder=geocoder)
    second = await ingest_lmx_order(db_session, queue, _order(hub_id, client_id), geocoder=geocoder)

    assert first.shop_id == second.shop_id
    assert len(geocoder.calls) == 1, "the second order must not re-geocode"

    shop_count = (
        await db_session.execute(
            select(func.count()).select_from(Shop).where(Shop.client_id == client_id)
        )
    ).scalar_one()
    assert shop_count == 1


async def test_a_differently_typed_version_of_the_same_address_still_reuses_it(db_session, real_redis_client):
    """Case and spacing vary between two entries of the same place by the same
    person - the dedupe key is the normalized form."""
    hub_id, client_id = await _seed(db_session)
    geocoder = FakeGeocoder()
    queue = HoldQueueStore()

    first = await ingest_lmx_order(db_session, queue, _order(hub_id, client_id), geocoder=geocoder)
    second = await ingest_lmx_order(
        db_session,
        queue,
        _order(hub_id, client_id, pickup_address="  1200 e 6th st, AUSTIN tx "),
        geocoder=geocoder,
    )

    assert first.shop_id == second.shop_id


async def test_two_different_addresses_create_two_shops(db_session, real_redis_client):
    hub_id, client_id = await _seed(db_session)
    queue = HoldQueueStore()

    a = await ingest_lmx_order(
        db_session, queue, _order(hub_id, client_id), geocoder=FakeGeocoder()
    )
    b = await ingest_lmx_order(
        db_session,
        queue,
        _order(hub_id, client_id, pickup_address="500 W 2nd St, Austin TX"),
        geocoder=FakeGeocoder(
            GeocodeResult(lat=30.2650, lng=-97.7500, display_name="500 W 2nd", provider="fake")
        ),
    )

    assert a.shop_id != b.shop_id


async def test_coordinates_supplied_directly_skip_geocoding(db_session, real_redis_client):
    """A source that already knows where the pickup is shouldn't pay for a
    lookup - or consume a slot against a rate-limited provider."""
    hub_id, client_id = await _seed(db_session)
    geocoder = FakeGeocoder()

    order = await ingest_lmx_order(
        db_session,
        HoldQueueStore(),
        _order(hub_id, client_id, pickup_lat=PICKUP_LAT, pickup_lng=PICKUP_LNG),
        geocoder=geocoder,
    )

    shop = await db_session.get(Shop, order.shop_id)
    assert shop.lat == pytest.approx(PICKUP_LAT)
    assert geocoder.calls == []


# ---------------------------------------------------------------------------
# Failure is loud
# ---------------------------------------------------------------------------


async def test_an_unresolvable_address_refuses_the_order(db_session, real_redis_client):
    """Refusing beats accepting a silently undeliverable order. Without
    coordinates the queue can't cluster it, the optimizer can't route it, and the
    driver app renders its pickup at 0.0/0.0."""
    hub_id, client_id = await _seed(db_session)

    with pytest.raises(OriginUnresolvableError):
        await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FailingGeocoder()
        )


async def test_a_registered_shop_that_does_not_exist_still_raises(db_session, real_redis_client):
    """The pre-existing path is untouched - naming a shop that isn't there is
    still an error, not a silent auto-create. An adapter naming an unknown shop
    is a real integration fault worth surfacing."""
    hub_id, client_id = await _seed(db_session)

    with pytest.raises(ShopNotFoundError):
        await ingest_lmx_order(
            db_session,
            HoldQueueStore(),
            _order(hub_id, client_id, pickup_address=None, shop_external_ref="NOPE-1"),
            geocoder=FakeGeocoder(),
        )


# ---------------------------------------------------------------------------
# Commitment ownership (§1.3)
# ---------------------------------------------------------------------------


async def test_an_lmx_order_is_classified(db_session, real_redis_client):
    hub_id, client_id = await _seed(db_session)

    order = await ingest_lmx_order(
        db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FakeGeocoder()
    )

    assert order.sla_owner == "LMX"
    assert order.sla_tier is not None
    assert order.hold_deadline is not None


async def test_an_external_order_is_not_classified_and_keeps_its_given_window(db_session, real_redis_client):
    """The case the whole sla_owner split exists for. Someone else promised the
    customer a window; we enforce it rather than computing our own."""
    hub_id, client_id = await _seed(db_session)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=45)

    order = await ingest_lmx_order(
        db_session,
        HoldQueueStore(),
        _order(
            hub_id,
            client_id,
            sla_owner="EXTERNAL",
            delivery_window_start=datetime.now(timezone.utc),
            delivery_window_end=window_end,
        ),
        geocoder=FakeGeocoder(),
    )

    assert order.sla_owner == "EXTERNAL"
    # The external window became the deadline the hold queue holds against -
    # §1.3's claim that the queue needs no changes for an external path.
    assert order.hold_deadline == window_end
    assert order.delivery_window_end == window_end


# ---------------------------------------------------------------------------
# The regression that matters
# ---------------------------------------------------------------------------


async def test_an_adhoc_order_reaches_the_driver_at_real_coordinates(db_session, real_redis_client):
    """**The 0.0/0.0 guard.**

    `app/api/driver_routes.py` builds a pickup stop's coordinates as
    `shop.lat if shop else 0.0`. A shopless pickup therefore does not error - it
    renders at latitude 0, longitude 0, several hundred miles off the coast of
    West Africa. A driver would open their app to a stop in the Gulf of Guinea.

    This walks the whole path an ad-hoc order takes - ingest, dispatch cycle,
    offer, accept, route view - and asserts the driver sees the address the
    client actually typed.
    """
    hub_id, client_id = await _seed(db_session)
    driver_id = uuid.uuid4()
    db_session.add(
        Driver(
            id=driver_id, hub_id=hub_id, name="Sam O.",
            phone=f"+1512555{uuid.uuid4().int % 10000:04d}", vehicle_capacity_units=5,
        )
    )
    await db_session.commit()

    fleet = FleetStateManager()
    await fleet.upsert_driver_state(
        DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5)
    )
    await fleet.update_driver_location(
        DriverLocation(
            driver_id=str(driver_id), lat=PICKUP_LAT, lng=PICKUP_LNG,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        str(hub_id),
    )

    order = await ingest_lmx_order(
        db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FakeGeocoder()
    )

    result = await DispatchOptimizerService().run_cycle(str(hub_id))
    assert len(result.assignments) == 1, "an ad-hoc order must be dispatchable"

    driver = AuthedDriver(
        driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device"
    )
    offers = await list_my_offers(driver=driver, session=db_session)
    assert len(offers) == 1
    await accept_offer(offers[0].offer_id, driver=driver, session=db_session)

    route = await get_my_route(driver=driver, session=db_session)
    assert route is not None
    pickups = [s for s in route.stops if s.stop_type == "pickup"]
    assert len(pickups) == 1

    pickup = pickups[0]
    # The actual assertion. Not 0.0/0.0.
    assert pickup.lat == pytest.approx(PICKUP_LAT, abs=1e-4)
    assert pickup.lng == pytest.approx(PICKUP_LNG, abs=1e-4)
    assert (pickup.lat, pickup.lng) != (0.0, 0.0)

    # refresh(), not get(): the optimizer writes the status from its own session,
    # so this session's identity map still holds the `held` instance it created
    # during ingestion. get() would return that stale object and the assertion
    # would pass or fail for the wrong reason.
    await db_session.refresh(order)
    # en_route_pickup, not assigned: accepting the offer means a driver has taken
    # the job and is on their way (§1.4).
    assert order.status == OrderStatus.en_route_pickup
