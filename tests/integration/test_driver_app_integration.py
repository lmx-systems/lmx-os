"""
End-to-end coverage for the driver app's Phase 1 core loop against real
Postgres + Redis: OTP login -> profile setup -> go online -> a Dispatch
Optimizer cycle creates a job offer -> accept (creates a real Route +
pickup/dropoff Stops for the first time in this codebase) -> pickup
scan/complete -> dropoff complete -> order delivered, route + driver
freed up. See docs/NEXT_STEPS.md item 12.

Calls the route functions directly, same pattern as
tests/integration/test_dashboard_enrichment.py and
tests/integration/test_end_to_end_pipeline.py.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api.driver_routes import (
    accept_offer,
    arrive_at_stop,
    complete_stop,
    decline_offer,
    get_my_profile,
    get_my_route,
    list_my_offers,
    request_otp,
    scan_parcels,
    update_my_availability,
    update_my_profile,
    verify_otp,
)
from app.batch_queue.store import HoldQueueStore
from app.batch_queue.queue import HeldOrder
from app.driver_auth.dependencies import AuthedDriver
from app.driver_auth.tokens import decode_token
from app.fleet_state.manager import FleetStateManager
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.optimizer.event_trigger import dispatch_event_bus
from app.optimizer.service import DispatchOptimizerService
from app.schemas.driver_app import CompleteStopBody, DriverAvailabilityUpdate, DriverProfileUpdate, ScanParcelsBody
from app.schemas.driver_auth import RequestOtpBody, VerifyOtpBody
from app.schemas.fleet import DriverLocation, DriverState

pytestmark = pytest.mark.integration


async def _seed(db_session):
    hub_id, client_id, shop_id, driver_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Driver App Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()

    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()

    db_session.add_all(
        [
            Shop(
                id=shop_id, client_id=client_id, name="Midtown Auto Parts", address="220 Harbor St",
                lat=34.051, lng=-118.251, external_ref="SHOP-DRIVER-APP",
            ),
            Driver(id=driver_id, hub_id=hub_id, name="Jordan P.", phone="+15555550199", vehicle_capacity_units=5),
        ]
    )
    await db_session.commit()

    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref="ORD-DRIVER-APP-1", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now + timedelta(minutes=30), weight_units=1,
        status=OrderStatus.held, requested_at=now,
        delivery_address="14 Oak Ave, Apt 3", delivery_lat=34.0530, delivery_lng=-118.2530,
        delivery_contact_name="J. Rivera", delivery_contact_phone="+15555550188",
        delivery_notes="Leave at door, ring bell",
    )
    db_session.add(order)
    await db_session.commit()

    hold_queue = HoldQueueStore()
    await hold_queue.add(
        str(hub_id),
        HeldOrder(
            order_id=str(order.id), shop_lat=34.051, shop_lng=-118.251, sla_tier="T2",
            hold_deadline=now + timedelta(minutes=30), held_since=now, shop_name="Midtown Auto Parts",
        ),
    )

    fleet_state = FleetStateManager()
    await fleet_state.upsert_driver_state(
        DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5)
    )
    await fleet_state.update_driver_location(
        DriverLocation(driver_id=str(driver_id), lat=34.0511, lng=-118.2511, recorded_at=now.isoformat()),
        str(hub_id),
    )

    return hub_id, client_id, shop_id, driver_id, order


async def test_full_driver_app_core_loop(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)

    # 1. Phone + OTP login (screens 1a/1b).
    otp_result = await request_otp(RequestOtpBody(phone="+15555550199"), session=db_session)
    assert otp_result.debug_code is not None  # no Twilio configured in tests

    token = await verify_otp(VerifyOtpBody(phone="+15555550199", code=otp_result.debug_code), session=db_session)
    decoded_driver_id, decoded_hub_id = decode_token(token.access_token)
    assert decoded_driver_id == str(driver_id)
    authed = AuthedDriver(driver_id=decoded_driver_id, hub_id=decoded_hub_id)

    profile = await get_my_profile(driver=authed, session=db_session)
    assert profile.setup_complete is False  # no vehicle_type yet

    # 2. Vehicle & profile setup (screen 1c).
    updated_profile = await update_my_profile(
        DriverProfileUpdate(vehicle_type="car", plate_number="ABC-1234", delivery_zone="Downtown - Zone 4"),
        driver=authed, session=db_session,
    )
    assert updated_profile.setup_complete is True

    # 3. Go online (screens 1d/1e).
    await update_my_availability(DriverAvailabilityUpdate(status="available"), driver=authed, session=db_session)

    # 4. A Dispatch Optimizer cycle should create a job offer, not directly
    # hand the driver a route (docs/NEXT_STEPS.md item 12's accept/decline gap).
    result = await DispatchOptimizerService().run_cycle(str(hub_id))
    assert len(result.assignments) == 1
    assert result.assignments[0].driver_id == str(driver_id)

    offers = await list_my_offers(driver=authed, session=db_session)
    assert len(offers) == 1
    offer = offers[0]
    assert offer.stops[0].order_id == str(order.id)
    assert offer.stops[0].shop_name == "Midtown Auto Parts"

    # No Route/Stop exists yet - only accepting an offer creates them.
    assert await get_my_route(driver=authed, session=db_session) is None

    # 5. Accept (screen 1g) - creates the real Route: one pickup stop
    # (Midtown Auto Parts) + one dropoff stop (14 Oak Ave).
    route = await accept_offer(offer.offer_id, driver=authed, session=db_session)
    assert route.status == "active"
    assert len(route.stops) == 2
    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    dropoff = next(s for s in route.stops if s.stop_type == "dropoff")
    assert pickup.shop_name == "Midtown Auto Parts"
    assert pickup.order_ids == [str(order.id)]
    assert dropoff.address == "14 Oak Ave, Apt 3"
    assert dropoff.contact_name == "J. Rivera"
    assert dropoff.order_ids == [str(order.id)]

    # 6. Pickup: arrive, scan, complete (screens 1h-1k).
    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    scanned = await scan_parcels(pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    assert scanned.scanned_count == 1
    await complete_stop(
        pickup.stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/pickup.jpg"),
        driver=authed, session=db_session,
    )

    # Picking up doesn't mark the order delivered - only the dropoff does.
    await db_session.refresh(order)
    assert order.status == OrderStatus.assigned

    # 7. Dropoff: arrive, proof of delivery (screens 1l/1m).
    await arrive_at_stop(dropoff.stop_id, driver=authed, session=db_session)
    final_view = await complete_stop(
        dropoff.stop_id, CompleteStopBody(method="signature", signature_url="https://example.com/sig.png"),
        driver=authed, session=db_session,
    )
    assert final_view.status == "completed"

    await db_session.refresh(order)
    assert order.status == OrderStatus.delivered

    # 8. Whole route wraps up, driver is freed back to available.
    assert await get_my_route(driver=authed, session=db_session) is None
    fleet_state = FleetStateManager()
    final_state = await fleet_state.get_driver_state(str(hub_id), str(driver_id))
    assert final_state.status == "available"
    assert final_state.current_route_id is None


async def test_declined_offer_requeues_order_for_reassignment(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id))

    await DispatchOptimizerService().run_cycle(str(hub_id))
    offers = await list_my_offers(driver=authed, session=db_session)
    assert len(offers) == 1

    # Take the driver off availability before declining. Otherwise the
    # decline's own auto-triggered re-optimization cycle (job_offer_lapsed -
    # see app/api/driver_routes.py) races the rest of this test and, since
    # this driver is still the only one available, immediately re-offers
    # them the same order - which is correct system behavior, just not what
    # this test is isolating ("declining requeues it" vs. "...and then it
    # may get reassigned instantly if someone's available").
    fleet_state = FleetStateManager()
    state = await fleet_state.get_driver_state(str(hub_id), str(driver_id))
    state.status = "on_break"
    await fleet_state.upsert_driver_state(state)

    await decline_offer(offers[0].offer_id, driver=authed, session=db_session)
    await dispatch_event_bus.wait_idle()  # let the job_offer_lapsed-triggered cycle finish

    # The order goes back to the hold queue instead of being stuck showing
    # "assigned" with no driver actually working it.
    hold_queue = HoldQueueStore()
    held = await hold_queue.get_all(str(hub_id))
    assert [o.order_id for o in held] == [str(order.id)]

    await db_session.refresh(order)
    assert order.status == OrderStatus.queued

    remaining_offers = await list_my_offers(driver=authed, session=db_session)
    assert remaining_offers == []
