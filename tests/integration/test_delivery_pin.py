"""
Real PIN issuance/verification for proof of delivery (docs/ROADMAP.md A4)
against real Postgres - accept_offer issues a real PIN (and texts it) to
every dropoff with a delivery contact phone on file, and complete_stop
checks the driver's submitted PIN against it for real.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.driver_routes import accept_offer, arrive_at_stop, complete_stop, scan_parcels
from app.driver_auth.dependencies import AuthedDriver
from app.messaging.delivery_pin import MAX_PIN_VERIFICATION_ATTEMPTS
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.message import Message
from app.models.order import Order, OrderStatus
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop
from app.schemas.driver_app import CompleteStopBody, ScanParcelsBody

pytestmark = pytest.mark.integration


async def _seed_offer(db_session, *, delivery_contact_phone: str | None = "+15555550188") -> tuple[uuid.UUID, str]:
    hub_id, client_id, shop_id, driver_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="PIN Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="PIN Test Client", pos_system="flat_file"))
    await db_session.commit()
    db_session.add_all([
        Shop(id=shop_id, client_id=client_id, name="PIN Test Shop", address="1 Main St", lat=34.06, lng=-118.24),
        Driver(id=driver_id, hub_id=hub_id, name="Sam D.", phone="+15555550304", vehicle_capacity_units=5),
    ])
    await db_session.commit()

    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref="ORD-PIN-1", source_system="flat_file", raw_payload={},
        sla_tier="T2", status=OrderStatus.held, requested_at=now,
        delivery_address="14 Oak Ave", delivery_lat=34.053, delivery_lng=-118.253,
        delivery_contact_name="J. Rivera", delivery_contact_phone=delivery_contact_phone,
    )
    db_session.add(order)
    await db_session.commit()

    offer = RouteOffer(
        hub_id=hub_id, driver_id=driver_id, status="offered",
        stop_payload=[{"order_id": str(order.id), "lat": 34.06, "lng": -118.24, "sla_tier": "T2", "shop_name": "PIN Test Shop"}],
        offered_at=now, expires_at=now + timedelta(minutes=2),
    )
    db_session.add(offer)
    await db_session.commit()

    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    route = await accept_offer(str(offer.id), driver=authed, session=db_session)
    pickup_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "pickup")
    dropoff_stop_id = next(s.stop_id for s in route.stops if s.stop_type == "dropoff")

    # The dropoff can't complete until its own route's pickup(s) do -
    # not what this test file is about, just a prerequisite to get there.
    await arrive_at_stop(pickup_stop_id, driver=authed, session=db_session)
    await scan_parcels(pickup_stop_id, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(
        pickup_stop_id, CompleteStopBody(method="photo", photo_url="https://example.com/pickup.jpg"),
        driver=authed, session=db_session,
    )

    return authed, dropoff_stop_id


async def test_accept_offer_issues_and_texts_a_real_pin(db_session, real_redis_client):
    _authed, dropoff_stop_id = await _seed_offer(db_session)

    result = await db_session.execute(select(Stop).where(Stop.id == uuid.UUID(dropoff_stop_id)))
    stop = result.scalar_one()
    assert stop.delivery_pin is not None
    assert len(stop.delivery_pin) == 4
    assert stop.pin_verification_attempts == 0

    messages_result = await db_session.execute(select(Message).where(Message.stop_id == stop.id))
    messages = messages_result.scalars().all()
    assert len(messages) == 1
    assert messages[0].channel == "delivery_pin"
    assert stop.delivery_pin in messages[0].body


async def test_no_pin_issued_without_a_delivery_contact_phone(db_session, real_redis_client):
    _authed, dropoff_stop_id = await _seed_offer(db_session, delivery_contact_phone=None)

    result = await db_session.execute(select(Stop).where(Stop.id == uuid.UUID(dropoff_stop_id)))
    stop = result.scalar_one()
    assert stop.delivery_pin is None


async def test_complete_stop_with_the_correct_pin_succeeds(db_session, real_redis_client):
    authed, dropoff_stop_id = await _seed_offer(db_session)
    result = await db_session.execute(select(Stop).where(Stop.id == uuid.UUID(dropoff_stop_id)))
    stop = result.scalar_one()
    real_pin = stop.delivery_pin

    await arrive_at_stop(dropoff_stop_id, driver=authed, session=db_session)
    completed = await complete_stop(
        dropoff_stop_id, CompleteStopBody(method="pin", pin=real_pin), driver=authed, session=db_session
    )
    assert completed.status == "completed"


async def test_complete_stop_rejects_an_incorrect_pin_without_completing(db_session, real_redis_client):
    authed, dropoff_stop_id = await _seed_offer(db_session)
    await arrive_at_stop(dropoff_stop_id, driver=authed, session=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await complete_stop(
            dropoff_stop_id, CompleteStopBody(method="pin", pin="0000"), driver=authed, session=db_session
        )
    assert exc_info.value.status_code == 400

    result = await db_session.execute(select(Stop).where(Stop.id == uuid.UUID(dropoff_stop_id)))
    stop = result.scalar_one()
    assert stop.status != "completed"
    assert stop.pin_verification_attempts == 1


async def test_complete_stop_rejects_pin_method_when_none_was_issued(db_session, real_redis_client):
    authed, dropoff_stop_id = await _seed_offer(db_session, delivery_contact_phone=None)
    await arrive_at_stop(dropoff_stop_id, driver=authed, session=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await complete_stop(
            dropoff_stop_id, CompleteStopBody(method="pin", pin="1234"), driver=authed, session=db_session
        )
    assert exc_info.value.status_code == 409


async def test_complete_stop_locks_out_after_too_many_incorrect_attempts(db_session, real_redis_client):
    authed, dropoff_stop_id = await _seed_offer(db_session)
    result = await db_session.execute(select(Stop).where(Stop.id == uuid.UUID(dropoff_stop_id)))
    real_pin = result.scalar_one().delivery_pin
    await arrive_at_stop(dropoff_stop_id, driver=authed, session=db_session)

    for _ in range(MAX_PIN_VERIFICATION_ATTEMPTS):
        with pytest.raises(HTTPException) as exc_info:
            await complete_stop(
                dropoff_stop_id, CompleteStopBody(method="pin", pin="0000"), driver=authed, session=db_session
            )
        assert exc_info.value.status_code == 400

    # Even the real PIN is refused once the attempt cap is hit.
    with pytest.raises(HTTPException) as exc_info:
        await complete_stop(
            dropoff_stop_id, CompleteStopBody(method="pin", pin=real_pin), driver=authed, session=db_session
        )
    assert exc_info.value.status_code == 409
