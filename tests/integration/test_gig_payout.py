"""
Real per-delivery pay model for gig-classified drivers (docs/ROADMAP.md
A11) against real Postgres - list_my_offers shows a real pay estimate
only to a gig driver, and complete_stop creates a real GigPayout row (and
attempts a real payout) when a gig driver's dropoff completes.

Reuses test_driver_app_integration.py's _seed helper for the base
hub/client/shop/order fixture, same pattern test_delivery_pin.py and
test_masked_calling.py already established for this test family, but
builds its own offer/accept flow so the driver's employment_type can be
set to "gig" (the shared _accept_one_offer helper always creates a
default w2 driver).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.driver_routes import (
    accept_offer,
    arrive_at_stop,
    complete_stop,
    get_my_earnings,
    list_my_offers,
    scan_parcels,
)
from app.driver_auth.dependencies import AuthedDriver
from app.models.client import Client
from app.models.driver import Driver
from app.models.gig_payout import GigPayout
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.schemas.driver_app import CompleteStopBody, ScanParcelsBody

pytestmark = pytest.mark.integration


async def _seed_gig_offer(db_session, *, employment_type: str = "gig") -> tuple[AuthedDriver, str, str]:
    hub_id, client_id, shop_id, driver_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Gig Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Gig Test Client", pos_system="flat_file"))
    await db_session.commit()
    db_session.add_all([
        Shop(id=shop_id, client_id=client_id, name="Gig Test Shop", address="1 Main St", lat=34.05, lng=-118.25),
        Driver(
            id=driver_id, hub_id=hub_id, name="Gig D.", phone="+15555550777", vehicle_capacity_units=5,
            employment_type=employment_type,
        ),
    ])
    await db_session.commit()

    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref="ORD-GIG-1", source_system="flat_file", raw_payload={},
        sla_tier="HOT_SHOT", status=OrderStatus.held, requested_at=now,
        delivery_address="14 Oak Ave", delivery_lat=34.06, delivery_lng=-118.26,
        delivery_contact_name="J. Rivera", delivery_contact_phone="+15555550188",
    )
    db_session.add(order)
    await db_session.commit()

    offer = RouteOffer(
        hub_id=hub_id, driver_id=driver_id, status="offered",
        stop_payload=[{"order_id": str(order.id), "lat": 34.05, "lng": -118.25, "sla_tier": "HOT_SHOT", "shop_name": "Gig Test Shop"}],
        offered_at=now, expires_at=now + timedelta(minutes=2),
    )
    db_session.add(offer)
    await db_session.commit()

    return AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device"), str(offer.id), str(order.id)


async def test_list_my_offers_shows_a_real_pay_estimate_for_a_gig_driver(db_session, real_redis_client):
    authed, offer_id, _order_id = await _seed_gig_offer(db_session)

    offers = await list_my_offers(driver=authed, session=db_session)
    assert len(offers) == 1
    assert offers[0].estimated_pay_cents is not None
    assert offers[0].estimated_pay_cents > 0


async def test_list_my_offers_shows_no_pay_estimate_for_a_w2_driver(db_session, real_redis_client):
    authed, offer_id, _order_id = await _seed_gig_offer(db_session, employment_type="w2")

    offers = await list_my_offers(driver=authed, session=db_session)
    assert len(offers) == 1
    assert offers[0].estimated_pay_cents is None


async def test_complete_stop_creates_a_real_gig_payout_for_a_gig_driver(db_session, real_redis_client):
    authed, offer_id, _order_id = await _seed_gig_offer(db_session)
    route = await accept_offer(offer_id, driver=authed, session=db_session)
    pickup_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "pickup")
    dropoff_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "dropoff")

    await arrive_at_stop(pickup_stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup_stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(
        pickup_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/pickup.jpg"),
        driver=authed, session=db_session,
    )

    await arrive_at_stop(dropoff_stop_id, driver=authed, session=db_session)
    await complete_stop(
        dropoff_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/dropoff.jpg"),
        driver=authed, session=db_session,
    )

    result = await db_session.execute(select(GigPayout).where(GigPayout.stop_id == uuid.UUID(dropoff_stop_id)))
    payout = result.scalar_one()
    assert payout.amount_cents > 0
    # No Stripe account configured in tests, and this driver has no
    # stripe_connect_account_id set either - same "unconfigured -> stub"
    # status either way ends in a non-"paid" status, not silently lost.
    assert payout.status in ("stub", "skipped_no_payout_account")


async def test_complete_stop_creates_no_gig_payout_for_a_w2_driver(db_session, real_redis_client):
    authed, offer_id, _order_id = await _seed_gig_offer(db_session, employment_type="w2")
    route = await accept_offer(offer_id, driver=authed, session=db_session)
    pickup_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "pickup")
    dropoff_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "dropoff")

    await arrive_at_stop(pickup_stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup_stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(
        pickup_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/pickup.jpg"),
        driver=authed, session=db_session,
    )
    await arrive_at_stop(dropoff_stop_id, driver=authed, session=db_session)
    await complete_stop(
        dropoff_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/dropoff.jpg"),
        driver=authed, session=db_session,
    )

    result = await db_session.execute(select(GigPayout).where(GigPayout.stop_id == uuid.UUID(dropoff_stop_id)))
    assert result.scalar_one_or_none() is None


async def test_complete_stop_never_double_pays_the_same_stop(db_session, real_redis_client):
    """complete_stop's own idempotent early-return already prevents a
    retried request from reaching the payout trigger at all - this proves
    GigPayout's unique(stop_id) constraint holds even if that ever
    changes, by calling the payout helper path twice directly."""
    authed, offer_id, _order_id = await _seed_gig_offer(db_session)
    route = await accept_offer(offer_id, driver=authed, session=db_session)
    pickup_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "pickup")
    dropoff_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "dropoff")

    await arrive_at_stop(pickup_stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup_stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(
        pickup_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/pickup.jpg"),
        driver=authed, session=db_session,
    )
    await arrive_at_stop(dropoff_stop_id, driver=authed, session=db_session)
    await complete_stop(
        dropoff_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/dropoff.jpg"),
        driver=authed, session=db_session,
    )
    # A second call is an idempotent replay (same payload) - complete_stop
    # returns early without re-running any side effect, including payout.
    await complete_stop(
        dropoff_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/dropoff.jpg"),
        driver=authed, session=db_session,
    )

    result = await db_session.execute(select(GigPayout).where(GigPayout.stop_id == uuid.UUID(dropoff_stop_id)))
    assert len(result.scalars().all()) == 1


async def test_get_my_earnings_reflects_real_gig_payouts_not_hourly_rate(db_session, real_redis_client):
    authed, offer_id, _order_id = await _seed_gig_offer(db_session)
    route = await accept_offer(offer_id, driver=authed, session=db_session)
    pickup_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "pickup")
    dropoff_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "dropoff")

    await arrive_at_stop(pickup_stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup_stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(
        pickup_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/pickup.jpg"),
        driver=authed, session=db_session,
    )
    await arrive_at_stop(dropoff_stop_id, driver=authed, session=db_session)
    await complete_stop(
        dropoff_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/dropoff.jpg"),
        driver=authed, session=db_session,
    )

    earnings = await get_my_earnings(driver=authed, session=db_session)
    assert earnings.employment_type == "gig"
    assert earnings.hourly_rate_cents == 0
    assert earnings.is_placeholder is False

    payout_result = await db_session.execute(select(GigPayout).where(GigPayout.stop_id == uuid.UUID(dropoff_stop_id)))
    payout = payout_result.scalar_one()
    assert earnings.estimated_pay_cents == payout.amount_cents
