"""
Internal/admin-only endpoints. Not client-facing, not driver-facing - gated
by the real per-account ops auth (app/ops_auth/, docs/ROADMAP.md S1), same
as the rest of app/api/routes.py's ops tooling. No new auth scheme needed
here since whoever calls this is LMX ops, not a client or a driver.

Phase 8 (docs/ROADMAP.md): a minimal client onboarding endpoint. There's no
admin UI yet to onboard a client's shops/rates/portal login as separate
steps, so this does all of it in one request - see
app/schemas/admin.py's ClientOnboardingBody docstring.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.payroll.hours as payroll_hours
from app.billing.service import NoBillableOrdersError, generate_invoice, invoice_detail_view
from app.client_auth.passwords import hash_password
from app.db import get_db
from app.driver_auth.dependencies import revoked_devices_key
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.driver import Driver
from app.models.driver_device import DriverDevice
from app.models.shop import Shop
from app.ops_auth.dependencies import AuthedOpsUser, require_admin
from app.payroll import get_payroll_provider
from app.redis_client import get_client as get_redis_client
from app.schemas.admin import (
    ClientOnboardingBody,
    ClientOnboardingResult,
    DriverPayrollSubmission,
    PayrollRunResult,
)
from app.schemas.billing import InvoiceDetailView, InvoiceGenerateBody

router = APIRouter(prefix="/admin", tags=["admin"])

# Kept in sync by hand with app.models.order.SLATier's string values - not
# imported directly since ClientRate.sla_tier is deliberately a plain
# string, decoupled from that enum (see app/models/client_rate.py's
# docstring on why a future tier shouldn't need an enum migration first).
# This endpoint still validates against the tiers that exist *today* so a
# typo'd tier name doesn't silently create a rate nothing will ever match.
VALID_SLA_TIERS = {"HOT_SHOT", "T1", "T2", "T3"}


@router.post("/clients", response_model=ClientOnboardingResult)
async def onboard_client(
    body: ClientOnboardingBody, session: AsyncSession = Depends(get_db), _admin: AuthedOpsUser = Depends(require_admin)
) -> ClientOnboardingResult:
    existing = await session.execute(select(Client.id).where(Client.portal_email == body.portal_email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A client already uses this portal email")

    bad_tiers = [r.sla_tier for r in body.rates if r.sla_tier not in VALID_SLA_TIERS]
    if bad_tiers:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sla_tier(s) in rates: {bad_tiers}. Valid tiers: {sorted(VALID_SLA_TIERS)}",
        )
    if not body.shops:
        raise HTTPException(status_code=422, detail="At least one shop is required to onboard a client")

    client = Client(
        hub_id=uuid.UUID(body.hub_id),
        name=body.name,
        pos_system=body.pos_system,
        portal_email=body.portal_email,
        portal_password_hash=hash_password(body.portal_password),
    )
    session.add(client)
    await session.flush()  # need client.id for shops/rates below

    shop_ids: list[uuid.UUID] = []
    for shop_input in body.shops:
        shop = Shop(
            client_id=client.id,
            name=shop_input.name,
            address=shop_input.address,
            lat=shop_input.lat,
            lng=shop_input.lng,
            external_ref=shop_input.external_ref,
            phone=shop_input.phone,
        )
        session.add(shop)
        await session.flush()
        shop_ids.append(shop.id)

    for rate_input in body.rates:
        session.add(
            ClientRate(
                client_id=client.id,
                sla_tier=rate_input.sla_tier,
                rate_per_drop_cents=rate_input.rate_per_drop_cents,
            )
        )

    await session.commit()
    return ClientOnboardingResult(client_id=str(client.id), shop_ids=[str(sid) for sid in shop_ids])


@router.delete("/drivers/{driver_id}/devices/{device_id}", status_code=204)
async def admin_revoke_driver_device(
    driver_id: str,
    device_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> None:
    """
    The "driver calls dispatch, ops revokes on their behalf" path - same
    effect as the driver-facing DELETE /driver/me/devices/{device_id}, for
    when the driver themselves can't (lost phone, no app access).
    """
    result = await session.execute(
        select(DriverDevice).where(
            DriverDevice.driver_id == uuid.UUID(driver_id), DriverDevice.device_id == device_id
        )
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    device.revoked_at = datetime.now(timezone.utc)
    await session.commit()
    await get_redis_client().sadd(revoked_devices_key(driver_id), device_id)


@router.post("/payroll/{hub_id}/run", response_model=PayrollRunResult)
async def run_payroll_for_hub(
    hub_id: str, session: AsyncSession = Depends(get_db), _admin: AuthedOpsUser = Depends(require_admin)
) -> PayrollRunResult:
    """
    Manually submit every driver-in-this-hub's most recently *completed*
    pay period (w2 monthly, 1099/gig weekly - see app/payroll/hours.py) to
    the configured PayrollProvider (app/payroll/, Rippling once
    credentialed, StubPayrollProvider until then). Same "manual trigger
    today, a real scheduler's hook later" pattern as
    run_learning_loop_nightly_job (app/api/routes.py) - no scheduler
    exists yet, and running this twice for the same period is safe to
    retry (each call recomputes from the shift-event log and resubmits;
    whether the payroll provider itself dedupes a repeat submission is
    between it and whoever runs this).
    """
    drivers_result = await session.execute(select(Driver).where(Driver.hub_id == uuid.UUID(hub_id)))
    drivers = list(drivers_result.scalars().all())

    provider = get_payroll_provider()
    now = datetime.now(timezone.utc)
    submissions: list[DriverPayrollSubmission] = []

    for driver in drivers:
        start, end = payroll_hours.previous_pay_period_bounds(driver.employment_type, now)
        rate_cents = driver.hourly_rate_cents or payroll_hours.PLACEHOLDER_HOURLY_RATE_CENTS
        regular_hours, overtime_hours, estimated_pay_cents = await payroll_hours.hours_and_pay_for_period(
            session,
            driver_id=str(driver.id),
            hub_id=str(driver.hub_id),
            employment_type=driver.employment_type,
            rate_cents=rate_cents,
            start=start,
            end=end,
        )
        if regular_hours == 0.0 and overtime_hours == 0.0:
            continue  # nothing to submit for a driver who wasn't on duty at all last period

        period_end_inclusive = (end - timedelta(days=1)).date()
        reference = await provider.submit_hours(
            driver_id=str(driver.id),
            driver_name=driver.name,
            period_start=start.date(),
            period_end=period_end_inclusive,
            hours_worked=round(regular_hours + overtime_hours, 2),
            rate_cents=rate_cents,
        )
        submissions.append(
            DriverPayrollSubmission(
                driver_id=str(driver.id),
                driver_name=driver.name,
                employment_type=driver.employment_type,
                period_start=start.date().isoformat(),
                period_end=period_end_inclusive.isoformat(),
                hours_worked=round(regular_hours, 2),
                overtime_hours=round(overtime_hours, 2),
                estimated_pay_cents=estimated_pay_cents,
                provider_reference=reference,
            )
        )

    return PayrollRunResult(hub_id=hub_id, engine=provider.engine_name, submissions=submissions)


@router.post("/clients/{client_id}/invoices/generate", response_model=InvoiceDetailView)
async def generate_client_invoice(
    client_id: str,
    body: InvoiceGenerateBody,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> InvoiceDetailView:
    """
    Sweeps this client's delivered, priced, not-yet-billed orders in
    [period_start, period_end) into a new statement (docs/ROADMAP.md C3,
    app/billing/service.py). Safe to call repeatedly for different, later
    periods - already-billed orders (Order.invoice_id set) are never
    picked up twice; running it again for a period with nothing new to
    bill 404s rather than creating an empty invoice.
    """
    client = await session.get(Client, uuid.UUID(client_id))
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    try:
        invoice = await generate_invoice(session, uuid.UUID(client_id), body.period_start, body.period_end)
    except NoBillableOrdersError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return await invoice_detail_view(session, invoice)
