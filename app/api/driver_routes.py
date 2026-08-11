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
import app.payroll.hours as payroll_hours
from app.payroll import get_payout_provider
from app.payroll.gig_pricing import estimate_delivery_pay_cents
from app.redis_client import get_client
from app.messaging.delivery_pin import MAX_PIN_VERIFICATION_ATTEMPTS, generate_delivery_pin, send_delivery_pin_sms
from app.messaging.shop_notifications import (
    notify_shop_delivery_failed,
    notify_shop_en_route,
    notify_shop_picked_up,
)
from app.messaging.sms_client import get_sms_client
from app.messaging.voice_client import get_voice_client
from app.models.call import Call
from app.models.driver import Driver
from app.models.driver_device import DriverDevice
from app.gig_platform import service as gig_store
from app.gig_platform.accept_gate import evaluate_offer
from app.models.driver_document import DriverDocument
from app.models.driver_location_ping import DriverLocationPing
from app.models.gig_job import GigJob
from app.models.driver_shift_event import DriverShiftEvent
from app.models.gig_payout import GigPayout
from app.models.message import Message
from app.models.order import Order, OrderStatus, SLATier
from app.models.parcel import Parcel
from app.models.return_item import ReturnItem
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.optimizer.event_trigger import dispatch_event_bus
from app.messaging.cod_notifications import ESCALATION_SENT, notify_shop_of_cod_dispute
from app.messaging.tracking_notifications import notify_recipient_picked_up
from app.orders.status_service import advance_orders
from app.returns.service import return_views
from app.schemas.returns import CollectReturnBody, ReturnItemView
from app.schemas.driver_app import (
    CallView,
    CompleteStopBody,
    DeclineOfferBody,
    DriverAvailabilityUpdate,
    DriverComplianceProblemView,
    DriverComplianceView,
    DriverDocumentUpdate,
    DriverDocumentUploadBody,
    DriverDocumentView,
    DriverLocationPingBody,
    DriverProfileUpdate,
    DriverProfileView,
    EarningsView,
    FlagStopBody,
    JobOfferView,
    MessageView,
    ParcelView,
    OfferStopSummary,
    PaymentMethodUpdate,
    RouteView,
    ScanParcelBody,
    ScanParcelsBody,
    SendMessageBody,
    CodDisputeBody,
    CodObligationView,
    CollectCodBody,
    StopProofRequirementView,
    StopView,
    TripSummaryView,
    UploadUrlRequestBody,
    UploadUrlResult,
)
from app.schemas.driver_auth import (
    AuthToken,
    DriverDeviceView,
    PushTokenBody,
    RequestOtpBody,
    RequestOtpResult,
    VerifyOtpBody,
)
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.gig import (
    AcceptVerdictView,
    GigJobIntake,
    GigJobStatusUpdate,
    GigJobView,
    MarginalEconomicsView,
)
from app.compliance.driver_documents import evaluate_driver_documents
from app.delivery.cod import (
    CodError,
    CodNotSettled,
    assert_cod_settled,
    cod_obligations,
    record_collection,
    record_dispute,
)
from app.delivery.en_route import mark_current_stop_en_route
from app.delivery.eta import refresh_route_etas
from app.models.cod_collection import CodCollection
from app.delivery.proof import ProofNotSatisfied, assert_proof_satisfied, resolve_stop_proof
from app.models.driver_document import REQUIRED_DOC_TYPES, REVIEW_PENDING
from app.storage.document_upload_client import (
    UnsupportedDocumentType,
    create_document_upload,
)
from app.storage.photo_upload_client import generate_object_key, get_photo_upload_client

router = APIRouter(prefix="/driver", tags=["driver"])
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Auth (screens 1a/1b) - the only two endpoints in this router that don't
# require get_current_driver, since their whole point is to produce a token.
# ---------------------------------------------------------------------------


@router.post("/auth/request-otp", response_model=RequestOtpResult)
async def request_otp(body: RequestOtpBody, session: AsyncSession = Depends(get_db)) -> RequestOtpResult:
    otp_store = OtpStore()
    try:
        # Charged before the existence check below, not after - otherwise
        # the 404/200 distinction on an unthrottled endpoint is a phone-
        # number-enumeration oracle for who's a registered driver.
        await otp_store.check_rate_limit(body.phone)
    except OtpRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    result = await session.execute(select(Driver.id).where(Driver.phone == body.phone))
    if result.scalar_one_or_none() is None:
        # Drivers are provisioned by ops, not self-registered - see 1a's
        # "Apply to drive" annotation (out of app scope).
        raise HTTPException(status_code=404, detail="No driver registered with this phone number")

    issued = await otp_store.issue(body.phone, skip_rate_limit_check=True)
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


@router.post("/me/push-token", status_code=204)
async def register_push_token(
    body: PushTokenBody, driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> None:
    """Called once per app launch after sign-in (docs/ROADMAP.md A1) so
    app/messaging/job_offer_notifications.py has somewhere real to send a
    new-job-offer push. `body.device_id` must already have a DriverDevice
    row - verify_otp creates one for every device the moment it signs in,
    so a call here for a device that was never signed in is a genuine
    404, not a race to handle."""
    result = await session.execute(
        select(DriverDevice).where(
            DriverDevice.driver_id == uuid.UUID(driver.driver_id), DriverDevice.device_id == body.device_id
        )
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    device.expo_push_token = body.expo_push_token
    device.push_token_registered_at = datetime.now(timezone.utc)
    await session.commit()


@router.post("/me/location", status_code=204)
async def report_my_location(
    body: DriverLocationPingBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> None:
    """The driver's own device reporting where it is (docs/ROADMAP.md F1).

    Before this endpoint the *only* way a driver's position could be set was
    POST /fleet/{hub_id}/drivers/location, which requires an ops admin
    (app/api/routes.py) - so in production nothing would ever have
    populated it, and app/optimizer/service.py skips any driver whose
    location is None. That made this the difference between the optimizer
    assigning work and silently assigning none.

    Writes twice, deliberately:

    - Redis, via FleetStateManager, is the optimizer's hot path and holds
      only the current position.
    - Postgres (app/models/driver_location_ping.py) is the durable trail,
      because Redis overwrites and miles-per-drop (W9's scorecard) needs
      the path travelled.

    The hub comes from the driver's own JWT, never from the request body -
    a driver cannot report a position into another hub's fleet state.

    Deliberately does NOT publish a dispatch event. app/api/routes.py's
    upsert_driver_state already documents the rule: a status change alters
    what the optimizer can assign, a raw location ping does not. Publishing
    here would re-run a hub's whole optimization cycle every 30 seconds per
    on-duty driver.
    """
    manager = FleetStateManager()
    await manager.update_driver_location(
        DriverLocation(
            driver_id=driver.driver_id,
            lat=body.lat,
            lng=body.lng,
            recorded_at=body.recorded_at.isoformat(),
        ),
        driver.hub_id,
    )

    session.add(
        DriverLocationPing(
            driver_id=uuid.UUID(driver.driver_id),
            hub_id=uuid.UUID(driver.hub_id),
            lat=body.lat,
            lng=body.lng,
            recorded_at=body.recorded_at,
            accuracy_m=body.accuracy_m,
        )
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Gig-platform jobs (docs/ROADMAP.md G3)
#
# Unrelated to this file's gig *payout* code below (A11), which is about how
# a gig-classified LMX driver gets paid. These endpoints are about work
# sourced from Curri/Dispatch/Roadie.
#
# Manual entry is the intended v1 path and these endpoints are it: at three
# drivers, typing an offer in costs minutes a day, while automated intake is
# the riskiest work in the section and a 30-driver problem. G1's notification
# listener and G2's share-sheet extraction will call the same store with the
# same shape, so adding them changes nothing here.
# ---------------------------------------------------------------------------


@router.post("/me/gig-jobs", response_model=GigJobView, status_code=201)
async def record_my_gig_job(
    body: GigJobIntake,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> GigJobView:
    """Record a gig-platform offer that surfaced on this driver's account.

    Hub and driver both come from the JWT, never the body - the offer
    arrived on this driver's own platform account, and on the gig track that
    is precisely what pins the job to them.

    A repeat of the same platform ref is a 409 rather than a second row.
    Once G1/G2 exist this stops being an edge case: a notification and a
    manual entry can easily capture the same offer, and silently keeping
    both would corrupt the density figures (G12) that decide when batching
    becomes possible.
    """
    try:
        job = await gig_store.record_job(
            session, body, hub_id=driver.hub_id, driver_id=driver.driver_id
        )
    except gig_store.DuplicateGigJob as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{body.source_platform} job {body.platform_job_ref} is already recorded",
        ) from exc
    return gig_store.gig_job_view(job)


@router.post("/me/gig-jobs/evaluate", response_model=AcceptVerdictView)
async def evaluate_gig_offer(
    body: GigJobIntake,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> AcceptVerdictView:
    """Take it or skip it (docs/ROADMAP.md G4).

    Evaluates an offer WITHOUT recording it - a platform offer lives about
    45 seconds and most get declined, so the common path shouldn't write a
    row. Recording is a separate call the driver makes if they take it.

    Judged against the driver's live position and everything they've already
    promised. A driver whose app has never reported a position can't be
    evaluated at all, which is a 409 rather than a guess: assuming a
    location would produce a confident answer about the wrong starting
    point, and the reachability check is the one doing most of the work.
    """
    location = await FleetStateManager().get_driver_location(driver.hub_id, driver.driver_id)
    if location is None:
        raise HTTPException(
            status_code=409,
            detail="No current location for this driver - go on duty so the app reports position.",
        )

    row = await _get_driver_row(session, driver)
    committed = [
        job
        for job in await gig_store.list_for_driver(session, driver.driver_id)
        if job.status in ("offered", "accepted", "picked_up")
    ]

    # Evaluated as a transient GigJob rather than a dict so the gate works on
    # exactly the same shape whether an offer has been recorded or not.
    candidate = GigJob(
        hub_id=uuid.UUID(driver.hub_id),
        driver_id=uuid.UUID(driver.driver_id),
        source_platform=body.source_platform,
        intake_source=body.intake_source,
        platform_job_ref=body.platform_job_ref,
        pickup_address=body.pickup_address,
        pickup_lat=body.pickup_lat,
        pickup_lng=body.pickup_lng,
        dropoff_address=body.dropoff_address,
        dropoff_lat=body.dropoff_lat,
        dropoff_lng=body.dropoff_lng,
        pickup_window_open=body.pickup_window_open,
        pickup_window_close=body.pickup_window_close,
        dropoff_window_open=body.dropoff_window_open,
        dropoff_window_close=body.dropoff_window_close,
        pay_cents=body.pay_cents,
        distance_miles=body.distance_miles,
        assignment_scope=body.assignment_scope,
        status="offered",
    )

    verdict = evaluate_offer(
        offer=candidate,
        driver_lat=location.lat,
        driver_lng=location.lng,
        committed=committed,
        capacity_units=row.vehicle_capacity_units,
    )

    return AcceptVerdictView(
        accept=verdict.accept,
        reason=verdict.reason,
        detail=verdict.detail,
        economics=(
            MarginalEconomicsView(
                pay_cents=verdict.economics.pay_cents,
                deadhead_miles=verdict.economics.deadhead_miles,
                engaged_miles=verdict.economics.engaged_miles,
                reposition_miles=verdict.economics.reposition_miles,
                vehicle_cost_cents=verdict.economics.vehicle_cost_cents,
                time_cost_cents=verdict.economics.time_cost_cents,
                total_cost_cents=verdict.economics.total_cost_cents,
                margin_cents=verdict.economics.margin_cents,
                total_minutes=verdict.economics.total_minutes,
                effective_hourly_cents=verdict.economics.effective_hourly_cents,
            )
            if verdict.economics is not None
            else None
        ),
    )


@router.get("/me/gig-jobs", response_model=list[GigJobView])
async def list_my_gig_jobs(
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> list[GigJobView]:
    """This driver's gig jobs across every platform, pickup-window order.

    One ordered day spanning three platforms is the point of the store being
    multi-platform - today a driver reconciles Curri, Dispatch and Roadie in
    their head. G6 builds the real itinerary surface on this.
    """
    jobs = await gig_store.list_for_driver(session, driver.driver_id)
    return [gig_store.gig_job_view(job) for job in jobs]


@router.patch("/me/gig-jobs/{gig_job_id}", response_model=GigJobView)
async def update_my_gig_job_status(
    gig_job_id: str,
    body: GigJobStatusUpdate,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> GigJobView:
    """Move one of this driver's gig jobs along its lifecycle.

    Note what this does NOT do: it does not mark the job delivered on the
    platform. Under the gig track the driver still has to close it in the
    platform's own app to get paid, so this records our view of reality
    rather than driving it. That double entry is the friction most likely to
    make drivers quietly abandon the app, and it needs a real design answer
    (G11) rather than being papered over here.
    """
    job = await session.get(GigJob, uuid.UUID(gig_job_id))
    if job is None or str(job.driver_id) != driver.driver_id:
        # Same 404 for "doesn't exist" and "belongs to someone else" - a
        # distinct 403 would confirm the id is real to a driver who has no
        # business knowing that.
        raise HTTPException(status_code=404, detail="Gig job not found")

    try:
        job = await gig_store.transition(session, job, body.status)
    except gig_store.InvalidGigJobTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return gig_store.gig_job_view(job)


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
        employment_type=row.employment_type,
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


def _document_view(doc: DriverDocument) -> DriverDocumentView:
    return DriverDocumentView(
        doc_type=doc.doc_type,
        claimed_expires_at=doc.claimed_expires_at,
        verified_expires_at=doc.verified_expires_at,
        review_status=doc.review_status,
        rejection_reason=doc.rejection_reason,
        file_url=doc.file_url,
        is_usable=doc.is_usable_on,
    )


def _require_known_doc_type(doc_type: str) -> str:
    """`doc_type` arrives as a URL path segment and used to be stored verbatim, so
    a driver could invent document types. Against a gate that checks for PRESENCE
    of required types that would be a way to clutter their own record with rows
    nobody asked for."""
    if doc_type not in REQUIRED_DOC_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown document type - expected one of {', '.join(REQUIRED_DOC_TYPES)}",
        )
    return doc_type


async def _my_document(
    session: AsyncSession, driver: AuthedDriver, doc_type: str
) -> DriverDocument | None:
    result = await session.execute(
        select(DriverDocument).where(
            DriverDocument.driver_id == uuid.UUID(driver.driver_id),
            DriverDocument.doc_type == doc_type,
        )
    )
    return result.scalar_one_or_none()


@router.get("/me/documents", response_model=list[DriverDocumentView])
async def list_my_documents(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> list[DriverDocumentView]:
    result = await session.execute(
        select(DriverDocument).where(DriverDocument.driver_id == uuid.UUID(driver.driver_id))
    )
    return [_document_view(doc) for doc in result.scalars().all()]


@router.get("/me/compliance", response_model=DriverComplianceView)
async def my_compliance(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> DriverComplianceView:
    """Why the "go online" toggle is disabled, if it is (docs/ROADMAP.md R4).

    Exists so the app can explain the block up front instead of letting a driver
    discover it by tapping the toggle and getting a 409 - and so the explanation is
    the SAME computation the gate uses, rather than the app's own guess at it.
    """
    result = await evaluate_driver_documents(session, driver.driver_id)
    return DriverComplianceView(
        can_go_on_shift=result.can_go_on_shift,
        problems=[
            DriverComplianceProblemView(
                doc_type=problem.doc_type, reason=problem.reason, detail=problem.detail
            )
            for problem in result.problems
        ],
    )


@router.post("/me/documents/{doc_type}/upload-url", response_model=UploadUrlResult)
async def create_document_upload_url(
    doc_type: str,
    body: DriverDocumentUploadBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> UploadUrlResult:
    """Somewhere to put a photo of a license or insurance card.

    **This replaces the driver being able to name their own `file_url`.** Before
    this, `PUT /driver/me/documents/{doc_type}` accepted any string and stored it
    as compliance evidence, so a fabricated URL was indistinguishable from a real
    scan. The backend now mints the object key, and writes `file_url` itself from
    that key - so the row can only point at something we hold.

    Submitting a new upload resets the review: a document that was verified, then
    replaced, is not still verified. Re-uploading after a rejection is the normal
    path, and it must put the row back in the queue rather than leaving the old
    verdict attached to new evidence.
    """
    _require_known_doc_type(doc_type)
    try:
        upload, key = create_document_upload(driver.driver_id, doc_type, body.content_type)
    except UnsupportedDocumentType as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    doc = await _my_document(session, driver, doc_type)
    if doc is None:
        doc = DriverDocument(
            driver_id=uuid.UUID(driver.driver_id),
            doc_type=doc_type,
            claimed_expires_at=body.claimed_expires_at,
        )
        session.add(doc)

    doc.claimed_expires_at = body.claimed_expires_at
    doc.file_url = upload.final_url
    # New evidence, so no verdict. Everything a previous review established is
    # about a file this row no longer points at.
    doc.review_status = REVIEW_PENDING
    doc.verified_expires_at = None
    doc.reviewed_at = None
    doc.reviewed_by_ops_user_id = None
    doc.rejection_reason = None
    await session.commit()

    logger.info(
        "driver_document_uploaded",
        driver_id=driver.driver_id,
        doc_type=doc_type,
        object_key=key,
    )
    return UploadUrlResult(
        upload_url=upload.upload_url,
        final_url=upload.final_url,
        requires_upload=upload.requires_upload,
    )


@router.put("/me/documents/{doc_type}", response_model=DriverDocumentView)
async def update_my_document(
    doc_type: str,
    body: DriverDocumentUpdate,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> DriverDocumentView:
    """Correct the expiry date a driver gave us.

    Records a CLAIM and nothing more (docs/ROADMAP.md R4). This endpoint used to
    be the whole compliance story - the driver set the date the gate then read, so
    a lapsed license became a valid one by typing next year. The date is now
    context for a reviewer; only `verified_expires_at`, set by an ops user reading
    the document, opens the gate.

    Changing the claimed date sends the document back for review: if we had already
    verified it against one date and the driver now says a different one, the
    verdict no longer matches what they are asserting.
    """
    _require_known_doc_type(doc_type)
    doc = await _my_document(session, driver, doc_type)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail="Upload the document first - there's nothing on file to correct",
        )

    if doc.claimed_expires_at != body.claimed_expires_at:
        doc.review_status = REVIEW_PENDING
        doc.verified_expires_at = None
        doc.reviewed_at = None
        doc.reviewed_by_ops_user_id = None
        doc.rejection_reason = None
    doc.claimed_expires_at = body.claimed_expires_at
    await session.commit()
    return _document_view(doc)


@router.post("/me/state")
async def update_my_availability(
    body: DriverAvailabilityUpdate,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> dict:
    row = await _get_driver_row(session, driver)

    if body.status == "available":
        # **The gate that used to be defeatable two ways** (docs/ROADMAP.md R4).
        # It refused only when a document row on file had passed an expiry date the
        # DRIVER had typed - so typing next year got you online, and having no
        # documents at all got you online too, because nothing on file could be
        # expired. `evaluate_driver_documents` requires each document to be
        # present, reviewed by an ops user, and unexpired per the reviewer's date.
        compliance = await evaluate_driver_documents(session, driver.driver_id)
        if not compliance.can_go_on_shift:
            raise HTTPException(
                status_code=409,
                # Every reason at once, so a driver missing two documents isn't
                # sent back twice. GET /driver/me/compliance returns the same
                # structure for the app to render up front.
                detail="; ".join(problem.detail for problem in compliance.problems),
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

    # Durable history of this transition, independent of the Redis fleet
    # state above (which only ever holds the current status) - see
    # app/models/driver_shift_event.py for why this exists.
    session.add(
        DriverShiftEvent(
            driver_id=uuid.UUID(driver.driver_id),
            hub_id=uuid.UUID(driver.hub_id),
            event_type=body.status,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

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
                # Read off the order rather than the offer payload: the offer only
                # ever carried pickup coordinates. Without this a declined or
                # lapsed order would go back into the queue having lost its
                # delivery location, so the next cycle would plan half its journey
                # (app/optimizer/google_routes_client.py::_build_request).
                delivery_lat=(
                    float(order.delivery_lat)
                    if order is not None and order.delivery_lat is not None
                    else None
                ),
                delivery_lng=(
                    float(order.delivery_lng)
                    if order is not None and order.delivery_lng is not None
                    else None
                ),
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


async def _estimate_offer_pay_cents(session: AsyncSession, stop_payload: list[dict]) -> int:
    """Real per-delivery pay estimate for a gig-classified driver's offer
    (docs/ROADMAP.md A11) - each stop_payload entry's own lat/lng is the
    pickup (shop) location (see app/optimizer/service.py's offer
    construction), so only the dropoff side needs a lookup here."""
    order_ids = [uuid.UUID(s["order_id"]) for s in stop_payload]
    orders_result = await session.execute(select(Order).where(Order.id.in_(order_ids)))
    orders_by_id = {o.id: o for o in orders_result.scalars().all()}

    total_cents = 0
    for stop_summary in stop_payload:
        order = orders_by_id.get(uuid.UUID(stop_summary["order_id"]))
        if order is None or order.delivery_lat is None or order.delivery_lng is None:
            continue
        total_cents += estimate_delivery_pay_cents(
            pickup_lat=stop_summary["lat"],
            pickup_lng=stop_summary["lng"],
            dropoff_lat=float(order.delivery_lat),
            dropoff_lng=float(order.delivery_lng),
            sla_tier=stop_summary.get("sla_tier"),
        )
    return total_cents


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

    # Real per-delivery pay (docs/ROADMAP.md A11) is only shown to
    # gig-classified drivers - w2/1099 already understand their pay as
    # hourly/monthly (app/payroll/hours.py), and showing a per-offer dollar
    # amount there would misrepresent how they're actually paid.
    driver_row = await _get_driver_row(session, driver)

    live: list[JobOfferView] = []
    for offer in offers:
        if await _expire_if_lapsed(session, offer):
            continue
        estimated_pay_cents = None
        if driver_row.employment_type == "gig":
            estimated_pay_cents = await _estimate_offer_pay_cents(session, offer.stop_payload)
        live.append(
            JobOfferView(
                offer_id=str(offer.id),
                hub_id=str(offer.hub_id),
                expires_at=offer.expires_at,
                stops=[OfferStopSummary(**s) for s in offer.stop_payload],
                estimated_pay_cents=estimated_pay_cents,
            )
        )
    await session.commit()
    return live


@router.post("/offers/{offer_id}/decline")
async def decline_offer(
    offer_id: str,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
    body: DeclineOfferBody | None = None,
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
    # Ground-truth capture (docs/ROADMAP.md I1) - null when the caller gave
    # no reason, which is fine; a reason is a bonus signal, not required.
    if body is not None:
        offer.decline_reason = body.reason
    await _requeue_orders_from_offer(session, str(offer.hub_id), str(offer.driver_id), offer.stop_payload)
    await session.commit()
    return {"ok": True}


async def _parcel_count_for_orders(session: AsyncSession, order_ids: list[uuid.UUID]) -> int:
    """Total Parcel rows across these orders (docs/ROADMAP.md W10). 0 when
    none exist - callers fall back to the pre-W10 one-per-order count."""
    if not order_ids:
        return 0
    result = await session.execute(
        select(func.count()).select_from(Parcel).where(Parcel.order_id.in_(order_ids))
    )
    return int(result.scalar_one())


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
            # Real parcel count (docs/ROADMAP.md W10), falling back to 1 for
            # an order with no Parcel rows (e.g. seeded directly in a test,
            # or ingested before W10) so scan progress still works.
            parcel_count=(await _parcel_count_for_orders(session, [oid])) or 1,
            stop_type="pickup",
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
            # Real parcel count across this pickup's commingled orders (W10),
            # falling back to one-per-order when no Parcel rows exist.
            parcel_count=(await _parcel_count_for_orders(session, shop_order_ids)) or len(shop_order_ids),
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
    # Collected here, sent after the main commit below - same "generate
    # now, notify best-effort afterward" split as the shop SMS further
    # down, so a Twilio blip can never roll back a route the driver
    # already accepted.
    dropoffs_needing_pin_sms: list[tuple[Stop, Order]] = []

    for stop_summary in sorted_stop_payload:
        order = orders_by_id.get(uuid.UUID(stop_summary["order_id"]))
        if order is None:
            continue
        dropoff = Stop(route_id=route.id, shop_id=None, sequence=sequence, stop_type="dropoff", parcel_count=1)
        # Real PIN issuance (docs/ROADMAP.md A4) - only when there's
        # somewhere real to send it. No contact phone on file means
        # method="pin" simply won't be an option complete_stop accepts
        # for this stop, same as everywhere else in this app that treats
        # "nothing configured" as "can't do this," not "silently succeed."
        if order.delivery_contact_phone:
            dropoff.delivery_pin = generate_delivery_pin()
            dropoffs_needing_pin_sms.append((dropoff, order))
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

    # A driver has taken this work and is on their way to collect it
    # (LMX_LINK_PLAN.md §1.4). This is the first transition a client actually
    # feels: before it, an order sat at "assigned" from the dispatch cycle with
    # no signal that anyone had picked it up as a job.
    await advance_orders(session, list(orders_by_id.keys()), OrderStatus.en_route_pickup)

    # And the first stop is where they are actually driving (docs/ROADMAP.md L11).
    # `Stop.status` has documented `en_route` since the model was written and nothing
    # ever set it.
    await mark_current_stop_en_route(session, route.id)

    # Per-stop ETAs, now that every Stop row exists and is in its final sequence
    # (app/delivery/eta.py). This is also the only moment `planned_eta` is written, so
    # it has to run after the HOT_SHOT re-sequencing above rather than during it -
    # predicting arrival times for an order the driver has not been given yet would
    # score the wrong plan.
    await refresh_route_etas(session, route.id)

    await session.commit()

    # Real PIN issuance (docs/ROADMAP.md A4): text each dropoff's PIN to
    # its customer now that the route above is durably committed.
    # Best-effort, same reasoning as the shop SMS immediately below.
    for dropoff, order in dropoffs_needing_pin_sms:
        await send_delivery_pin_sms(
            session, hub_id=offer.hub_id, driver_id=offer.driver_id, stop=dropoff, order=order
        )
    if dropoffs_needing_pin_sms:
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
                    proof=await _stop_proof_view(session, stop.id),
                    cod=await _stop_cod_view(session, stop.id),
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
                    # Sent with the stop rather than discovered on rejection: a driver
                    # who learns at the door that this client wanted four photos has
                    # already put the box down.
                    proof=await _stop_proof_view(session, stop.id),
                    cod=await _stop_cod_view(session, stop.id),
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


def _assert_arrived(stop: Stop, action: str) -> None:
    """Refuse an action that requires the driver to be at the stop.

    **Checks for arrival rather than against `pending`, and that distinction is a bug
    fix.** Four call sites spelled "has this driver arrived" as `status == "pending"`,
    which was accurate only while `pending` was the sole pre-arrival state. Filling in
    `en_route` (docs/ROADMAP.md L11) made it wrong at all four: a driver who was merely
    driving toward a stop could scan its parcels and complete it, from anywhere.

    A dead state is not inert - it is a value every guard was written without, and this
    is what it cost to start using one.
    """
    if stop.status != "arrived":
        raise HTTPException(
            status_code=409, detail=f"Arrive at this stop before {action}"
        )


async def _stop_cod_view(session: AsyncSession, stop_id: uuid.UUID) -> list[CodObligationView]:
    """Money owed at this stop, and whether it is settled (docs/ROADMAP.md W2).

    Sent with the stop rather than discovered at the door: a driver who learns there is
    money to collect while the customer is already taking the parts has lost the moment to
    ask for it.
    """
    obligations = await cod_obligations(session, stop_id)
    if not obligations:
        return []

    recorded = {
        str(row[0]): row[1]
        for row in (
            await session.execute(
                select(CodCollection.order_id, CodCollection.outcome).where(
                    CodCollection.stop_id == stop_id
                )
            )
        ).all()
    }
    return [
        CodObligationView(
            order_id=obligation.order_id,
            amount_due_cents=obligation.amount_due_cents,
            settled=obligation.order_id in recorded,
            outcome=recorded.get(obligation.order_id),
        )
        for obligation in obligations
    ]


async def _stop_proof_view(
    session: AsyncSession, stop_id: uuid.UUID
) -> StopProofRequirementView:
    """What proof this stop needs, for the app to ask for up front."""
    resolved = await resolve_stop_proof(session, stop_id)
    return StopProofRequirementView(
        photo_count_required=resolved.photo_count_required,
        photo_subjects=resolved.photo_subjects,
        signature_required=resolved.signature_required,
    )


async def _orders_for_recipient_notice(
    session: AsyncSession, order_ids: list[uuid.UUID]
) -> list[Order]:
    """The orders on a just-completed pickup that have a recipient to text.

    Filtered here rather than inside the notifier so a commingled pickup carrying
    five orders doesn't do five round-trips to discover four of them have no phone
    number on file (docs/ROADMAP.md F3).
    """
    if not order_ids:
        return []
    result = await session.execute(
        select(Order).where(
            Order.id.in_(order_ids), Order.delivery_contact_phone.is_not(None)
        )
    )
    return list(result.scalars().all())


def _assert_stop_not_terminal(stop: Stop, action: str) -> None:
    if stop.status in _TERMINAL_STOP_STATUSES:
        raise HTTPException(status_code=409, detail=f"Stop is {stop.status}, cannot {action}")


async def _pay_out_gig_delivery(
    session: AsyncSession, *, driver: AuthedDriver, driver_row: Driver, stop: Stop, order_ids: list[uuid.UUID]
) -> None:
    """Real per-delivery instant payout for a gig-classified driver
    (docs/ROADMAP.md A11). GigPayout.stop_id is unique, so this can never
    double-pay the same stop even on a hypothetical future retry path -
    complete_stop's own idempotent early-return already keeps a retried
    request from reaching this far, but this check holds regardless."""
    existing = await session.execute(select(GigPayout).where(GigPayout.stop_id == stop.id))
    if existing.scalar_one_or_none() is not None:
        return

    orders_result = await session.execute(select(Order).where(Order.id.in_(order_ids)))
    orders = list(orders_result.scalars().all())
    shop_ids = [o.shop_id for o in orders if o.shop_id is not None]
    shops_by_id: dict[uuid.UUID, Shop] = {}
    if shop_ids:
        shops_result = await session.execute(select(Shop).where(Shop.id.in_(shop_ids)))
        shops_by_id = {s.id: s for s in shops_result.scalars().all()}

    amount_cents = 0
    for order in orders:
        shop = shops_by_id.get(order.shop_id)
        if shop is None or order.delivery_lat is None or order.delivery_lng is None:
            continue
        amount_cents += estimate_delivery_pay_cents(
            pickup_lat=shop.lat,
            pickup_lng=shop.lng,
            dropoff_lat=float(order.delivery_lat),
            dropoff_lng=float(order.delivery_lng),
            sla_tier=order.sla_tier,
        )
    if amount_cents <= 0:
        return

    payout = GigPayout(
        hub_id=uuid.UUID(driver.hub_id),
        driver_id=uuid.UUID(driver.driver_id),
        stop_id=stop.id,
        amount_cents=amount_cents,
        status="pending",
    )
    session.add(payout)
    await session.flush()

    if not driver_row.stripe_connect_account_id:
        # Self-serve onboarding (docs/ROADMAP.md A11) doesn't exist yet -
        # same "field exists, no real linking flow" status as
        # Driver.payment_bank_last4. Record the payout as owed, not paid,
        # rather than silently dropping a real dollar amount.
        payout.status = "skipped_no_payout_account"
        await session.commit()
        return

    transfer_id = await get_payout_provider().pay_out(
        connected_account_id=driver_row.stripe_connect_account_id,
        amount_cents=amount_cents,
        description=f"LMX delivery payout - stop {stop.id}",
    )
    payout.status = "paid" if transfer_id else "stub"
    payout.stripe_transfer_id = transfer_id
    await session.commit()


@router.post("/stops/{stop_id}/arrive", response_model=StopView)
async def arrive_at_stop(
    stop_id: str, driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> StopView:
    stop = await _get_owned_stop(session, stop_id, driver)
    _assert_stop_not_terminal(stop, "mark arrived")
    stop.status = "arrived"
    # Ground-truth capture (docs/ROADMAP.md I1): stamp the first arrival only,
    # so a re-marked arrival can't overwrite the real one.
    if stop.arrived_at is None:
        stop.arrived_at = datetime.now(timezone.utc)
    # An arrival is the strongest signal there is about where this route actually is, so
    # the remaining stops are re-estimated from it. This stop's own ETA is left alone -
    # it is finished as a prediction, and `planned_eta` preserves what it was.
    await refresh_route_etas(session, stop.route_id)
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
    _assert_arrived(stop, "scanning parcels")
    stop.scanned_count = max(0, min(body.scanned_count, stop.parcel_count))
    await session.commit()
    return await _stop_view_after_reload(session, stop)


async def _stop_order_ids(session: AsyncSession, stop_id: uuid.UUID) -> list[uuid.UUID]:
    result = await session.execute(select(StopOrder.order_id).where(StopOrder.stop_id == stop_id))
    return [row[0] for row in result.all()]


@router.post("/stops/{stop_id}/scan-parcel", response_model=StopView)
async def scan_parcel(
    stop_id: str,
    body: ScanParcelBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> StopView:
    """Scan one real barcode at a pickup and verify it against the stop's
    order(s) (docs/ROADMAP.md W10) - the check that catches WRONG_PART in
    the warehouse instead of at the customer's door. The manual /scan
    (count-only) endpoint above stays as the can't-scan fallback.

    for_update serializes the derived scanned_count against concurrent
    scans; the parcel's own scanned_at is the source of truth, so a
    re-scan of the same barcode is an idempotent no-op, not a double count."""
    stop = await _get_owned_stop(session, stop_id, driver, for_update=True)
    _assert_stop_not_terminal(stop, "scan a parcel")
    _assert_arrived(stop, "scanning parcels")
    if stop.stop_type != "pickup":
        raise HTTPException(status_code=409, detail="Parcels are scanned at the pickup stop")

    order_ids = await _stop_order_ids(session, stop.id)
    parcel_result = await session.execute(
        select(Parcel).where(
            Parcel.hub_id == uuid.UUID(driver.hub_id), Parcel.barcode == body.barcode
        )
    )
    parcel = parcel_result.scalar_one_or_none()
    if parcel is None or parcel.order_id not in order_ids:
        # Unknown barcode, or a barcode for an order that isn't on this
        # pickup - either way, not a parcel this driver should be loading.
        raise HTTPException(
            status_code=422,
            detail="This barcode isn't for an order on this pickup - possible wrong part; do not load it",
        )

    if parcel.scanned_at is None:
        parcel.scanned_at = datetime.now(timezone.utc)

    scanned_result = await session.execute(
        select(func.count())
        .select_from(Parcel)
        .where(Parcel.order_id.in_(order_ids), Parcel.scanned_at.is_not(None))
    )
    stop.scanned_count = int(scanned_result.scalar_one())
    await session.commit()
    return await _stop_view_after_reload(session, stop)


@router.get("/stops/{stop_id}/parcels", response_model=list[ParcelView])
async def list_stop_parcels(
    stop_id: str,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> list[ParcelView]:
    """The parcels expected at this stop and whether each has been scanned -
    what the driver app renders as "3 of 5 collected" (W10)."""
    stop = await _get_owned_stop(session, stop_id, driver)
    order_ids = await _stop_order_ids(session, stop.id)
    if not order_ids:
        return []
    result = await session.execute(
        select(Parcel).where(Parcel.order_id.in_(order_ids)).order_by(Parcel.barcode)
    )
    return [ParcelView(barcode=p.barcode, scanned=p.scanned_at is not None) for p in result.scalars().all()]


async def _expected_returns_for_stop(session: AsyncSession, stop_id: uuid.UUID) -> list[ReturnItem]:
    order_ids = await _stop_order_ids(session, stop_id)
    if not order_ids:
        return []
    result = await session.execute(
        select(ReturnItem).where(
            ReturnItem.origin_order_id.in_(order_ids), ReturnItem.status == "expected"
        )
    )
    return list(result.scalars().all())


@router.post("/stops/{stop_id}/collect-cod", response_model=StopView)
async def collect_cod(
    stop_id: str,
    body: CollectCodBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> StopView:
    """Record that the full cash-on-delivery amount was collected (docs/ROADMAP.md W2).

    **There is no amount in the request body, and that absence is the feature.** The
    figure comes off the order, so "collected" can only mean "all of it". The money is the
    distributor's invoice to their own customer; nobody at LMX has authority to discount
    it, so a field to type a smaller number into would hand a driver an authority they
    were never given - and leave them negotiating at a door on someone else's behalf,
    which is exactly what the rule exists to get them out of.

    Requires arrival, like every other at-the-door action. Idempotent: a retried tap on a
    bad connection records one collection, not two payments.
    """
    stop = await _get_owned_stop(session, stop_id, driver)
    _assert_stop_not_terminal(stop, "collect payment")
    _assert_arrived(stop, "collecting payment")
    if stop.stop_type != "dropoff":
        raise HTTPException(
            status_code=409, detail="Payment is collected at the delivery stop"
        )

    obligations = await cod_obligations(session, stop.id)
    if not obligations:
        raise HTTPException(
            status_code=409, detail="Nothing to collect - this delivery isn't cash on delivery"
        )

    for obligation in obligations:
        order = await session.get(Order, uuid.UUID(obligation.order_id))
        try:
            await record_collection(
                session,
                order=order,
                stop_id=stop.id,
                driver_id=driver.driver_id,
                method=body.method,
            )
        except CodError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    return await _stop_view_after_reload(session, stop)


@router.post("/stops/{stop_id}/cod-dispute", response_model=StopView)
async def raise_cod_dispute(
    stop_id: str,
    body: CodDisputeBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> StopView:
    """The customer won't pay. One tap, the distributor is told, the driver moves on (W2).

    **Escalates to the distributor, not to LMX ops.** The disputed sum is their invoice to
    their own customer, so they are the only party who can decide anything about it -
    waive it, insist, phone the customer. Routing it through us first would insert LMX
    into a commercial dispute we are not part of, and cost the one thing that matters:
    the distributor hearing about it while their customer is still standing there.

    Does NOT fail the stop by itself. Whether the parts go back is R5's resolution
    decision, made by someone with the full picture; this endpoint's job is to record the
    dispute, escalate it, and let the driver leave. A driver who wants to record the
    delivery as failed still uses `flag_stop_issue` with COD_DISPUTE.
    """
    stop = await _get_owned_stop(session, stop_id, driver)
    _assert_stop_not_terminal(stop, "raise a payment dispute")
    _assert_arrived(stop, "raising a payment dispute")

    obligations = await cod_obligations(session, stop.id)
    if not obligations:
        raise HTTPException(
            status_code=409, detail="This delivery isn't cash on delivery"
        )

    disputes = []
    for obligation in obligations:
        order = await session.get(Order, uuid.UUID(obligation.order_id))
        try:
            dispute = await record_dispute(
                session,
                order=order,
                stop_id=stop.id,
                driver_id=driver.driver_id,
                note=body.note,
            )
        except CodError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        disputes.append((dispute, order))

    await session.commit()

    # After the commit, so a dead SMS gateway cannot roll back the dispute: the record is
    # the thing that must survive, the message is a courtesy on top of it.
    for dispute, order in disputes:
        shop = await session.get(Shop, order.shop_id) if order.shop_id else None
        outcome = await notify_shop_of_cod_dispute(
            session,
            hub_id=uuid.UUID(driver.hub_id),
            driver_id=uuid.UUID(driver.driver_id),
            stop_id=stop.id,
            shop=shop,
            delivery_address=order.delivery_address,
            amount_cents=dispute.amount_due_cents,
            reference=order.source_order_ref or order.external_order_ref,
            note=dispute.dispute_note,
        )
        if outcome == ESCALATION_SENT:
            dispute.escalated_at = datetime.now(timezone.utc)
    await session.commit()

    return await _stop_view_after_reload(session, stop)


@router.post("/stops/{stop_id}/collect-return", response_model=list[ReturnItemView])
async def collect_return(
    stop_id: str,
    body: CollectReturnBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> list[ReturnItemView]:
    """Record a core/return collected on the delivery visit (docs/ROADMAP.md
    W1, piggyback). Confirms whatever was expected on this dropoff's order(s);
    if nothing was expected, a `manifest` records an ad-hoc core the driver
    found on the spot."""
    stop = await _get_owned_stop(session, stop_id, driver)
    if stop.stop_type != "dropoff":
        raise HTTPException(status_code=409, detail="Returns are collected at the delivery (dropoff) stop")
    _assert_arrived(stop, "collecting a return")

    now = datetime.now(timezone.utc)
    expected = await _expected_returns_for_stop(session, stop.id)
    if expected:
        for item in expected:
            item.status = "collected"
            item.collected_at = now
        collected = expected
    elif body.manifest:
        order_ids = await _stop_order_ids(session, stop.id)
        if not order_ids:
            raise HTTPException(status_code=409, detail="This stop has no order to attach a return to")
        order = await session.get(Order, order_ids[0])
        adhoc = ReturnItem(
            hub_id=order.hub_id, origin_order_id=order.id, shop_id=order.shop_id,
            manifest=body.manifest, status="collected", collected_at=now,
        )
        session.add(adhoc)
        collected = [adhoc]
    else:
        raise HTTPException(
            status_code=409,
            detail="No return expected on this stop - include a manifest to record an ad-hoc core",
        )
    await session.commit()
    return await return_views(session, collected)


@router.post("/stops/{stop_id}/return-not-ready", response_model=list[ReturnItemView])
async def return_not_ready(
    stop_id: str,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> list[ReturnItemView]:
    """The expected core wasn't available to collect (docs/ROADMAP.md W1) -
    mark it not_ready so it drops off this delivery and into the reschedule
    workflow (a later slice) rather than silently staying 'expected'."""
    stop = await _get_owned_stop(session, stop_id, driver)
    if stop.stop_type != "dropoff":
        raise HTTPException(status_code=409, detail="Returns are handled at the delivery (dropoff) stop")
    expected = await _expected_returns_for_stop(session, stop.id)
    if not expected:
        raise HTTPException(status_code=409, detail="No expected return on this stop to mark not-ready")
    for item in expected:
        item.status = "not_ready"
    await session.commit()
    return await return_views(session, expected)


@router.post("/stops/{stop_id}/return-to-shop", response_model=list[ReturnItemView])
async def return_cores_to_shop(
    stop_id: str,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> list[ReturnItemView]:
    """Close the reverse loop (docs/ROADMAP.md W1 slice 3): the driver drops
    the cores they collected earlier back at their destination shop, while
    at that shop for a (forward) pickup. Marks every `collected` return
    bound for this shop as returned_to_shop.

    v1 scopes by destination shop, not by which driver is physically
    holding which core (ReturnItem carries no driver_id) - fine while one
    driver covers a shop; revisit if cores routinely change hands mid-transit."""
    stop = await _get_owned_stop(session, stop_id, driver)
    if stop.stop_type != "pickup" or stop.shop_id is None:
        raise HTTPException(status_code=409, detail="Cores are returned at a shop pickup stop")

    result = await session.execute(
        select(ReturnItem).where(ReturnItem.shop_id == stop.shop_id, ReturnItem.status == "collected")
    )
    items = list(result.scalars().all())
    if not items:
        raise HTTPException(status_code=409, detail="No collected cores are destined for this shop")

    now = datetime.now(timezone.utc)
    for item in items:
        item.status = "returned_to_shop"
        item.returned_at = now
    await session.commit()
    return await return_views(session, items)


_CONTENT_TYPE_EXTENSION = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@router.post("/stops/{stop_id}/upload-url", response_model=UploadUrlResult)
async def create_upload_url(
    stop_id: str,
    body: UploadUrlRequestBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> UploadUrlResult:
    """Requested just before capturing a parcel-scan barcode image or a
    proof-of-delivery photo/signature (docs/ROADMAP.md A2/A3) - the driver
    app uploads directly to the returned upload_url (never proxied through
    this backend), then submits final_url as CompleteStopBody's
    photo_url/signature_url. Ownership-checked the same way every other
    stop-scoped endpoint is, even though nothing is written to the stop
    here - a driver has no reason to mint upload URLs for a stop that
    isn't theirs."""
    await _get_owned_stop(session, stop_id, driver)

    key = generate_object_key(
        driver.driver_id, stop_id, body.kind, _CONTENT_TYPE_EXTENSION[body.content_type]
    )
    upload = get_photo_upload_client().create_upload(key, body.content_type)
    return UploadUrlResult(
        upload_url=upload.upload_url, final_url=upload.final_url, requires_upload=upload.requires_upload
    )


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
    _assert_arrived(stop, "completing it")
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

    if body.method == "pin":
        # Real verification (docs/ROADMAP.md A4) - stop.delivery_pin is
        # the actual PIN texted to the customer at accept_offer time
        # (app/messaging/delivery_pin.py), not just recorded and trusted.
        if stop.delivery_pin is None:
            raise HTTPException(
                status_code=409, detail="No PIN was issued for this stop - use photo or signature instead"
            )
        if stop.pin_verification_attempts >= MAX_PIN_VERIFICATION_ATTEMPTS:
            raise HTTPException(
                status_code=409, detail="Too many incorrect PIN attempts - use photo or signature instead"
            )
        if body.pin != stop.delivery_pin:
            stop.pin_verification_attempts += 1
            await session.commit()
            raise HTTPException(status_code=400, detail="Incorrect PIN")

    # **What the order actually requires, enforced at last** (docs/ROADMAP.md LMX
    # Link; app/delivery/proof.py). `orders.proof_requirements` has been written at
    # ingestion since L3 and read by nothing, so the object advertised configurable
    # proof while this endpoint enforced a constant - and the constant was "none":
    # `method="photo"` with a null photo_url completed the stop. Proof of delivery
    # proved nothing.
    # **Money first.** Before this, a driver could mark a COD delivery done with no
    # record of any cash changing hands - parts gone, invoice unpaid, no dispute raised to
    # explain it, and nothing anywhere noticing (docs/ROADMAP.md W2). A dispute counts as
    # settled for the purpose of leaving, because the rule is "keep moving".
    if stop.stop_type == "dropoff":
        try:
            await assert_cod_settled(session, stop.id)
        except CodNotSettled as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    required = await resolve_stop_proof(session, stop.id)
    try:
        assert_proof_satisfied(
            required,
            method=body.method,
            photo_urls=body.all_photo_urls,
            signature_url=body.signature_url,
            # A verified PIN satisfies a signature requirement - both answer "the
            # right person received this", and the PIN is the stronger of the two
            # because it is checked against what we issued.
            pin_verified=body.method == "pin",
        )
    except ProofNotSatisfied as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    stop.status = "completed"
    stop.completed_at = now
    stop.pod_method = body.method
    stop.pod_photo_url = body.photo_url
    stop.pod_signature_url = body.signature_url
    # Every photo captured, not just the first. A stop that required four and stored
    # one would leave us unable to produce the evidence we just insisted on.
    stop.pod_photo_urls = body.all_photo_urls
    stop.pod_pin = body.pin
    stop.pod_left_at = body.left_at

    # Every stop's orders, regardless of type - the dropoff branch below
    # needs them for the Order.status update, and the vehicle-load
    # adjustment further down needs them for both stop types.
    order_ids_result = await session.execute(select(StopOrder.order_id).where(StopOrder.stop_id == stop.id))
    order_ids = [row[0] for row in order_ids_result.all()]

    # Order status now follows the stop (LMX_LINK_PLAN.md §1.4). This used to
    # update only on a dropoff, on the reasoning that stop status captured
    # collection more precisely than Order.status could - true, but it left a
    # client watching their order sitting on "assigned" for an hour with no way
    # to tell whether their parts had been collected. That is the gap the
    # stop-level states close.
    if order_ids:
        await advance_orders(
            session,
            order_ids,
            OrderStatus.delivered if stop.stop_type == "dropoff" else OrderStatus.picked_up,
            # `now` is this stop's completed_at, set above - the real moment,
            # which for a dropoff is also delivered_at's ground truth (I1).
            occurred_at=now,
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

    # The strongest re-estimation point on a route: the driver is leaving a known place
    # at a known time, so every stop after this one is re-walked from here. A route that
    # ran twenty minutes long stops claiming its original arrival times.
    if not route_finished:
        await refresh_route_etas(session, stop.route_id)

    await session.commit()

    # Real per-delivery instant payout for gig-classified drivers
    # (docs/ROADMAP.md A11) - best-effort, same "commit the delivery
    # first, pay/notify after" pattern as the shop-SMS/PIN-SMS sends
    # elsewhere in this file: a payout failure must never roll back or
    # block a delivery the driver already completed.
    if stop.stop_type == "dropoff" and order_ids:
        driver_row = await _get_driver_row(session, driver)
        if driver_row.employment_type == "gig":
            await _pay_out_gig_delivery(session, driver=driver, driver_row=driver_row, stop=stop, order_ids=order_ids)

    # Real vehicle-load tracking - a pickup's weight enters the vehicle the
    # moment it's completed here, a dropoff's the moment it leaves. This is
    # what app/optimizer/service.py's live-route-push capacity check reads
    # (DriverState.load_units was never incremented anywhere before this).
    # Defensive `if state:` - skip silently if this driver has no fleet
    # state yet, same pattern the route-finished block below already uses.
    if order_ids:
        weight_result = await session.execute(select(Order.weight_units).where(Order.id.in_(order_ids)))
        total_weight = float(sum(row[0] for row in weight_result.all()))
        if total_weight > 0:
            fleet_state_manager = FleetStateManager()
            state = await fleet_state_manager.get_driver_state(driver.hub_id, driver.driver_id)
            if state:
                if stop.stop_type == "pickup":
                    state.load_units = state.load_units + total_weight
                else:
                    state.load_units = max(0.0, state.load_units - total_weight)
                await fleet_state_manager.upsert_driver_state(state)

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

        # Text each recipient their live tracking link (docs/ROADMAP.md F3).
        # Pickup is the trigger because it is the first moment the link is worth
        # opening - there is now a van with their parts on it. Same best-effort
        # placement as the shop SMS above: after the completion commit, so a
        # failed send can never unwind a delivery the driver already made.
        for order_row in await _orders_for_recipient_notice(session, order_ids):
            await notify_recipient_picked_up(
                session,
                hub_id=uuid.UUID(driver.hub_id),
                driver_id=uuid.UUID(driver.driver_id),
                stop_id=stop.id,
                order=order_row,
            )
        await session.commit()

    if not route_finished:
        # Completing a stop promotes the next one, and that is the moment the driver
        # starts driving to it - the signal L11 needed. On a dropoff this is what
        # finally advances its orders to `en_route_drop`, a state that existed in the
        # machine and was never reached.
        await mark_current_stop_en_route(session, stop.route_id)
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
        # delivery_failed is §1.4's EXCEPTION_RAISED - the same state under the
        # name this codebase already used, rather than a duplicate value.
        await advance_orders(session, order_ids, OrderStatus.delivery_failed)
        await session.execute(
            update(Order).where(Order.id.in_(order_ids)).values(failure_reason=body.reason.value)
        )

    remaining_result = await session.execute(
        select(func.count())
        .select_from(Stop)
        .where(Stop.route_id == stop.route_id, Stop.status.notin_(_TERMINAL_STOP_STATUSES))
    )
    if remaining_result.scalar_one() == 0:
        route = await session.get(Route, stop.route_id)
        route.status = "completed"
    else:
        # A flagged stop is finished too, so the next one is promoted and the driver is
        # on their way to it (docs/ROADMAP.md L11) - the same signal as a completion.
        await mark_current_stop_en_route(session, stop.route_id)
        # And it moves the remaining ETAs for the same reason a completion does. A stop
        # that failed still consumed the drive to it and the time spent at the door.
        await refresh_route_etas(session, stop.route_id)

    await session.commit()

    # Vehicle-load tracking, mirroring complete_stop's: a flagged *dropoff*
    # still means that weight leaves the vehicle (whatever the resolution
    # turns out to be, it's no longer this driver's responsibility) - a
    # flagged *pickup* needs no adjustment, since nothing was ever loaded
    # for a stop whose pickup never completed.
    if stop.stop_type == "dropoff" and order_ids:
        weight_result = await session.execute(select(Order.weight_units).where(Order.id.in_(order_ids)))
        total_weight = float(sum(row[0] for row in weight_result.all()))
        if total_weight > 0:
            fleet_state_manager = FleetStateManager()
            state = await fleet_state_manager.get_driver_state(driver.hub_id, driver.driver_id)
            if state:
                state.load_units = max(0.0, state.load_units - total_weight)
                await fleet_state_manager.upsert_driver_state(state)

    # Tell each affected shop their customer's delivery failed (R5) - a
    # failed *dropoff*, unlike a pickup, is a delivery the shop's customer
    # never received. One SMS per distinct shop. Shop-facing and best-effort;
    # ops still gets the event-bus signal below regardless.
    if stop.stop_type == "dropoff" and order_ids:
        shop_result = await session.execute(
            select(Shop).join(Order, Order.shop_id == Shop.id).where(Order.id.in_(order_ids))
        )
        for shop in {s.id: s for s in shop_result.scalars().all()}.values():
            await notify_shop_delivery_failed(
                session,
                hub_id=uuid.UUID(driver.hub_id),
                driver_id=uuid.UUID(driver.driver_id),
                stop_id=stop.id,
                shop=shop,
            )
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
        select(Message)
        .where(Message.stop_id == stop.id, Message.channel == "customer")
        .order_by(Message.created_at)
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
# Masked voice calling (docs/ROADMAP.md A7) - app/messaging/voice_client.py.
# "Masked" here means two real phone calls bridged by Twilio, not in-app
# audio: this endpoint places a call to the *driver's* own phone, and once
# they answer, app/api/webhooks.py's voice_connect tells Twilio to <Dial>
# the customer with LMX's shared number as caller ID. The customer's real
# number never reaches the driver app (see CallView, which omits it).
# ---------------------------------------------------------------------------


def _call_view(call: Call) -> CallView:
    return CallView(
        call_id=str(call.id),
        status=call.status,
        created_at=call.created_at,
        duration_seconds=call.duration_seconds,
    )


@router.post("/stops/{stop_id}/call", response_model=CallView)
async def call_customer(
    stop_id: str,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> CallView:
    stop = await _get_owned_stop(session, stop_id, driver)
    if stop.stop_type != "dropoff":
        raise HTTPException(status_code=409, detail="Only a dropoff stop has a customer to call")

    order_id_result = await session.execute(select(StopOrder.order_id).where(StopOrder.stop_id == stop.id))
    order_id = order_id_result.scalar_one_or_none()
    order = await session.get(Order, order_id) if order_id else None
    if order is None or not order.delivery_contact_phone:
        raise HTTPException(status_code=409, detail="No customer contact number on file for this stop")

    driver_row = await _get_driver_row(session, driver)

    call = Call(
        hub_id=uuid.UUID(driver.hub_id),
        driver_id=uuid.UUID(driver.driver_id),
        stop_id=stop.id,
        counterparty_phone=order.delivery_contact_phone,
        status="initiated",
    )
    session.add(call)
    await session.flush()

    # Twilio needs a publicly-reachable URL to call back into for both the
    # connect-TwiML and the status callback - same setting webhooks.py's
    # inbound-signature check uses for the reverse direction (see
    # settings.twilio_webhook_base_url's docstring). Unset (today's
    # un-proxied docker-compose dev stack) still lets the stub client run
    # end-to-end since it never actually dials out.
    base_url = (settings.twilio_webhook_base_url or "").rstrip("/")
    call.twilio_call_sid = await get_voice_client().place_masked_call(
        driver_phone=driver_row.phone,
        connect_url=f"{base_url}/webhooks/twilio/voice-connect/{call.id}",
        status_callback_url=f"{base_url}/webhooks/twilio/voice-status/{call.id}",
    )
    await session.commit()
    return _call_view(call)


# ---------------------------------------------------------------------------
# Earnings + trip history (screens 1n/1o) - see EarningsView/TripSummaryView
# docstrings (app/schemas/driver_app.py) for why this is explicitly labeled
# an estimate rather than a real payroll figure. Hours/overtime math lives
# in app/payroll/hours.py, shared with the admin payroll-run endpoint
# (app/api/admin_routes.py) so the two never drift on what "hours worked"
# means.
# ---------------------------------------------------------------------------


def _route_hours(route: Route) -> float:
    # Proxy for one *trip's* duration (screen 1o's trip history, a
    # different, lower-stakes claim than "total hours worked this pay
    # period" below) - route.created_at (job accepted) to route.updated_at
    # (last touched, which for a completed route is when its last stop
    # finished - see complete_stop above).
    return max((route.updated_at - route.created_at).total_seconds() / 3600, 0.0)


@router.get("/me/earnings", response_model=EarningsView)
async def get_my_earnings(
    driver: AuthedDriver = Depends(get_current_driver), session: AsyncSession = Depends(get_db)
) -> EarningsView:
    row = await _get_driver_row(session, driver)
    rate_cents = row.hourly_rate_cents or payroll_hours.PLACEHOLDER_HOURLY_RATE_CENTS
    # Gig isn't paid hourly at all (docs/ROADMAP.md A11) - "placeholder"
    # specifically means "an hourly rate stood in for a real one," which
    # doesn't apply when there's no hourly rate in the pay formula.
    is_placeholder = row.hourly_rate_cents is None and row.employment_type != "gig"

    now = datetime.now(timezone.utc)
    start, end = payroll_hours.pay_period_bounds(row.employment_type, now)
    clipped_end = min(end, now)

    regular_hours, overtime_hours, estimated_pay_cents = await payroll_hours.hours_and_pay_for_period(
        session,
        driver_id=driver.driver_id,
        hub_id=str(row.hub_id),
        employment_type=row.employment_type,
        rate_cents=rate_cents,
        start=start,
        end=clipped_end,
    )

    return EarningsView(
        period_start=start.date(),
        period_end=(end - timedelta(days=1)).date(),
        hours_worked=round(regular_hours + overtime_hours, 2),
        overtime_hours=round(overtime_hours, 2),
        hourly_rate_cents=0 if row.employment_type == "gig" else rate_cents,
        estimated_pay_cents=estimated_pay_cents,
        is_placeholder=is_placeholder,
        employment_type=row.employment_type,
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
