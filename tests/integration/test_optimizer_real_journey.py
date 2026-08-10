"""
The routing solver plans the whole journey, not half of it
(docs/ROADMAP.md E1) - against real Postgres + Redis.

**The defect this closes.** `StopCandidate.lat/lng` is the SHOP, i.e. the pickup
(see `app/optimizer/service.py`'s candidate construction), and the Google Route
Optimization request sent it as the shipment's only `deliveries` entry. So the
solver was told every job both began and ended on collection: the delivery drive
was never costed, sequencing never considered where the van actually had to go
next, and `considerRoadTraffic: True` bought accurate traffic data for legs that
weren't in the model. The response parses perfectly either way, which is exactly
why this survived a full unit-test suite and would have shipped as "verified".

Unit coverage for the request shape lives in `tests/test_optimizer_google_client.py`.
These tests exist for the part unit tests can't reach: the delivery location has
to travel from an ingested order, through a JSON round-trip in Redis, into the
candidate handed to the client. Two links in that chain fail only at runtime -
`Order.delivery_lat` is `Numeric(9,6)` and comes back as a `Decimal` that
`json.dumps` refuses, and a queue row written before these fields existed has no
such keys at all.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.fleet_state.manager import FleetStateManager
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.ingestion.service import ingest_lmx_order
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.optimizer.google_routes_client import (
    GoogleRouteOptimizationClient,
    RouteOptimizationClient,
)
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.lmx_order import LMXOrder
from app.schemas.optimizer import DriverCandidate, StopCandidate

pytestmark = pytest.mark.integration

PICKUP_ADDRESS = "1200 E 6th St, Austin TX"
PICKUP_LAT, PICKUP_LNG = 30.264642, -97.730218
DROP_LAT, DROP_LNG = 30.274500, -97.740300


class _FakeGeocoder(BaseGeocoder):
    provider_name = "fake"

    async def geocode(self, address: str) -> GeocodeResult | None:
        return GeocodeResult(
            lat=PICKUP_LAT, lng=PICKUP_LNG, display_name=address, provider="fake"
        )


class _SpyRouteClient(RouteOptimizationClient):
    """Captures exactly what the optimizer hands the routing provider."""

    engine_name = "spy"

    def __init__(self) -> None:
        self.stops: list[StopCandidate] = []
        self.drivers: list[DriverCandidate] = []

    async def optimize(self, drivers, stops):
        self.drivers = list(drivers)
        self.stops = list(stops)
        return [], [s.stop_id for s in stops]


async def _seed(db_session):
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file")
    )
    await db_session.commit()
    return hub_id, client_id


async def _seed_available_driver(db_session, hub_id):
    """A driver the optimizer can actually see. `optimize` is only called when
    there is at least one driver WITH a known position, so both the fleet state and
    the location ping are required for the spy to be reached at all."""
    driver_id = uuid.uuid4()
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

    fleet = FleetStateManager()
    await fleet.upsert_driver_state(
        DriverState(
            driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5
        )
    )
    await fleet.update_driver_location(
        DriverLocation(
            driver_id=str(driver_id),
            lat=30.26,
            lng=-97.73,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        hub_id=str(hub_id),
    )
    return driver_id


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
# Ingestion -> Redis
# ---------------------------------------------------------------------------


async def test_an_ingested_order_carries_its_drop_into_the_hold_queue(
    db_session, real_redis_client
):
    """**The Decimal trap.** `Order.delivery_lat` is Numeric(9,6), so SQLAlchemy
    hands back a `Decimal` - and `json.dumps` raises TypeError on one. Without the
    float() coercion at the construction site this doesn't degrade, it takes down
    order ingestion outright."""
    hub_id, client_id = await _seed(db_session)
    queue = HoldQueueStore()

    await ingest_lmx_order(
        db_session, queue, _order(hub_id, client_id), geocoder=_FakeGeocoder()
    )

    held = (await queue.get_all(str(hub_id)))[0]
    assert held.delivery_lat == pytest.approx(DROP_LAT)
    assert held.delivery_lng == pytest.approx(DROP_LNG)
    # Not a Decimal, or the row could never have been written in the first place.
    assert isinstance(held.delivery_lat, float)


# ---------------------------------------------------------------------------
# Redis -> the routing provider
# ---------------------------------------------------------------------------


async def test_the_candidate_handed_to_the_solver_knows_both_ends(
    db_session, real_redis_client
):
    """The link the unit tests can't see: a drop location that survives the JSON
    round-trip but never reaches `StopCandidate` leaves the solver planning a
    collection and no delivery."""
    hub_id, client_id = await _seed(db_session)
    await _seed_available_driver(db_session, hub_id)
    now = datetime.now(timezone.utc)

    # Deadline already passed, so the hold cycle force-releases it this cycle
    # rather than holding it for a cluster-mate that will never arrive.
    await HoldQueueStore().add(
        str(hub_id),
        HeldOrder(
            order_id=str(uuid.uuid4()),
            shop_lat=PICKUP_LAT,
            shop_lng=PICKUP_LNG,
            sla_tier="T2",
            hold_deadline=now - timedelta(minutes=1),
            held_since=now - timedelta(minutes=10),
            shop_name=PICKUP_ADDRESS,
            delivery_lat=DROP_LAT,
            delivery_lng=DROP_LNG,
        ),
    )

    spy = _SpyRouteClient()
    await DispatchOptimizerService(route_client=spy).run_cycle(str(hub_id))

    assert len(spy.stops) == 1, "the order should have been released to the solver"
    stop = spy.stops[0]
    assert (stop.lat, stop.lng) == pytest.approx((PICKUP_LAT, PICKUP_LNG))
    assert (stop.delivery_lat, stop.delivery_lng) == pytest.approx((DROP_LAT, DROP_LNG))
    assert stop.has_delivery_location


async def test_that_candidate_produces_a_two_legged_shipment(db_session, real_redis_client):
    """Closes the loop: the request Google would actually receive for a real
    ingested order contains the collection AND the drop, at the right coordinates
    and the right way round."""
    hub_id, client_id = await _seed(db_session)
    await _seed_available_driver(db_session, hub_id)
    queue = HoldQueueStore()

    await ingest_lmx_order(
        db_session, queue, _order(hub_id, client_id), geocoder=_FakeGeocoder()
    )
    # Force the release rather than waiting out the SLA hold.
    held = (await queue.get_all(str(hub_id)))[0]
    await queue.add(
        str(hub_id),
        HeldOrder(
            order_id=held.order_id,
            shop_lat=held.shop_lat,
            shop_lng=held.shop_lng,
            sla_tier=held.sla_tier,
            hold_deadline=datetime.now(timezone.utc) - timedelta(minutes=1),
            held_since=held.held_since,
            shop_name=held.shop_name,
            delivery_lat=held.delivery_lat,
            delivery_lng=held.delivery_lng,
        ),
    )

    spy = _SpyRouteClient()
    await DispatchOptimizerService(route_client=spy).run_cycle(str(hub_id))
    assert spy.stops, "nothing reached the solver"

    body = GoogleRouteOptimizationClient._build_request(spy.drivers, spy.stops)
    shipment = body["model"]["shipments"][0]

    assert shipment["pickups"][0]["arrivalLocation"] == {
        "latitude": pytest.approx(PICKUP_LAT),
        "longitude": pytest.approx(PICKUP_LNG),
    }
    assert shipment["deliveries"][0]["arrivalLocation"] == {
        "latitude": pytest.approx(DROP_LAT),
        "longitude": pytest.approx(DROP_LNG),
    }
    # And the solver has an objective to minimise while doing it.
    assert body["model"]["vehicles"][0]["costPerKilometer"] > 0
