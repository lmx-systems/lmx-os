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
from fastapi import HTTPException
from sqlalchemy import select

from app.api.driver_routes import (
    accept_offer,
    arrive_at_stop,
    complete_stop,
    decline_offer,
    flag_stop_issue,
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
from app.models.message import Message
from app.models.order import Order, OrderStatus
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop
from app.optimizer.event_trigger import dispatch_event_bus
from app.optimizer.service import DispatchOptimizerService
from app.schemas.driver_app import (
    CompleteStopBody,
    DriverAvailabilityUpdate,
    DriverProfileUpdate,
    FlagStopBody,
    ScanParcelsBody,
    StopFailureReason,
)
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
                lat=34.051, lng=-118.251, external_ref="SHOP-DRIVER-APP", phone="+15555550120",
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

    token = await verify_otp(
        VerifyOtpBody(phone="+15555550199", code=otp_result.debug_code, device_id="test-device"),
        session=db_session,
    )
    decoded_driver_id, decoded_hub_id, decoded_device_id = decode_token(token.access_token)
    assert decoded_driver_id == str(driver_id)
    assert decoded_device_id == "test-device"
    authed = AuthedDriver(driver_id=decoded_driver_id, hub_id=decoded_hub_id, device_id=decoded_device_id)

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
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

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
    # "held", not "queued" - it's back in the same Redis hold queue
    # app/ingestion/service.py uses "held" for (see
    # _requeue_orders_from_offer's docstring).
    assert order.status == OrderStatus.held

    remaining_offers = await list_my_offers(driver=authed, session=db_session)
    assert remaining_offers == []


async def _accept_one_offer(db_session, hub_id, driver_id):
    """Shared setup for the stop-state-machine tests below: go straight
    from a fresh seed to an accepted route with one pickup + one dropoff."""
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    await DispatchOptimizerService().run_cycle(str(hub_id))
    offers = await list_my_offers(driver=authed, session=db_session)
    route = await accept_offer(offers[0].offer_id, driver=authed, session=db_session)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    dropoff = next(s for s in route.stops if s.stop_type == "dropoff")
    return authed, pickup, dropoff


async def test_complete_stop_rejects_a_stop_that_never_arrived(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    with pytest.raises(HTTPException) as exc_info:
        await complete_stop(pickup.stop_id, CompleteStopBody(method="photo"), driver=authed, session=db_session)
    assert exc_info.value.status_code == 409


async def test_scan_parcels_rejects_before_arrival(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    with pytest.raises(HTTPException) as exc_info:
        await scan_parcels(pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    assert exc_info.value.status_code == 409


async def test_complete_stop_rejects_pickup_not_fully_scanned(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    with pytest.raises(HTTPException) as exc_info:
        await complete_stop(pickup.stop_id, CompleteStopBody(method="photo"), driver=authed, session=db_session)
    assert exc_info.value.status_code == 409


async def test_complete_stop_rejects_dropoff_before_its_pickup_is_completed(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, _pickup, dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(dropoff.stop_id, driver=authed, session=db_session)
    with pytest.raises(HTTPException) as exc_info:
        await complete_stop(dropoff.stop_id, CompleteStopBody(method="signature"), driver=authed, session=db_session)
    assert exc_info.value.status_code == 409


async def test_complete_stop_is_idempotent_on_retry(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    first = await complete_stop(pickup.stop_id, CompleteStopBody(method="photo"), driver=authed, session=db_session)

    # A retried/double-tapped complete call (e.g. an offline-queue replay
    # after a dropped response) must return the same success, not a 409 -
    # this is what makes it safe for a client to blindly retry.
    second = await complete_stop(pickup.stop_id, CompleteStopBody(method="photo"), driver=authed, session=db_session)
    assert second == first


async def test_complete_stop_replay_with_different_payload_keeps_original(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(pickup.stop_id, CompleteStopBody(method="photo"), driver=authed, session=db_session)

    # First write wins - a replay with a different payload must not silently
    # overwrite already-committed proof-of-delivery. StopView doesn't
    # surface pod_method, so check the row directly.
    await complete_stop(pickup.stop_id, CompleteStopBody(method="signature"), driver=authed, session=db_session)
    db_session.expire_all()
    pickup_row = await db_session.get(Stop, uuid.UUID(pickup.stop_id))
    assert pickup_row.pod_method == "photo"


async def test_complete_stop_still_rejects_a_failed_stop(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    pickup_row = await db_session.get(Stop, uuid.UUID(pickup.stop_id))
    pickup_row.status = "failed"
    await db_session.commit()

    # A genuine conflict (stop already terminal via a DIFFERENT terminal
    # status than "completed") must still 409, not be treated as a replay.
    with pytest.raises(HTTPException) as exc_info:
        await complete_stop(pickup.stop_id, CompleteStopBody(method="photo"), driver=authed, session=db_session)
    assert exc_info.value.status_code == 409


async def test_complete_stop_records_left_at(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(pickup.stop_id, CompleteStopBody(method="photo"), driver=authed, session=db_session)

    await arrive_at_stop(dropoff.stop_id, driver=authed, session=db_session)
    final_view = await complete_stop(
        dropoff.stop_id,
        CompleteStopBody(method="signature", signature_url="https://example.com/sig.png", left_at="front door"),
        driver=authed, session=db_session,
    )
    assert final_view.left_at == "front door"


async def test_accept_offer_never_commingles_a_hot_shot_pickup(db_session, real_redis_client):
    """
    Phase 8: HOT_SHOT is direct point-to-point and must never share a
    pickup stop with another order, even one from the same shop in the
    same offer - unlike T1/T2/T3, which do commingle (see the module
    docstring's Section 8 clustering reference). Also checks that the
    HOT_SHOT pickup and dropoff are sequenced ahead of the regular
    order's, and that "every pickup precedes every dropoff" still holds.
    """
    hub_id, client_id, shop_id, driver_id, regular_order = await _seed(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    now = datetime.now(timezone.utc)
    hot_order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref="ORD-DRIVER-APP-HOT-1", source_system="flat_file", raw_payload={},
        sla_tier="HOT_SHOT", hold_deadline=now + timedelta(minutes=2), weight_units=1,
        status=OrderStatus.assigned, requested_at=now,
        delivery_address="9 Speedway Ln", delivery_lat=34.0540, delivery_lng=-118.2540,
        delivery_contact_name="A. Cruz", delivery_contact_phone="+15555550177",
    )
    db_session.add(hot_order)
    await db_session.commit()

    offer = RouteOffer(
        hub_id=hub_id,
        driver_id=driver_id,
        status="offered",
        stop_payload=[
            {
                "order_id": str(regular_order.id), "lat": 34.051, "lng": -118.251,
                "sla_tier": "T2", "shop_name": "Midtown Auto Parts",
            },
            {
                "order_id": str(hot_order.id), "lat": 34.051, "lng": -118.251,
                "sla_tier": "HOT_SHOT", "shop_name": "Midtown Auto Parts",
            },
        ],
        offered_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    db_session.add(offer)
    await db_session.commit()

    route = await accept_offer(str(offer.id), driver=authed, session=db_session)

    pickups = [s for s in route.stops if s.stop_type == "pickup"]
    dropoffs = [s for s in route.stops if s.stop_type == "dropoff"]

    # Two separate pickup stops, not one commingled stop for the shop -
    # each carries exactly one order.
    assert len(pickups) == 2
    assert all(p.parcel_count == 1 for p in pickups)
    pickup_order_ids = {p.order_ids[0] for p in pickups}
    assert pickup_order_ids == {str(regular_order.id), str(hot_order.id)}

    # HOT_SHOT pickup and dropoff both come first within their block.
    hot_pickup = next(p for p in pickups if p.order_ids == [str(hot_order.id)])
    regular_pickup = next(p for p in pickups if p.order_ids == [str(regular_order.id)])
    assert hot_pickup.sequence < regular_pickup.sequence

    hot_dropoff = next(d for d in dropoffs if d.order_ids == [str(hot_order.id)])
    regular_dropoff = next(d for d in dropoffs if d.order_ids == [str(regular_order.id)])
    assert hot_dropoff.sequence < regular_dropoff.sequence

    # The "every pickup before every dropoff" invariant complete_stop's
    # unfinished_pickups check relies on must still hold.
    assert max(p.sequence for p in pickups) < min(d.sequence for d in dropoffs)


async def _shop_messages(db_session, stop_id):
    result = await db_session.execute(
        select(Message).where(Message.channel == "shop", Message.stop_id == uuid.UUID(stop_id))
        .order_by(Message.created_at)
    )
    return list(result.scalars().all())


async def test_accepting_an_offer_sends_an_en_route_shop_sms(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    messages = await _shop_messages(db_session, pickup.stop_id)
    assert len(messages) == 1
    assert messages[0].direction == "outbound"
    assert messages[0].counterparty_phone == "+15555550120"
    assert "Thanks for LMX'ing it!" in messages[0].body
    assert "Hot Shot" not in messages[0].body  # regular T2 order, not the premium tier


async def test_completing_a_pickup_stop_sends_a_picked_up_shop_sms(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(pickup.stop_id, CompleteStopBody(method="photo"), driver=authed, session=db_session)

    messages = await _shop_messages(db_session, pickup.stop_id)
    # The "en route" sent at accept, plus "picked up" sent at completion -
    # no second "en route" since this route only has the one pickup stop.
    assert len(messages) == 2
    assert "picked up" in messages[1].body.lower()


async def test_hot_shot_pickup_gets_the_premium_shop_sms_copy(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, regular_order = await _seed(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    now = datetime.now(timezone.utc)
    hot_order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref="ORD-DRIVER-APP-HOT-SMS", source_system="flat_file", raw_payload={},
        sla_tier="HOT_SHOT", hold_deadline=now + timedelta(minutes=2), weight_units=1,
        status=OrderStatus.assigned, requested_at=now,
        delivery_address="9 Speedway Ln", delivery_lat=34.0540, delivery_lng=-118.2540,
        delivery_contact_name="A. Cruz", delivery_contact_phone="+15555550177",
    )
    db_session.add(hot_order)
    await db_session.commit()

    offer = RouteOffer(
        hub_id=hub_id, driver_id=driver_id, status="offered",
        stop_payload=[
            {"order_id": str(hot_order.id), "lat": 34.051, "lng": -118.251, "sla_tier": "HOT_SHOT", "shop_name": "Midtown Auto Parts"},
        ],
        offered_at=now, expires_at=now + timedelta(minutes=5),
    )
    db_session.add(offer)
    await db_session.commit()

    route = await accept_offer(str(offer.id), driver=authed, session=db_session)
    pickup = next(s for s in route.stops if s.stop_type == "pickup")

    messages = await _shop_messages(db_session, pickup.stop_id)
    assert len(messages) == 1
    assert "Hot Shot" in messages[0].body
    assert "Thanks for LMX'ing it!" in messages[0].body


async def test_flag_stop_sets_failed_and_order_delivery_failed(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    flagged = await flag_stop_issue(
        pickup.stop_id,
        FlagStopBody(reason=StopFailureReason.SHOP_CLOSED, note="Gate padlocked, no answer"),
        driver=authed,
        session=db_session,
    )
    assert flagged.status == "failed"
    assert flagged.failure_reason == "SHOP_CLOSED"
    assert flagged.flag_note == "Gate padlocked, no answer"

    # Capture the plain UUID before expire_all() - accessing order.id on the
    # now-expired ORM instance would trigger a synchronous lazy-load outside
    # any async-aware call, raising MissingGreenlet.
    order_id = order.id
    db_session.expire_all()
    refreshed_order = await db_session.get(Order, order_id)
    assert refreshed_order.status == OrderStatus.delivery_failed


async def test_flag_stop_rejects_an_already_terminal_stop(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup.stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(pickup.stop_id, CompleteStopBody(method="photo"), driver=authed, session=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await flag_stop_issue(
            pickup.stop_id, FlagStopBody(reason=StopFailureReason.SHOP_CLOSED), driver=authed, session=db_session
        )
    assert exc_info.value.status_code == 409


async def test_dropoff_completes_after_its_pickup_was_flagged(db_session, real_redis_client):
    """Regression test for the route-finished bug this feature surfaced:
    a *failed* pickup must not count as "unfinished" forever, or a route
    with one flagged stop could never be completed."""
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await arrive_at_stop(pickup.stop_id, driver=authed, session=db_session)
    await flag_stop_issue(
        pickup.stop_id, FlagStopBody(reason=StopFailureReason.SHOP_CLOSED), driver=authed, session=db_session
    )

    await arrive_at_stop(dropoff.stop_id, driver=authed, session=db_session)
    completed_dropoff = await complete_stop(
        dropoff.stop_id, CompleteStopBody(method="signature"), driver=authed, session=db_session
    )
    assert completed_dropoff.status == "completed"

    db_session.expire_all()
    route = await get_my_route(driver=authed, session=db_session)
    # None of this route's stops are still non-terminal, so it's finished -
    # get_my_route only returns status="active" routes, so a None result
    # here confirms the route flipped to "completed".
    assert route is None
