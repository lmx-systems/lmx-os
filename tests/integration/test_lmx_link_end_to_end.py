"""
The LMX Link walkthrough, end to end (docs/LMX_LINK_PLAN.md).

This is the demo the plan was written to reach, as a test:

    Someone signs up on a public URL -> LMX approves them and sets their rates
    -> they log in and submit an order, typing a pickup address nobody has ever
    registered -> a dispatch cycle assigns it -> a driver accepts, collects and
    delivers it -> the client watches the status move the whole way.

Every other test file here covers one link in that chain. This one exists
because a chain of individually-passing links is not the same as a working
chain, and the failure mode this guards against is precisely the one that has
bitten twice already during this build: a stale read making a legal transition
silently skip, and a shopless pickup rendering at 0.0/0.0 without raising.

If this test fails, the demo is broken - regardless of what the unit tests say.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.api.admin_routes import approve_signup, list_signups
from app.api.client_routes import list_my_orders, submit_order
from app.api.driver_routes import (
    accept_offer,
    arrive_at_stop,
    complete_stop,
    get_my_route,
    list_my_offers,
    scan_parcels,
)
from app.api.public_routes import client_signup
from app.client_auth.dependencies import AuthedClient
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.models.client import Client
from app.models.client_user import ClientUser
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.optimizer.service import DispatchOptimizerService
from app.orders.state_machine import public_label
from app.schemas.client_order import ClientOrderBody
from app.schemas.driver_app import CompleteStopBody, ScanParcelsBody
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.signup import ApproveRateInput, ApproveSignupBody, ClientSignupBody

pytestmark = pytest.mark.integration

PICKUP = "1200 E 6th St, Austin TX"
DROP = "900 Congress Ave, Austin TX"
COORDS = {
    PICKUP: (30.2646, -97.7302),
    DROP: (30.2729, -97.7414),
}


class MapGeocoder(BaseGeocoder):
    """Resolves the two addresses in this walkthrough and nothing else."""

    provider_name = "fake"

    async def geocode(self, address: str) -> GeocodeResult | None:
        for known, (lat, lng) in COORDS.items():
            if address.strip().casefold() == known.casefold():
                return GeocodeResult(lat=lat, lng=lng, display_name=known, provider="fake")
        return None


class _Request:
    def __init__(self, ip: str) -> None:
        self.client = type("C", (), {"host": ip})()


async def test_signup_to_delivered(db_session, real_redis_client, monkeypatch):
    import app.api.client_routes as client_routes

    monkeypatch.setattr(client_routes, "get_geocoder", lambda: MapGeocoder())

    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()

    # ---- 1. A distributor finds the signup URL and applies -------------
    signup_result = await client_signup(
        ClientSignupBody(
            company_name="Midtown Auto Parts",
            contact_name="Jordan Rivera",
            contact_email=f"jordan-{uuid.uuid4().hex[:6]}@example.com",
            contact_phone="+15125550142",
            service_area="Austin metro",
            password="a-long-enough-password",
            terms_version="v1",
            accepted_terms=True,
        ),
        _Request("198.51.100.7"),
        session=db_session,
    )
    assert signup_result.status == "pending"

    # They cannot do anything yet - their login is inactive.
    applicant = (await db_session.execute(select(ClientUser))).scalar_one()
    assert applicant.is_active is False

    # ---- 2. LMX reviews the queue and approves with rates --------------
    queue = await list_signups(session=db_session, _admin=None)
    assert [s.company_name for s in queue] == ["Midtown Auto Parts"]
    assert queue[0].service_area == "Austin metro"

    await approve_signup(
        queue[0].client_id,
        ApproveSignupBody(
            rates=[
                ApproveRateInput(sla_tier="HOT_SHOT", rate_per_drop_cents=3500),
                ApproveRateInput(sla_tier="T1", rate_per_drop_cents=1800),
                ApproveRateInput(sla_tier="T2", rate_per_drop_cents=1200),
                ApproveRateInput(sla_tier="T3", rate_per_drop_cents=900),
            ],
            hub_id=str(hub_id),
        ),
        session=db_session,
        _admin=None,
    )

    client_row = (await db_session.execute(select(Client))).scalar_one()
    await db_session.refresh(client_row)
    assert client_row.signup_status == "active"
    await db_session.refresh(applicant)
    assert applicant.is_active is True, "approval must let them sign in"

    client = AuthedClient(
        client_user_id=str(applicant.id),
        client_id=str(client_row.id),
        role="admin",
        email=applicant.email,
        name=applicant.name,
    )

    # ---- 3. They submit an order for an address nobody registered ------
    placed = await submit_order(
        ClientOrderBody(
            pickup_address=PICKUP,
            drop_address=DROP,
            drop_contact_name="Rivera Motors",
            deadline="within_the_hour",
            entry_seconds=41,
        ),
        client=client,
        session=db_session,
    )

    assert placed.sla_tier == "T1", "'within the hour' is an urgent order"
    assert placed.fee_cents == 1800, "priced from the rates set at approval"
    assert placed.dispatchable is True, "both ends geocoded"
    assert placed.collect_by is not None, "the confirmation states a commitment"

    # The typed pickup is now a remembered shop with real coordinates.
    shop = (await db_session.execute(select(Shop))).scalar_one()
    assert shop.lat == pytest.approx(COORDS[PICKUP][0])
    assert shop.external_ref.startswith("lmxlink:")

    order_id = uuid.UUID(placed.order_id)
    order = await db_session.get(Order, order_id)
    assert order.status == OrderStatus.held

    # ---- 4. A driver comes on shift ------------------------------------
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
        DriverState(
            driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5
        )
    )
    await fleet.update_driver_location(
        DriverLocation(
            driver_id=str(driver_id),
            lat=COORDS[PICKUP][0],
            lng=COORDS[PICKUP][1],
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        str(hub_id),
    )

    # ---- 5. Dispatch assigns it ----------------------------------------
    cycle = await DispatchOptimizerService().run_cycle(str(hub_id))
    assert len(cycle.assignments) == 1

    driver = AuthedDriver(
        driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device"
    )
    offers = await list_my_offers(driver=driver, session=db_session)
    assert len(offers) == 1

    # ---- 6. The driver accepts, and the client sees it move ------------
    await accept_offer(offers[0].offer_id, driver=driver, session=db_session)
    await db_session.refresh(order)
    assert order.status == OrderStatus.en_route_pickup
    assert public_label(order.status) == "EN_ROUTE_PICKUP"

    route = await get_my_route(driver=driver, session=db_session)
    pickup_stop = next(s for s in route.stops if s.stop_type == "pickup")
    dropoff_stop = next(s for s in route.stops if s.stop_type == "dropoff")

    # THE 0.0/0.0 GUARD, in the real walkthrough: the driver's pickup is the
    # address the client typed, not the Gulf of Guinea.
    assert pickup_stop.lat == pytest.approx(COORDS[PICKUP][0], abs=1e-4)
    assert pickup_stop.lng == pytest.approx(COORDS[PICKUP][1], abs=1e-4)

    # ---- 7. Collect ----------------------------------------------------
    await arrive_at_stop(pickup_stop.stop_id, driver=driver, session=db_session)
    # A pickup must be fully scanned before it can be completed (W10).
    await scan_parcels(
        pickup_stop.stop_id,
        ScanParcelsBody(scanned_count=pickup_stop.parcel_count),
        driver=driver,
        session=db_session,
    )
    await complete_stop(
        pickup_stop.stop_id,
        CompleteStopBody(method="photo", photo_url="https://example.com/pickup.jpg"),
        driver=driver,
        session=db_session,
    )

    await db_session.refresh(order)
    assert order.status == OrderStatus.picked_up, "the client can now see it's collected"

    # ---- 8. Deliver ----------------------------------------------------
    await arrive_at_stop(dropoff_stop.stop_id, driver=driver, session=db_session)
    await complete_stop(
        dropoff_stop.stop_id,
        CompleteStopBody(method="photo", photo_url="https://example.com/pod.jpg"),
        driver=driver,
        session=db_session,
    )

    await db_session.refresh(order)
    assert order.status == OrderStatus.delivered
    assert order.delivered_at is not None, "ground truth, written once (I1)"

    # ---- 9. The client sees it in their own portal ---------------------
    their_orders = await list_my_orders(client=client, session=db_session)
    assert len(their_orders) == 1
    assert their_orders[0].status == "delivered"
