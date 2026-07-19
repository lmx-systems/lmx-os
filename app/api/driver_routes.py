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
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.db import get_db
from app.driver_auth.dependencies import AuthedDriver, get_current_driver
from app.driver_auth.otp_store import OtpStore
from app.driver_auth.tokens import issue_token
from app.fleet_state.manager import FleetStateManager
from app.models.driver import Driver
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.optimizer.event_trigger import dispatch_event_bus
from app.schemas.driver_app import (
    CompleteStopBody,
    DriverAvailabilityUpdate,
    DriverProfileUpdate,
    DriverProfileView,
    JobOfferView,
    OfferStopSummary,
    RouteView,
    ScanParcelsBody,
    StopView,
)
from app.schemas.driver_auth import AuthToken, RequestOtpBody, RequestOtpResult, VerifyOtpBody
from app.schemas.fleet import DriverState

router = APIRouter(prefix="/driver", tags=["driver"])


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

    issued = await OtpStore().issue(body.phone)
    return RequestOtpResult(ok=True, debug_code=None if issued.sent_via_sms else issued.code)


@router.post("/auth/verify-otp", response_model=AuthToken)
async def verify_otp(body: VerifyOtpBody, session: AsyncSession = Depends(get_db)) -> AuthToken:
    if not await OtpStore().verify(body.phone, body.code):
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    result = await session.execute(select(Driver).where(Driver.phone == body.phone))
    driver = result.scalar_one_or_none()
    if driver is None:
        raise HTTPException(status_code=404, detail="No driver registered with this phone number")

    return AuthToken(access_token=issue_token(str(driver.id), str(driver.hub_id)))


# ---------------------------------------------------------------------------
# Profile + availability (screens 1c, 1d/1e)
# ---------------------------------------------------------------------------


def _profile_view(row: Driver) -> DriverProfileView:
    return DriverProfileView(
        driver_id=str(row.id),
        hub_id=str(row.hub_id),
        name=row.name,
        phone=row.phone,
        status=row.status,
        vehicle_type=row.vehicle_type,
        plate_number=row.plate_number,
        delivery_zone=row.delivery_zone,
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
    return _profile_view(await _get_driver_row(session, driver))


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
    return _profile_view(row)


@router.post("/me/state")
async def update_my_availability(
    body: DriverAvailabilityUpdate,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> dict:
    row = await _get_driver_row(session, driver)
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


async def _requeue_orders_from_offer(session: AsyncSession, hub_id: str, stop_payload: list[dict]) -> None:
    """
    A declined/expired offer never touches Route/Stop - the orders just go
    back to the hold queue (Redis) with their original geography/SLA tier
    so the next Dispatch Optimizer cycle tries again, and Order.status
    reverts from "assigned" (set optimistically the moment the optimizer
    proposed the offer - see app/optimizer/service.py) back to "queued" so
    it doesn't lie about a dispatch that never actually happened.
    """
    hold_queue = HoldQueueStore()
    now = datetime.now(timezone.utc)
    order_ids = [uuid.UUID(s["order_id"]) for s in stop_payload]
    if order_ids:
        await session.execute(
            update(Order).where(Order.id.in_(order_ids)).values(status=OrderStatus.queued)
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


@router.get("/me/offers", response_model=list[JobOfferView])
async def list_my_offers(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> list[JobOfferView]:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(RouteOffer).where(
            RouteOffer.driver_id == uuid.UUID(driver.driver_id), RouteOffer.status == "offered"
        )
    )
    offers = result.scalars().all()

    live: list[JobOfferView] = []
    for offer in offers:
        if offer.expires_at <= now:
            offer.status = "expired"
            offer.responded_at = now
            await _requeue_orders_from_offer(session, str(offer.hub_id), offer.stop_payload)
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
    offer = await _get_owned_offer(session, offer_id, driver)
    if offer.status != "offered":
        raise HTTPException(status_code=409, detail=f"Offer is {offer.status}, not open for a response")

    offer.status = "declined"
    offer.responded_at = datetime.now(timezone.utc)
    await _requeue_orders_from_offer(session, str(offer.hub_id), offer.stop_payload)
    await session.commit()
    return {"ok": True}


@router.post("/offers/{offer_id}/accept", response_model=RouteView)
async def accept_offer(
    offer_id: str,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> RouteView:
    offer = await _get_owned_offer(session, offer_id, driver)
    if offer.status != "offered":
        raise HTTPException(status_code=409, detail=f"Offer is {offer.status}, not open for a response")

    now = datetime.now(timezone.utc)
    if offer.expires_at <= now:
        offer.status = "expired"
        offer.responded_at = now
        await _requeue_orders_from_offer(session, str(offer.hub_id), offer.stop_payload)
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
    # from that shop (Section 8 clustering) into a single parcel count.
    orders_by_shop: dict[uuid.UUID, list[uuid.UUID]] = {}
    for order in orders_by_id.values():
        orders_by_shop.setdefault(order.shop_id, []).append(order.id)

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
        sequence += 1

    # One dropoff stop per order, in the sequence the optimizer assigned
    # them - see app/models/order.py's delivery_* fields and the module
    # docstring on drop-sequencing being unoptimized in v1.
    for stop_summary in offer.stop_payload:
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
    return await _load_route_view(session, route.id)


async def _get_owned_offer(session: AsyncSession, offer_id: str, driver: AuthedDriver) -> RouteOffer:
    offer = await session.get(RouteOffer, uuid.UUID(offer_id))
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


async def _get_owned_stop(session: AsyncSession, stop_id: str, driver: AuthedDriver) -> Stop:
    stop = await session.get(Stop, uuid.UUID(stop_id))
    if stop is None:
        raise HTTPException(status_code=404, detail="Stop not found")
    route = await session.get(Route, stop.route_id)
    if route is None or str(route.driver_id) != driver.driver_id:
        raise HTTPException(status_code=404, detail="Stop not found")
    return stop


async def _stop_view_after_reload(session: AsyncSession, stop: Stop) -> StopView:
    view = await _load_route_view(session, stop.route_id)
    return next(s for s in view.stops if s.stop_id == str(stop.id))


@router.post("/stops/{stop_id}/arrive", response_model=StopView)
async def arrive_at_stop(
    stop_id: str, driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> StopView:
    stop = await _get_owned_stop(session, stop_id, driver)
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
    stop = await _get_owned_stop(session, stop_id, driver)
    now = datetime.now(timezone.utc)
    stop.status = "completed"
    stop.completed_at = now
    stop.pod_method = body.method
    stop.pod_photo_url = body.photo_url
    stop.pod_signature_url = body.signature_url
    stop.pod_pin = body.pin

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
        select(func.count()).select_from(Stop).where(Stop.route_id == stop.route_id, Stop.status != "completed")
    )
    route_finished = remaining_result.scalar_one() == 0
    if route_finished:
        route = await session.get(Route, stop.route_id)
        route.status = "completed"

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
