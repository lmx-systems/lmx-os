"""
Driver-facing API - screens 1a-1m of LMX Driver App Wireframes.dc.html
(onboarding, availability/jobs, active job). See docs/NEXT_STEPS.md item 12
for the gap analysis this closes: real per-driver auth (not the shared
X-API-Key), a job-offer/accept model, and the first Route/Stop endpoints
this codebase has ever had.

Every route below (other than the two auth endpoints) requires a driver
Bearer token - see app/driver_auth/dependencies.py.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.db import get_db
from app.driver_auth.dependencies import AuthedDriver, get_current_driver, revoked_devices_key
from app.driver_auth.otp_store import OtpRateLimitExceeded, OtpStore
from app.driver_auth.tokens import issue_token
from app.fleet_state.manager import FleetStateManager
from app.redis_client import get_client
from app.messaging.shop_notifications import notify_shop_en_route, notify_shop_picked_up
from app.messaging.sms_client import get_sms_client
from app.models.driver import Driver
from app.models.driver_device import DriverDevice
from app.models.driver_document import DriverDocument
from app.models.message import Message
from app.models.order import Order, OrderStatus, SLATier
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.optimizer.event_trigger import dispatch_event_bus
from app.schemas.driver_app import (
    CompleteStopBody,
    DriverAvailabilityUpdate,
    DriverDocumentUpdate,
    DriverDocumentView,
    DriverProfileUpdate,
    DriverProfileView,
    EarningsView,
    FlagStopBody,
    JobOfferView,
    MessageView,
    OfferStopSummary,
    PaymentMethodUpdate,
    RouteView,
    ScanParcelsBody,
    SendMessageBody,
    StopView,
    TripSummaryView,
)
from app.schemas.driver_auth import (
    AuthToken,
    DriverDeviceView,
    RequestOtpBody,
    RequestOtpResult,
    VerifyOtpBody,
)
from app.schemas.fleet import DriverState

router = APIRouter(prefix="/driver", tags=["driver"])
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Auth (screens 1a/1b) - the only two endpoints in this router that don't
# require get_current_driver, since their whole point is to produce a token.
# ---------------------------------------------------------------------------


@router.post("/auth/request-otp", response_model=RequestOtpResult)
async def request_otp(body: RequestOtpBody, session: AsyncSession = Depends(get_db)) -> RequestOtpResult:
    result = await session.execute(select(Driver.id).where(Driver.phone == body.phone))
    if result.scalar_one_or_none() is None:
        # Drivers are provisioned by ops, not self-registered - see 1a's
        # "Apply to drive" annotation (out of app scope).
        raise HTTPException(status_code=404, detail="No driver registered with this phone number")

    try:
        issued = await OtpStore().issue(body.phone)
    except OtpRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return RequestOtpResult(ok=True, debug_code=None if issued.sent_via_sms else issued.code)


@router.post("/auth/verify-otp", response_model=AuthToken)
async def verify_otp(body: VerifyOtpBody, session: AsyncSession = Depends(get_db)) -> AuthToken:
    if not await OtpStore().verify(body.phone, body.code):
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    result = await session.execute(select(Driver).where(Driver.phone == body.phone))
    driver = result.scalar_one_or_none()
    if driver is None:
        raise HTTPException(status_code=404, detail="No driver registered with this phone number")

    now = datetime.now(timezone.utc)
    device_result = await session.execute(
        select(DriverDevice).where(
            DriverDevice.driver_id == driver.id, DriverDevice.device_id == body.device_id
        )
    )
    device = device_result.scalar_one_or_none()
    if device is None:
        device = DriverDevice(
            driver_id=driver.id, device_id=body.device_id, device_name=body.device_name, last_seen_at=now
        )
        session.add(device)
    else:
        device.last_seen_at = now
        device.device_name = body.device_name or device.device_name
        # Re-verifying OTP is itself re-proof of identity - if this device
        # was previously revoked (e.g. "not my phone anymore" turned out to
        # be wrong, or a driver got their phone back), a fresh OTP clears it.
        device.revoked_at = None
    await session.commit()

    await get_client().srem(revoked_devices_key(str(driver.id)), body.device_id)

    return AuthToken(access_token=issue_token(str(driver.id), str(driver.hub_id), body.device_id))


@router.post("/auth/refresh", response_model=AuthToken)
async def refresh_token(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> AuthToken:
    """
    Lets a driver's session slide forward indefinitely on each app open
    without redoing OTP, as long as their device isn't revoked - the
    existing ~month-long token expiry already outlives any single shift,
    so this isn't fixing a TTL problem, it's what the client calls after a
    successful biometric unlock to keep a long-lived device-bound session
    alive without a second refresh-token artifact type.
    """
    device_result = await session.execute(
        select(DriverDevice).where(
            DriverDevice.driver_id == uuid.UUID(driver.driver_id), DriverDevice.device_id == driver.device_id
        )
    )
    device = device_result.scalar_one_or_none()
    if device is not None:
        device.last_seen_at = datetime.now(timezone.utc)
        await session.commit()

    return AuthToken(access_token=issue_token(driver.driver_id, driver.hub_id, driver.device_id))


@router.get("/me/devices", response_model=list[DriverDeviceView])
async def list_my_devices(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> list[DriverDeviceView]:
    result = await session.execute(
        select(DriverDevice)
        .where(DriverDevice.driver_id == uuid.UUID(driver.driver_id), DriverDevice.revoked_at.is_(None))
        .order_by(DriverDevice.last_seen_at.desc())
    )
    return [
        DriverDeviceView(
            device_id=d.device_id,
            device_name=d.device_name,
            last_seen_at=d.last_seen_at.isoformat(),
            is_current=d.device_id == driver.device_id,
        )
        for d in result.scalars().all()
    ]


@router.delete("/me/devices/{device_id}", status_code=204)
async def revoke_my_device(
    device_id: str, driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> None:
    """Self-service "this isn't my phone anymore" - takes effect on that
    device's very next request (checked in get_current_driver), not just
    the next time it tries to refresh."""
    result = await session.execute(
        select(DriverDevice).where(
            DriverDevice.driver_id == uuid.UUID(driver.driver_id), DriverDevice.device_id == device_id
        )
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    device.revoked_at = datetime.now(timezone.utc)
    await session.commit()
    await get_client().sadd(revoked_devices_key(driver.driver_id), device_id)


# ---------------------------------------------------------------------------
# Profile + availability (screens 1c, 1d/1e)
# ---------------------------------------------------------------------------


async def _count_completed_trips(session: AsyncSession, driver_id: str) -> int:
    """Real trip count for the profile screen (1r) - a completed Route, not
    a stand-in figure. There's no rating-submission system anywhere in this
    app, so unlike trip count, a star rating has nothing real to compute
    from and is deliberately not shown."""
    result = await session.execute(
        select(func.count())
        .select_from(Route)
        .where(Route.driver_id == uuid.UUID(driver_id), Route.status == "completed")
    )
    return result.scalar_one()


async def _profile_view(session: AsyncSession, row: Driver) -> DriverProfileView:
    return DriverProfileView(
        driver_id=str(row.id),
        hub_id=str(row.hub_id),
        name=row.name,
        phone=row.phone,
        status=row.status,
        vehicle_type=row.vehicle_type,
        plate_number=row.plate_number,
        delivery_zone=row.delivery_zone,
        payment_bank_last4=row.payment_bank_last4,
        trip_count=await _count_completed_trips(session, str(row.id)),
    )


async def _get_driver_row(session: AsyncSession, driver: AuthedDriver) -> Driver:
    row = await session.get(Driver, uuid.UUID(driver.driver_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    return row


@router.get("/me", response_model=DriverProfileView)
async def get_my_profile(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> DriverProfileView:
    return await _profile_view(session, await _get_driver_row(session, driver))


@router.put("/me", response_model=DriverProfileView)
async def update_my_profile(
    body: DriverProfileUpdate,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> DriverProfileView:
    row = await _get_driver_row(session, driver)
    row.vehicle_type = body.vehicle_type
    row.plate_number = body.plate_number
    row.delivery_zone = body.delivery_zone
    await session.commit()
    return await _profile_view(session, row)


@router.put("/me/payment-method", response_model=DriverProfileView)
async def update_my_payment_method(
    body: PaymentMethodUpdate,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> DriverProfileView:
    row = await _get_driver_row(session, driver)
    row.payment_bank_last4 = body.bank_last4
    await session.commit()
    return await _profile_view(session, row)


# ---------------------------------------------------------------------------
# Documents (screen 1r) - see app/models/driver_document.py.
# ---------------------------------------------------------------------------


async def _get_expired_documents(session: AsyncSession, driver_id: str) -> list[DriverDocument]:
    result = await session.execute(
        select(DriverDocument).where(
            DriverDocument.driver_id == uuid.UUID(driver_id), DriverDocument.expires_at < date.today()
        )
    )
    return list(result.scalars().all())


@router.get("/me/documents", response_model=list[DriverDocumentView])
async def list_my_documents(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> list[DriverDocumentView]:
    result = await session.execute(
        select(DriverDocument).where(DriverDocument.driver_id == uuid.UUID(driver.driver_id))
    )
    return [
        DriverDocumentView(doc_type=doc.doc_type, expires_at=doc.expires_at, file_url=doc.file_url)
        for doc in result.scalars().all()
    ]


@router.put("/me/documents/{doc_type}", response_model=DriverDocumentView)
async def update_my_document(
    doc_type: str,
    body: DriverDocumentUpdate,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> DriverDocumentView:
    result = await session.execute(
        select(DriverDocument).where(
            DriverDocument.driver_id == uuid.UUID(driver.driver_id), DriverDocument.doc_type == doc_type
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        doc = DriverDocument(driver_id=uuid.UUID(driver.driver_id), doc_type=doc_type, expires_at=body.expires_at)
        session.add(doc)
    doc.expires_at = body.expires_at
    doc.file_url = body.file_url
    await session.commit()
    return DriverDocumentView(doc_type=doc.doc_type, expires_at=doc.expires_at, file_url=doc.file_url)


@router.post("/me/state")
async def update_my_availability(
    body: DriverAvailabilityUpdate,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> dict:
    row = await _get_driver_row(session, driver)

    if body.status == "available":
        expired = await _get_expired_documents(session, driver.driver_id)
        if expired:
            expired_types = ", ".join(doc.doc_type for doc in expired)
            raise HTTPException(
                status_code=409,
                detail=f"Renew your {expired_types} before going online - see wireframe screen 1r's "
                "document-expiry annotation.",
            )

    manager = FleetStateManager()
    existing = await manager.get_driver_state(driver.hub_id, driver.driver_id)
    await manager.upsert_driver_state(
        DriverState(
            driver_id=driver.driver_id,
            hub_id=driver.hub_id,
            status=body.status,
            capacity_units=row.vehicle_capacity_units,
            load_units=existing.load_units if existing else 0,
            current_route_id=existing.current_route_id if existing else None,
        )
    )
    await dispatch_event_bus.publish(driver.hub_id, "driver_status_changed")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Job offers (screens 1f/1g) - see app/models/route_offer.py and
# app/optimizer/service.py, which is what actually creates these rows.
# ---------------------------------------------------------------------------


async def _requeue_orders_from_offer(
    session: AsyncSession, hub_id: str, driver_id: str, stop_payload: list[dict]
) -> None:
    """
    A declined/expired offer never touches Route/Stop - the orders just go
    back to the hold queue (Redis) with their original geography/SLA tier
    so the next Dispatch Optimizer cycle tries again, and Order.status
    reverts from "assigned" (set optimistically the moment the optimizer
    proposed the offer - see app/optimizer/service.py) back to "held" -
    not "queued", which per this enum's own definition means "released
    from hold, waiting for a route assignment." The order is neither of
    those right now; it's back in the same Redis hold queue app/ingestion/
    service.py uses "held" for, so the Postgres status should say so too.

    Also puts the driver back in the optimizer's assignable pool - the
    optimizer took them out of it the moment it made the offer (see
    app/optimizer/service.py) precisely so they can't be offered a second,
    overlapping job while this one is still pending.
    """
    manager = FleetStateManager()
    existing_state = await manager.get_driver_state(hub_id, driver_id)
    if existing_state is not None and existing_state.status == "offered":
        await manager.upsert_driver_state(
            DriverState(
                driver_id=driver_id,
                hub_id=hub_id,
                status="available",
                capacity_units=existing_state.capacity_units,
                load_units=existing_state.load_units,
                current_route_id=existing_state.current_route_id,
            )
        )

    hold_queue = HoldQueueStore()
    now = datetime.now(timezone.utc)
    order_ids = [uuid.UUID(s["order_id"]) for s in stop_payload]
    if order_ids:
        await session.execute(
            update(Order).where(Order.id.in_(order_ids)).values(status=OrderStatus.held)
        )
    orders_result = await session.execute(select(Order).where(Order.id.in_(order_ids))) if order_ids else None
    orders_by_id = {o.id: o for o in (orders_result.scalars().all() if orders_result else [])}

    for stop in stop_payload:
        order = orders_by_id.get(uuid.UUID(stop["order_id"]))
        await hold_queue.add(
            hub_id,
            HeldOrder(
                order_id=stop["order_id"],
                shop_lat=stop["lat"],
                shop_lng=stop["lng"],
                sla_tier=stop["sla_tier"],
                # Deliberately reuses the order's original hold_deadline
                # (very likely already in the past by now) rather than
                # inventing a fresh one - that makes the next hold cycle's
                # "past SLA deadline" rule force-release it immediately
                # instead of holding it all over again behind the driver
                # who just declined.
                hold_deadline=(order.hold_deadline if order else None) or (now + timedelta(minutes=5)),
                held_since=now,
                shop_name=stop.get("shop_name", ""),
            ),
        )
    await dispatch_event_bus.publish(hub_id, "job_offer_lapsed")


async def _expire_if_lapsed(session: AsyncSession, offer: RouteOffer) -> bool:
    """
    Lazily expires an offer past its TTL, returning True if it was (just)
    expired. Factored out once - previously duplicated inline in two of
    three offer-reading endpoints, which is exactly how decline_offer ended
    up as the one that forgot this check.
    """
    if offer.expires_at > datetime.now(timezone.utc):
        return False
    offer.status = "expired"
    offer.responded_at = datetime.now(timezone.utc)
    await _requeue_orders_from_offer(session, str(offer.hub_id), str(offer.driver_id), offer.stop_payload)
    return True


@router.get("/me/offers", response_model=list[JobOfferView])
async def list_my_offers(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> list[JobOfferView]:
    result = await session.execute(
        select(RouteOffer).where(
            RouteOffer.driver_id == uuid.UUID(driver.driver_id), RouteOffer.status == "offered"
        )
    )
    offers = result.scalars().all()

    live: list[JobOfferView] = []
    for offer in offers:
        if await _expire_if_lapsed(session, offer):
            continue
        live.append(
            JobOfferView(
                offer_id=str(offer.id),
                hub_id=str(offer.hub_id),
                expires_at=offer.expires_at,
                stops=[OfferStopSummary(**s) for s in offer.stop_payload],
            )
        )
    await session.commit()
    return live


@router.post("/offers/{offer_id}/decline")
async def decline_offer(
    offer_id: str,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> dict:
    # for_update: locks the row so a concurrent accept/decline on the same
    # offer can't both read "offered" before either commits (see accept_offer).
    offer = await _get_owned_offer(session, offer_id, driver, for_update=True)
    if offer.status != "offered":
        raise HTTPException(status_code=409, detail=f"Offer is {offer.status}, not open for a response")

    if await _expire_if_lapsed(session, offer):
        await session.commit()
        raise HTTPException(status_code=409, detail="Offer expired")

    offer.status = "declined"
    offer.responded_at = datetime.now(timezone.utc)
    await _requeue_orders_from_offer(session, str(offer.hub_id), str(offer.driver_id), offer.stop_payload)
    await session.commit()
    return {"ok": True}


@router.post("/offers/{offer_id}/accept", response_model=RouteView)
async def accept_offer(
    offer_id: str,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> RouteView:
    # for_update: without this, two concurrent accept calls for the same
    # offer (a double-tap, or a client retry after a request that actually
    # succeeded) can both read status="offered" before either commits,
    # and both go on to build a Route - two Routes for one offer. Locking
    # the row here means the second request blocks until the first commits,
    # then re-reads the now-"accepted" status and 409s below instead.
    offer = await _get_owned_offer(session, offer_id, driver, for_update=True)
    if offer.status != "offered":
        raise HTTPException(status_code=409, detail=f"Offer is {offer.status}, not open for a response")

    now = datetime.now(timezone.utc)
    if await _expire_if_lapsed(session, offer):
        await session.commit()
        raise HTTPException(status_code=409, detail="Offer expired")

    route = Route(hub_id=offer.hub_id, driver_id=offer.driver_id, status="active", plan_version=1)
    session.add(route)
    await session.flush()  # need route.id to attach stops

    order_ids = [uuid.UUID(s["order_id"]) for s in offer.stop_payload]
    orders_result = await session.execute(select(Order).where(Order.id.in_(order_ids)))
    orders_by_id = {o.id: o for o in orders_result.scalars().all()}

    sequence = 0

    # One pickup stop per unique shop, aggregating any commingled orders
    # from that shop (Section 8 clustering) into a single parcel count -
    # except HOT_SHOT orders (Phase 8), which never share a stop with any
    # other order, even another HOT_SHOT order from the same shop, per
    # Sourabh's "direct point-to-point, never commingled" definition. Each
    # HOT_SHOT order gets its own dedicated pickup Stop with parcel_count=1.
    orders_by_shop: dict[uuid.UUID, list[uuid.UUID]] = {}
    hot_shot_order_ids: list[uuid.UUID] = []
    for order in orders_by_id.values():
        if order.sla_tier == SLATier.HOT_SHOT:
            hot_shot_order_ids.append(order.id)
        else:
            orders_by_shop.setdefault(order.shop_id, []).append(order.id)

    # Tracks whichever pickup stop lands at sequence 0 - that's the driver's
    # first stop the moment this offer is accepted, so it gets an
    # immediate "en route" shop SMS below (Phase 8 shop notifications).
    first_pickup_stop: Stop | None = None
    first_pickup_is_hot_shot = False

    # HOT_SHOT pickups go first - the premium tier a client is paying extra
    # for shouldn't sit behind a driver's other pickups on the same route.
    for oid in hot_shot_order_ids:
        order = orders_by_id[oid]
        pickup = Stop(
            route_id=route.id,
            shop_id=order.shop_id,
            sequence=sequence,
            stop_type="pickup",
            parcel_count=1,
        )
        session.add(pickup)
        await session.flush()
        session.add(StopOrder(stop_id=pickup.id, order_id=oid))
        if first_pickup_stop is None:
            first_pickup_stop, first_pickup_is_hot_shot = pickup, True
        sequence += 1

    for shop_id, shop_order_ids in orders_by_shop.items():
        pickup = Stop(
            route_id=route.id,
            shop_id=shop_id,
            sequence=sequence,
            stop_type="pickup",
            parcel_count=len(shop_order_ids),
        )
        session.add(pickup)
        await session.flush()
        for oid in shop_order_ids:
            session.add(StopOrder(stop_id=pickup.id, order_id=oid))
        if first_pickup_stop is None:
            first_pickup_stop, first_pickup_is_hot_shot = pickup, False
        sequence += 1

    # One dropoff stop per order, in the sequence the optimizer assigned
    # them - see app/models/order.py's delivery_* fields and the module
    # docstring on drop-sequencing being unoptimized in v1. HOT_SHOT
    # dropoffs are sorted first, same reasoning as their pickups above -
    # this still preserves "every pickup stop is sequenced before every
    # dropoff stop" (see complete_stop's unfinished_pickups check below),
    # it just prioritizes HOT_SHOT within each of those two blocks.
    hot_shot_id_set = set(hot_shot_order_ids)
    sorted_stop_payload = sorted(
        offer.stop_payload,
        key=lambda s: 0 if uuid.UUID(s["order_id"]) in hot_shot_id_set else 1,
    )
    for stop_summary in sorted_stop_payload:
        order = orders_by_id.get(uuid.UUID(stop_summary["order_id"]))
        if order is None:
            continue
        dropoff = Stop(route_id=route.id, shop_id=None, sequence=sequence, stop_type="dropoff", parcel_count=1)
        session.add(dropoff)
        await session.flush()
        session.add(StopOrder(stop_id=dropoff.id, order_id=order.id))
        sequence += 1

    offer.status = "accepted"
    offer.responded_at = now
    offer.route_id = route.id

    manager = FleetStateManager()
    existing_state = await manager.get_driver_state(str(offer.hub_id), driver.driver_id)
    await manager.upsert_driver_state(
        DriverState(
            driver_id=driver.driver_id,
            hub_id=str(offer.hub_id),
            status="en_route",
            capacity_units=existing_state.capacity_units if existing_state else 1,
            load_units=existing_state.load_units if existing_state else 0,
            current_route_id=str(route.id),
        )
    )
    await dispatch_event_bus.publish(str(offer.hub_id), "driver_status_changed")

    await session.commit()

    # Phase 8 shop SMS: the driver is headed to their first pickup the
    # moment this offer is accepted - notify that shop now. Best-effort:
    # a shop with no phone on file (or a send failure) shouldn't block the
    # accept flow, which has already committed above.
    if first_pickup_stop is not None and first_pickup_stop.shop_id is not None:
        shop = await session.get(Shop, first_pickup_stop.shop_id)
        if shop is not None:
            await notify_shop_en_route(
                session,
                hub_id=offer.hub_id,
                driver_id=offer.driver_id,
                stop_id=first_pickup_stop.id,
                shop=shop,
                is_hot_shot=first_pickup_is_hot_shot,
            )
            await session.commit()

    return await _load_route_view(session, route.id)


async def _get_owned_offer(
    session: AsyncSession, offer_id: str, driver: AuthedDriver, *, for_update: bool = False
) -> RouteOffer:
    offer = await session.get(RouteOffer, uuid.UUID(offer_id), with_for_update=for_update)
    if offer is None or str(offer.driver_id) != driver.driver_id:
        raise HTTPException(status_code=404, detail="Offer not found")
    return offer


# ---------------------------------------------------------------------------
# Active job: route + stops (screens 1h-1m)
# ---------------------------------------------------------------------------


async def _load_route_view(session: AsyncSession, route_id: uuid.UUID) -> RouteView:
    route = await session.get(Route, route_id)

    stops_result = await session.execute(select(Stop).where(Stop.route_id == route_id).order_by(Stop.sequence))
    stop_rows = list(stops_result.scalars().all())
    stop_ids = [s.id for s in stop_rows]

    order_ids_by_stop: dict[uuid.UUID, list[uuid.UUID]] = {}
    if stop_ids:
        so_result = await session.execute(select(StopOrder).where(StopOrder.stop_id.in_(stop_ids)))
        for so in so_result.scalars().all():
            order_ids_by_stop.setdefault(so.stop_id, []).append(so.order_id)

    all_order_ids = [oid for ids in order_ids_by_stop.values() for oid in ids]
    orders_by_id: dict[uuid.UUID, Order] = {}
    if all_order_ids:
        orders_result = await session.execute(select(Order).where(Order.id.in_(all_order_ids)))
        orders_by_id = {o.id: o for o in orders_result.scalars().all()}

    shop_ids = [s.shop_id for s in stop_rows if s.shop_id is not None]
    shops_by_id: dict[uuid.UUID, Shop] = {}
    if shop_ids:
        shops_result = await session.execute(select(Shop).where(Shop.id.in_(shop_ids)))
        shops_by_id = {sh.id: sh for sh in shops_result.scalars().all()}

    stop_views: list[StopView] = []
    for stop in stop_rows:
        order_ids = order_ids_by_stop.get(stop.id, [])
        if stop.stop_type == "pickup":
            shop = shops_by_id.get(stop.shop_id) if stop.shop_id else None
            stop_views.append(
                StopView(
                    stop_id=str(stop.id),
                    sequence=stop.sequence,
                    stop_type=stop.stop_type,
                    status=stop.status,
                    lat=shop.lat if shop else 0.0,
                    lng=shop.lng if shop else 0.0,
                    shop_name=shop.name if shop else None,
                    address=shop.address if shop else None,
                    parcel_count=stop.parcel_count,
                    scanned_count=stop.scanned_count,
                    order_ids=[str(o) for o in order_ids],
                    eta=stop.eta,
                    completed_at=stop.completed_at,
                    failure_reason=stop.failure_reason,
                    flag_note=stop.flag_note,
                )
            )
        else:
            order = orders_by_id.get(order_ids[0]) if order_ids else None
            stop_views.append(
                StopView(
                    stop_id=str(stop.id),
                    sequence=stop.sequence,
                    stop_type=stop.stop_type,
                    status=stop.status,
                    lat=float(order.delivery_lat) if order and order.delivery_lat is not None else 0.0,
                    lng=float(order.delivery_lng) if order and order.delivery_lng is not None else 0.0,
                    address=order.delivery_address if order else None,
                    contact_name=order.delivery_contact_name if order else None,
                    contact_phone=order.delivery_contact_phone if order else None,
                    notes=order.delivery_notes if order else None,
                    parcel_count=stop.parcel_count,
                    scanned_count=stop.scanned_count,
                    order_ids=[str(o) for o in order_ids],
                    eta=stop.eta,
                    completed_at=stop.completed_at,
                    left_at=stop.pod_left_at,
                    failure_reason=stop.failure_reason,
                    flag_note=stop.flag_note,
                )
            )

    return RouteView(route_id=str(route.id), status=route.status, plan_version=route.plan_version, stops=stop_views)


@router.get("/me/route", response_model=RouteView | None)
async def get_my_route(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> RouteView | None:
    result = await session.execute(
        select(Route)
        .where(Route.driver_id == uuid.UUID(driver.driver_id), Route.status == "active")
        .order_by(Route.created_at.desc())
    )
    route = result.scalars().first()
    if route is None:
        return None
    return await _load_route_view(session, route.id)


@router.get("/me/route-events")
async def stream_my_route_events(driver: AuthedDriver = Depends(get_current_driver)) -> EventSourceResponse:
    """
    Live route-change push (the wireframe's "New stop added ahead" banner).
    Redis pub/sub, not the in-process HubEventBus (app/events/bus.py) -
    that bus always reruns the dispatch optimizer regardless of which
    handler you'd want, and pub/sub broadcasts to every subscriber
    regardless of which backend replica holds the connection, which
    matters once this runs behind more than one instance (S3/E8 in
    docs/ROADMAP.md). One channel per driver, not per hub - a driver only
    ever cares about their own route, so there's nothing to filter out.

    Client is expected to treat this as "go refetch GET /driver/me/route,"
    not as the source of truth for what changed - the event payload is
    enough to render a banner, but the authoritative stop list always
    comes from a real fetch. Route.plan_version (returned by that same
    endpoint) is the missed-event backstop: a driver reconnecting after
    being offline compares their last-known plan_version to the fresh
    fetch's, and a mismatch alone is enough to know a resync is needed even
    if the pub/sub message that caused it was never received.
    """

    async def event_generator():
        redis = get_client()
        pubsub = redis.pubsub()
        channel = f"driver_route_events:{driver.driver_id}"
        await pubsub.subscribe(channel)
        try:
            yield {"event": "connected", "data": "{}"}
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                yield {"event": "route_updated", "data": message["data"]}
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return EventSourceResponse(event_generator())


async def _get_owned_stop(
    session: AsyncSession, stop_id: str, driver: AuthedDriver, *, for_update: bool = False
) -> Stop:
    stop = await session.get(Stop, uuid.UUID(stop_id), with_for_update=for_update)
    if stop is None:
        raise HTTPException(status_code=404, detail="Stop not found")
    route = await session.get(Route, stop.route_id)
    if route is None or str(route.driver_id) != driver.driver_id:
        raise HTTPException(status_code=404, detail="Stop not found")
    return stop


async def _stop_view_after_reload(session: AsyncSession, stop: Stop) -> StopView:
    view = await _load_route_view(session, stop.route_id)
    return next(s for s in view.stops if s.stop_id == str(stop.id))


async def _pickup_stop_is_hot_shot(session: AsyncSession, stop_id: uuid.UUID) -> bool:
    """
    A HOT_SHOT pickup stop always carries exactly one order (accept_offer
    never lets it commingle - see that function's docstring), so checking
    that stop's order's tier is enough; a regular pickup stop's order(s)
    are never HOT_SHOT by the same construction.
    """
    order_id_result = await session.execute(
        select(StopOrder.order_id).where(StopOrder.stop_id == stop_id).limit(1)
    )
    order_id = order_id_result.scalar_one_or_none()
    if order_id is None:
        return False
    order = await session.get(Order, order_id)
    return order is not None and order.sla_tier == SLATier.HOT_SHOT


async def _notify_shop_for_pickup_stop(
    session: AsyncSession, *, hub_id: str, driver_id: str, stop: Stop, event: str
) -> None:
    """event is "picked_up" or "en_route" - see app/messaging/shop_notifications.py."""
    if stop.shop_id is None:
        return
    shop = await session.get(Shop, stop.shop_id)
    if shop is None:
        return
    is_hot_shot = await _pickup_stop_is_hot_shot(session, stop.id)
    notify = notify_shop_picked_up if event == "picked_up" else notify_shop_en_route
    await notify(
        session,
        hub_id=uuid.UUID(hub_id),
        driver_id=uuid.UUID(driver_id),
        stop_id=stop.id,
        shop=shop,
        is_hot_shot=is_hot_shot,
    )


# Stop.status's terminal states - once here, a stop can't transition again.
# Guards below exist so a stale/retried/out-of-order client call can't skip a
# step (complete a dropoff whose pickup was never scanned) or re-run a
# terminal transition's side effects a second time.
_TERMINAL_STOP_STATUSES = {"completed", "failed"}


def _assert_stop_not_terminal(stop: Stop, action: str) -> None:
    if stop.status in _TERMINAL_STOP_STATUSES:
        raise HTTPException(status_code=409, detail=f"Stop is {stop.status}, cannot {action}")


@router.post("/stops/{stop_id}/arrive", response_model=StopView)
async def arrive_at_stop(
    stop_id: str, driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> StopView:
    stop = await _get_owned_stop(session, stop_id, driver)
    _assert_stop_not_terminal(stop, "mark arrived")
    stop.status = "arrived"
    await session.commit()
    return await _stop_view_after_reload(session, stop)


@router.post("/stops/{stop_id}/scan", response_model=StopView)
async def scan_parcels(
    stop_id: str,
    body: ScanParcelsBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> StopView:
    stop = await _get_owned_stop(session, stop_id, driver)
    _assert_stop_not_terminal(stop, "scan parcels")
    if stop.status == "pending":
        raise HTTPException(status_code=409, detail="Arrive at this stop before scanning parcels")
    stop.scanned_count = max(0, min(body.scanned_count, stop.parcel_count))
    await session.commit()
    return await _stop_view_after_reload(session, stop)


@router.post("/stops/{stop_id}/complete", response_model=StopView)
async def complete_stop(
    stop_id: str,
    body: CompleteStopBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> StopView:
    # for_update: without this, two concurrent completion calls for the same
    # stop (e.g. an offline-queue retry racing a request that already landed)
    # could both read status != "completed" before either commits.
    stop = await _get_owned_stop(session, stop_id, driver, for_update=True)

    if stop.status == "completed":
        # Idempotent replay, not a conflict - an offline-queue retry (or any
        # client that resubmits after a dropped response) must see this as
        # the same success it already got, not a 409. First write wins: a
        # differing payload is logged for observability but never persisted
        # - this endpoint's idempotency exists to make blind retries of an
        # identical request safe, not to let a second call silently amend
        # already-committed proof-of-delivery.
        if (body.method, body.photo_url, body.signature_url, body.pin, body.left_at) != (
            stop.pod_method,
            stop.pod_photo_url,
            stop.pod_signature_url,
            stop.pod_pin,
            stop.pod_left_at,
        ):
            logger.warning(
                "stop_complete_replay_payload_mismatch",
                stop_id=stop_id,
                driver_id=driver.driver_id,
            )
        return await _stop_view_after_reload(session, stop)

    _assert_stop_not_terminal(stop, "complete")  # still 409s on status == "failed" - a genuine conflict
    if stop.status == "pending":
        raise HTTPException(status_code=409, detail="Arrive at this stop before completing it")
    if stop.stop_type == "pickup" and stop.scanned_count < stop.parcel_count:
        raise HTTPException(
            status_code=409,
            detail=f"Only {stop.scanned_count}/{stop.parcel_count} parcels scanned",
        )
    if stop.stop_type == "dropoff":
        # Sequence assignment (accept_offer) always numbers every pickup
        # stop before every dropoff stop on a route, so "any earlier-
        # sequenced pickup not yet completed" is exactly "this delivery's
        # pickup hasn't happened yet."
        unfinished_pickups = await session.execute(
            select(func.count())
            .select_from(Stop)
            .where(
                Stop.route_id == stop.route_id,
                Stop.stop_type == "pickup",
                Stop.sequence < stop.sequence,
                # notin_ terminal, not != "completed" - a *failed* pickup is
                # never going to become completed, so treating it as
                # "unfinished" would block this dropoff from ever completing.
                Stop.status.notin_(_TERMINAL_STOP_STATUSES),
            )
        )
        if unfinished_pickups.scalar_one() > 0:
            raise HTTPException(status_code=409, detail="Complete this route's pickup stop(s) first")

    now = datetime.now(timezone.utc)
    stop.status = "completed"
    stop.completed_at = now
    stop.pod_method = body.method
    stop.pod_photo_url = body.photo_url
    stop.pod_signature_url = body.signature_url
    stop.pod_pin = body.pin
    stop.pod_left_at = body.left_at

    # Only a *dropoff* stop's completion means an order was actually
    # delivered - completing a pickup stop just means the parcels were
    # collected, so its orders stay "assigned" (there's no intermediate
    # "picked up" OrderStatus value in v1; the route/stop status already
    # captures that detail more precisely than Order.status does).
    if stop.stop_type == "dropoff":
        order_ids_result = await session.execute(select(StopOrder.order_id).where(StopOrder.stop_id == stop.id))
        order_ids = [row[0] for row in order_ids_result.all()]
        if order_ids:
            await session.execute(
                update(Order).where(Order.id.in_(order_ids)).values(status=OrderStatus.delivered)
            )

    remaining_result = await session.execute(
        select(func.count())
        .select_from(Stop)
        .where(Stop.route_id == stop.route_id, Stop.status.notin_(_TERMINAL_STOP_STATUSES))
    )
    route_finished = remaining_result.scalar_one() == 0
    if route_finished:
        route = await session.get(Route, stop.route_id)
        route.status = "completed"

    await session.commit()

    # Phase 8 shop SMS - completing a pickup stop means (1) that shop just
    # had their order picked up, and (2) whichever pickup stop is next in
    # sequence on this route (if any, not yet completed) just became the
    # driver's next stop, i.e. "en route" to that shop now. Best-effort:
    # runs after the stop-completion commit above, so a shop with no phone
    # on file or a send failure never blocks completing the stop itself.
    if stop.stop_type == "pickup":
        await _notify_shop_for_pickup_stop(
            session, hub_id=driver.hub_id, driver_id=driver.driver_id, stop=stop, event="picked_up"
        )
        next_pickup_result = await session.execute(
            select(Stop)
            .where(
                Stop.route_id == stop.route_id,
                Stop.stop_type == "pickup",
                Stop.sequence > stop.sequence,
                Stop.status.notin_(_TERMINAL_STOP_STATUSES),
            )
            .order_by(Stop.sequence)
            .limit(1)
        )
        next_pickup = next_pickup_result.scalar_one_or_none()
        if next_pickup is not None:
            await _notify_shop_for_pickup_stop(
                session, hub_id=driver.hub_id, driver_id=driver.driver_id, stop=next_pickup, event="en_route"
            )
        await session.commit()

    if route_finished:
        # "Stop completed" is the design doc's third event-trigger source
        # (app/optimizer/event_trigger.py flagged it as having no producer
        # yet, since the driver app didn't exist) - this is that producer,
        # fired once the whole route wraps so the fleet frees up promptly.
        manager = FleetStateManager()
        state = await manager.get_driver_state(driver.hub_id, driver.driver_id)
        if state:
            state.status = "available"
            state.current_route_id = None
            await manager.upsert_driver_state(state)
        await dispatch_event_bus.publish(driver.hub_id, "stop_completed")

    return await _stop_view_after_reload(session, stop)


@router.post("/stops/{stop_id}/flag", response_model=StopView)
async def flag_stop_issue(
    stop_id: str,
    body: FlagStopBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> StopView:
    """
    "Flag an issue" (wireframe screen of the same name) - a stop that can't
    be completed normally (shop closed, access blocked, a dispute, etc.)
    becomes terminal via a specific reason code instead of being a dead
    end. Not the same thing as StopFlag (app/models/stop.py) - that's an
    ops route-planning annotation for the Learning Loop, a different
    consumer with different semantics; this is a driver-facing incident
    report.
    """
    stop = await _get_owned_stop(session, stop_id, driver, for_update=True)
    _assert_stop_not_terminal(stop, "flag")

    stop.status = "failed"
    stop.failure_reason = body.reason.value
    stop.flag_note = body.note
    stop.flagged_at = datetime.now(timezone.utc)

    order_ids_result = await session.execute(select(StopOrder.order_id).where(StopOrder.stop_id == stop.id))
    order_ids = [row[0] for row in order_ids_result.all()]
    if order_ids:
        await session.execute(
            update(Order).where(Order.id.in_(order_ids)).values(status=OrderStatus.delivery_failed)
        )

    remaining_result = await session.execute(
        select(func.count())
        .select_from(Stop)
        .where(Stop.route_id == stop.route_id, Stop.status.notin_(_TERMINAL_STOP_STATUSES))
    )
    if remaining_result.scalar_one() == 0:
        route = await session.get(Route, stop.route_id)
        route.status = "completed"

    await session.commit()

    # Ops notification reuses the existing in-process event bus, same
    # pattern as complete_stop's "stop_completed" - no new SSE/pubsub here,
    # that's a separate mechanism (see the live route-change push feature).
    await dispatch_event_bus.publish(driver.hub_id, "stop_failed")

    return await _stop_view_after_reload(session, stop)


# ---------------------------------------------------------------------------
# Messaging (screens 1p/1q) - masked SMS via app/messaging/sms_client.py.
# "Masked" means the customer/support side only ever sees LMX's shared
# Twilio number, and the driver app never receives the real counterparty
# phone number back (see MessageView, which omits it entirely).
# ---------------------------------------------------------------------------


def _message_view(message: Message) -> MessageView:
    return MessageView(
        message_id=str(message.id),
        channel=message.channel,
        direction=message.direction,
        body=message.body,
        created_at=message.created_at,
        stop_id=str(message.stop_id) if message.stop_id else None,
    )


@router.post("/stops/{stop_id}/message-customer", response_model=MessageView)
async def message_customer(
    stop_id: str,
    body: SendMessageBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> MessageView:
    stop = await _get_owned_stop(session, stop_id, driver)
    if stop.stop_type != "dropoff":
        raise HTTPException(status_code=409, detail="Only a dropoff stop has a customer to message")

    order_id_result = await session.execute(select(StopOrder.order_id).where(StopOrder.stop_id == stop.id))
    order_id = order_id_result.scalar_one_or_none()
    order = await session.get(Order, order_id) if order_id else None
    if order is None or not order.delivery_contact_phone:
        raise HTTPException(status_code=409, detail="No customer contact number on file for this stop")

    twilio_sid = await get_sms_client().send(order.delivery_contact_phone, body.body)
    message = Message(
        hub_id=uuid.UUID(driver.hub_id),
        driver_id=uuid.UUID(driver.driver_id),
        stop_id=stop.id,
        channel="customer",
        direction="outbound",
        body=body.body,
        counterparty_phone=order.delivery_contact_phone,
        twilio_sid=twilio_sid,
    )
    session.add(message)
    await session.commit()
    return _message_view(message)


@router.get("/stops/{stop_id}/messages", response_model=list[MessageView])
async def list_customer_messages(
    stop_id: str, driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> list[MessageView]:
    stop = await _get_owned_stop(session, stop_id, driver)
    result = await session.execute(
        select(Message).where(Message.stop_id == stop.id).order_by(Message.created_at)
    )
    return [_message_view(m) for m in result.scalars().all()]


@router.post("/me/messages", response_model=MessageView)
async def message_support(
    body: SendMessageBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> MessageView:
    # Unlike message_customer, there's no hard failure if
    # SUPPORT_PHONE_NUMBER isn't configured (app/config.py) - the message
    # is still recorded so it's not silently lost, just not actually sent
    # anywhere yet. Same "unconfigured -> store, don't pretend" pattern the
    # rest of this pass uses.
    twilio_sid = None
    if settings.support_phone_number:
        twilio_sid = await get_sms_client().send(settings.support_phone_number, body.body)

    message = Message(
        hub_id=uuid.UUID(driver.hub_id),
        driver_id=uuid.UUID(driver.driver_id),
        stop_id=None,
        channel="support",
        direction="outbound",
        body=body.body,
        counterparty_phone=settings.support_phone_number,
        twilio_sid=twilio_sid,
    )
    session.add(message)
    await session.commit()
    return _message_view(message)


@router.get("/me/messages", response_model=list[MessageView])
async def list_support_messages(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> list[MessageView]:
    result = await session.execute(
        select(Message)
        .where(Message.driver_id == uuid.UUID(driver.driver_id), Message.channel == "support")
        .order_by(Message.created_at)
    )
    return [_message_view(m) for m in result.scalars().all()]


# ---------------------------------------------------------------------------
# Earnings + trip history (screens 1n/1o) - see EarningsView/TripSummaryView
# docstrings (app/schemas/driver_app.py) for why this is explicitly labeled
# an estimate rather than a real payroll figure.
# ---------------------------------------------------------------------------

# Placeholder-flagged, not tuned against any real wage decision - see
# docs/NEXT_STEPS.md item 14. A single global rate rather than a
# per-driver field because there's no admin UI or payroll integration yet
# to set one meaningfully; swapping this for a real per-driver/per-hub
# rate is a contained change once that exists.
PLACEHOLDER_HOURLY_RATE_CENTS = 1_800  # $18.00/hr


def _week_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


async def _completed_routes_this_week(session: AsyncSession, driver_id: str) -> list[Route]:
    start, end = _week_bounds(datetime.now(timezone.utc))
    result = await session.execute(
        select(Route).where(
            Route.driver_id == uuid.UUID(driver_id),
            Route.status == "completed",
            Route.updated_at >= start,
            Route.updated_at < end,
        )
    )
    return list(result.scalars().all())


def _route_hours(route: Route) -> float:
    # Proxy for "hours worked" - route.created_at (job accepted) to
    # route.updated_at (last touched, which for a completed route is when
    # its last stop finished - see complete_stop above). There's no
    # clock-in/out event anywhere in this system, so this doesn't subtract
    # breaks or account for time before the route was created; it's a
    # reasonable estimate, not a timesheet.
    return max((route.updated_at - route.created_at).total_seconds() / 3600, 0.0)


@router.get("/me/earnings", response_model=EarningsView)
async def get_my_earnings(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> EarningsView:
    start, end = _week_bounds(datetime.now(timezone.utc))
    routes = await _completed_routes_this_week(session, driver.driver_id)
    hours_worked = sum(_route_hours(r) for r in routes)
    estimated_pay_cents = round(hours_worked * PLACEHOLDER_HOURLY_RATE_CENTS)
    return EarningsView(
        period_start=start.date(),
        period_end=(end - timedelta(days=1)).date(),
        hours_worked=round(hours_worked, 2),
        hourly_rate_cents=PLACEHOLDER_HOURLY_RATE_CENTS,
        estimated_pay_cents=estimated_pay_cents,
    )


@router.get("/me/trips", response_model=list[TripSummaryView])
async def list_my_trips(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> list[TripSummaryView]:
    result = await session.execute(
        select(Route)
        .where(Route.driver_id == uuid.UUID(driver.driver_id), Route.status == "completed")
        .order_by(Route.updated_at.desc())
    )
    routes = list(result.scalars().all())
    if not routes:
        return []

    stop_counts_result = await session.execute(
        select(Stop.route_id, func.count())
        .where(Stop.route_id.in_([r.id for r in routes]))
        .group_by(Stop.route_id)
    )
    stop_counts = dict(stop_counts_result.all())

    return [
        TripSummaryView(
            route_id=str(route.id),
            completed_at=route.updated_at,
            stop_count=stop_counts.get(route.id, 0),
            hours=round(_route_hours(route), 2),
        )
        for route in routes
    ]
