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
from datetime import date, datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.payroll.hours as payroll_hours
from app.batch_queue.store import HoldQueueStore
from app.billing.service import NoBillableOrdersError, generate_invoice, invoice_detail_view
from app.client_auth.passwords import hash_password
from app.db import get_db
from app.delivery.resolution import RESOLUTION_ACTIONS, OrderNotFailedError, resolve_failed_order
from app.driver_auth.dependencies import revoked_devices_key
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.client_sla_term import ClientSlaTerm
from app.models.client_user import CLIENT_ADMIN_ROLE, ClientUser
from app.models.driver import Driver
from app.compliance.driver_documents import evaluate_driver_documents
from app.reporting.cod_disputes import build_cod_dispute_report
from app.models.driver_device import DriverDevice
from app.models.driver_document import (
    REVIEW_PENDING,
    REVIEW_REJECTED,
    REVIEW_VERIFIED,
    DriverDocument,
)
from app.models.hub import Hub
from app.models.hub_closure import HubClosure
from app.learning_loop.promotion import (
    PENDING,
    ProposedRuleNotPendingError,
    dismiss_proposed_rule,
    promote_proposed_rule,
)
from app.models.order import Order
from app.models.return_item import ReturnItem
from app.models.rules import ActiveRule, ProposedRule
from app.models.shop import Shop
from app.ops_auth.dependencies import AuthedOpsUser, require_admin
from app.payroll import get_payroll_provider
from app.redis_client import get_client as get_redis_client
from app.schemas.admin import (
    ClientOnboardingBody,
    ClientOnboardingResult,
    ClientRateBody,
    ClientRateView,
    ClientSlaTermBody,
    ClientSlaTermView,
    CodDisputeReportView,
    DriverDocumentReviewBody,
    DriverDocumentReviewResult,
    DriverPayrollSubmission,
    HubClosureBody,
    PendingDriverDocumentView,
    ShopDisputeRowView,
    HubClosureView,
    OrderResolutionResult,
    PayrollRunResult,
    ProposedRuleApprovalResult,
    ProposedRuleView,
    ResolveFailedOrderBody,
    UrgencyRuleBody,
    UrgencyRuleUpdateBody,
    UrgencyRuleView,
)
from app.schemas.billing import InvoiceDetailView, InvoiceGenerateBody
from app.gig_platform import service as gig_store
from app.messaging.client_emails import send_signup_approved_email
from app.gig_platform.density import hub_density_report
from app.returns.service import AWAITING_STATUSES, return_views
from app.schemas.gig import GigDensityReport, GigJobView
from app.schemas.signup import (
    ApproveSignupBody,
    PendingSignupView,
    RejectSignupBody,
    SignupDecisionResult,
)
from app.schemas.returns import ReturnItemView

logger = structlog.get_logger(__name__)

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
    existing = await session.execute(select(ClientUser.id).where(ClientUser.email == body.portal_email))
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
    )
    session.add(client)
    await session.flush()  # need client.id for the shops/rates/first-user below

    # The client's first portal login is created as an admin (multi-user,
    # docs/ROADMAP.md C4) - it's the account that can then invite the rest
    # of the client's users itself, without ops involvement per new user.
    session.add(
        ClientUser(
            client_id=client.id,
            email=body.portal_email,
            password_hash=hash_password(body.portal_password),
            name=body.portal_user_name or body.name,
            role=CLIENT_ADMIN_ROLE,
            is_active=True,
        )
    )

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
                # The first version, effective immediately (T2.5 A1).
                effective_from=datetime.now(timezone.utc),
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
        if driver.employment_type == "gig":
            # Already paid instantly, per delivery, at complete_stop time
            # (docs/ROADMAP.md A11, app/models/gig_payout.py) - a payroll-
            # cycle submission here would be a second, bogus hours x rate
            # payment for pay that already went out through Stripe Connect,
            # not the hourly Rippling rail this endpoint submits to.
            continue

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


@router.post("/orders/{order_id}/resolve", response_model=OrderResolutionResult)
async def resolve_order(
    order_id: str,
    body: ResolveFailedOrderBody,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> OrderResolutionResult:
    """
    The defined next step for a delivery_failed order (docs/ROADMAP.md R5,
    app/delivery/resolution.py): `redeliver` reattempts it (re-enters the
    dispatch pipeline, bumping delivery_attempts), `return_to_shop` sends
    the parts back, `cancel` closes it out. 409 if the order isn't actually
    failed - only a failure can be resolved.
    """
    if body.action not in RESOLUTION_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown action {body.action!r}. Valid actions: {sorted(RESOLUTION_ACTIONS)}",
        )
    order = await session.get(Order, uuid.UUID(order_id))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    try:
        resolved = await resolve_failed_order(session, HoldQueueStore(), order, body.action)
    except OrderNotFailedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return OrderResolutionResult(
        order_id=str(resolved.id),
        status=resolved.status.value,
        delivery_attempts=resolved.delivery_attempts,
        action=body.action,
    )


def _closure_view(closure: HubClosure) -> HubClosureView:
    return HubClosureView(
        closure_date=closure.closure_date,
        reason=closure.reason,
        created_at=closure.created_at.isoformat(),
    )


@router.post("/hubs/{hub_id}/closures", response_model=HubClosureView, status_code=201)
async def add_hub_closure(
    hub_id: str,
    body: HubClosureBody,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> HubClosureView:
    """Mark a local calendar day the hub isn't operating (docs/ROADMAP.md
    R6) - the optimizer then skips dispatch and the nightly job skips that
    day. 409 if the day is already marked closed."""
    hub = await session.get(Hub, uuid.UUID(hub_id))
    if hub is None:
        raise HTTPException(status_code=404, detail="Hub not found")

    closure = HubClosure(hub_id=hub.id, closure_date=body.closure_date, reason=body.reason)
    session.add(closure)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail=f"{body.closure_date.isoformat()} is already marked closed for this hub"
        ) from exc
    return _closure_view(closure)


@router.get("/hubs/{hub_id}/closures", response_model=list[HubClosureView])
async def list_hub_closures(
    hub_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> list[HubClosureView]:
    result = await session.execute(
        select(HubClosure)
        .where(HubClosure.hub_id == uuid.UUID(hub_id))
        .order_by(HubClosure.closure_date)
    )
    return [_closure_view(c) for c in result.scalars().all()]


@router.delete("/hubs/{hub_id}/closures/{closure_date}", status_code=204)
async def remove_hub_closure(
    hub_id: str,
    closure_date: date,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> None:
    """Remove a closure - e.g. a planned shutdown that got cancelled."""
    result = await session.execute(
        select(HubClosure).where(
            HubClosure.hub_id == uuid.UUID(hub_id),
            HubClosure.closure_date == closure_date,
        )
    )
    closure = result.scalar_one_or_none()
    if closure is None:
        raise HTTPException(status_code=404, detail="No closure on that date for this hub")
    await session.delete(closure)
    await session.commit()


# ---------------------------------------------------------------------------
# Orchestrator-editable urgency rules (docs/ROADMAP.md W6) - direct human
# authoring of part-type -> tier rules ("body panels are never urgent"),
# stored as active_rules(rule_type='tier_override') and applied at ingestion
# (app/ingestion/service.py's _load_tier_overrides, app/sla/engine.py). Ops
# can add/disable a rule without a code deploy; distinct from the Learning
# Loop's machine-proposed rules.
# ---------------------------------------------------------------------------
def _urgency_rule_view(rule: ActiveRule) -> UrgencyRuleView:
    return UrgencyRuleView(
        rule_id=str(rule.id),
        match_key=rule.value.get("match_key", ""),
        match_value=rule.value.get("match_value", ""),
        tier=rule.value.get("tier", ""),
        enabled=rule.enabled,
    )


async def _get_owned_urgency_rule(session: AsyncSession, hub_id: str, rule_id: str) -> ActiveRule:
    rule = await session.get(ActiveRule, uuid.UUID(rule_id))
    if rule is None or str(rule.hub_id) != hub_id or rule.rule_type != "tier_override":
        raise HTTPException(status_code=404, detail="Urgency rule not found")
    return rule


@router.post("/hubs/{hub_id}/urgency-rules", response_model=UrgencyRuleView, status_code=201)
async def add_urgency_rule(
    hub_id: str,
    body: UrgencyRuleBody,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> UrgencyRuleView:
    if body.tier not in VALID_SLA_TIERS:
        raise HTTPException(
            status_code=422, detail=f"Unknown tier {body.tier!r}. Valid tiers: {sorted(VALID_SLA_TIERS)}"
        )
    hub = await session.get(Hub, uuid.UUID(hub_id))
    if hub is None:
        raise HTTPException(status_code=404, detail="Hub not found")

    rule = ActiveRule(
        hub_id=hub.id,
        rule_type="tier_override",
        scope={},
        value={"match_key": body.match_key, "match_value": body.match_value, "tier": body.tier},
        enabled=True,
    )
    session.add(rule)
    await session.commit()
    return _urgency_rule_view(rule)


@router.get("/hubs/{hub_id}/urgency-rules", response_model=list[UrgencyRuleView])
async def list_urgency_rules(
    hub_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> list[UrgencyRuleView]:
    result = await session.execute(
        select(ActiveRule)
        .where(ActiveRule.hub_id == uuid.UUID(hub_id), ActiveRule.rule_type == "tier_override")
        .order_by(ActiveRule.created_at)
    )
    return [_urgency_rule_view(r) for r in result.scalars().all()]


@router.patch("/hubs/{hub_id}/urgency-rules/{rule_id}", response_model=UrgencyRuleView)
async def update_urgency_rule(
    hub_id: str,
    rule_id: str,
    body: UrgencyRuleUpdateBody,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> UrgencyRuleView:
    """Enable/disable a rule without deleting it - a disabled rule stops
    affecting classification (ingestion only loads enabled ones) but stays
    on file to re-enable."""
    rule = await _get_owned_urgency_rule(session, hub_id, rule_id)
    rule.enabled = body.enabled
    await session.commit()
    return _urgency_rule_view(rule)


@router.delete("/hubs/{hub_id}/urgency-rules/{rule_id}", status_code=204)
async def remove_urgency_rule(
    hub_id: str,
    rule_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> None:
    rule = await _get_owned_urgency_rule(session, hub_id, rule_id)
    await session.delete(rule)
    await session.commit()


# ---------------------------------------------------------------------------
# Learning-Loop rule review & promotion (docs/ROADMAP.md I2) - the human
# approval step that turns nightly ProposedRule rows into ActiveRule rows
# the SLA engine / ingestion actually read. Completes component 6's loop.
# ---------------------------------------------------------------------------
def _proposed_rule_view(rule: ProposedRule) -> ProposedRuleView:
    return ProposedRuleView(
        rule_id=str(rule.id),
        rule_type=rule.rule_type,
        scope=rule.scope,
        proposed_change=rule.proposed_change,
        confidence=float(rule.confidence),
        supporting_annotation_count=rule.supporting_annotation_count,
        status=rule.status,
        created_at=rule.created_at.isoformat(),
    )


@router.get("/hubs/{hub_id}/proposed-rules", response_model=list[ProposedRuleView])
async def list_proposed_rules(
    hub_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> list[ProposedRuleView]:
    """Proposals still awaiting review for this hub, oldest first - the
    review queue the dashboard card renders."""
    result = await session.execute(
        select(ProposedRule)
        .where(ProposedRule.hub_id == uuid.UUID(hub_id), ProposedRule.status == PENDING)
        .order_by(ProposedRule.created_at)
    )
    return [_proposed_rule_view(r) for r in result.scalars().all()]


async def _get_pending_proposed_rule(session: AsyncSession, rule_id: str) -> ProposedRule:
    rule = await session.get(ProposedRule, uuid.UUID(rule_id))
    if rule is None:
        raise HTTPException(status_code=404, detail="Proposed rule not found")
    return rule


@router.post("/proposed-rules/{rule_id}/approve", response_model=ProposedRuleApprovalResult)
async def approve_proposed_rule(
    rule_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> ProposedRuleApprovalResult:
    """Promote a proposal into active_rules, where it starts affecting
    dispatch. 409 if it isn't still pending (already decided)."""
    rule = await _get_pending_proposed_rule(session, rule_id)
    try:
        active = await promote_proposed_rule(session, rule)
    except ProposedRuleNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ProposedRuleApprovalResult(
        proposed_rule_id=str(rule.id), status=rule.status, active_rule_id=str(active.id)
    )


@router.post("/proposed-rules/{rule_id}/dismiss", response_model=ProposedRuleApprovalResult)
async def dismiss_proposed_rule_endpoint(
    rule_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> ProposedRuleApprovalResult:
    """Reject a proposal - it stays on file as rejected, never promoted."""
    rule = await _get_pending_proposed_rule(session, rule_id)
    try:
        await dismiss_proposed_rule(session, rule)
    except ProposedRuleNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ProposedRuleApprovalResult(proposed_rule_id=str(rule.id), status=rule.status)


@router.get("/hubs/{hub_id}/returns", response_model=list[ReturnItemView])
async def list_returns(
    hub_id: str,
    status: str | None = None,
    awaiting: bool = False,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> list[ReturnItemView]:
    """Returns/cores for this hub (docs/ROADMAP.md W1), optionally filtered
    by status (expected | ready_for_pickup | collected | returned_to_shop |
    not_ready | cancelled) - the ops view over the reverse leg. Pass
    `awaiting=true` for the counter-facing 'awaiting pickup, with age' cut
    (everything still waiting on a pickup), oldest first so the stalest sits
    on top; each row carries `age_hours`."""
    query = select(ReturnItem).where(ReturnItem.hub_id == uuid.UUID(hub_id))
    if awaiting:
        query = query.where(ReturnItem.status.in_(AWAITING_STATUSES))
    if status is not None:
        query = query.where(ReturnItem.status == status)
    query = query.order_by(ReturnItem.created_at)
    rows = (await session.execute(query)).scalars().all()
    return await return_views(session, list(rows))


@router.post("/returns/{return_id}/mark-returned", response_model=ReturnItemView)
async def mark_return_returned(
    return_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> ReturnItemView:
    """Manually mark a return as delivered back (docs/ROADMAP.md W1 slice 3) -
    an ops correction, and the path for standalone shop-flagged returns that
    reach the warehouse outside the driver return-to-shop flow. 409 if it's
    already terminal (returned_to_shop / cancelled)."""
    item = await session.get(ReturnItem, uuid.UUID(return_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Return not found")
    if item.status in ("returned_to_shop", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Return is already '{item.status}'")
    item.status = "returned_to_shop"
    item.returned_at = datetime.now(timezone.utc)
    await session.commit()
    return (await return_views(session, [item]))[0]


@router.post("/returns/{return_id}/reschedule", response_model=ReturnItemView)
async def reschedule_return(
    return_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> ReturnItemView:
    """Requeue a core that wasn't ready at the delivery visit (docs/ROADMAP.md
    W1 slice 4). `not_ready` -> `ready_for_pickup`: it drops off the piggyback
    path and becomes a standalone pickup to schedule. 409 unless it's
    currently `not_ready` - there's nothing to reschedule otherwise."""
    item = await session.get(ReturnItem, uuid.UUID(return_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Return not found")
    if item.status != "not_ready":
        raise HTTPException(
            status_code=409, detail=f"Only a 'not_ready' return can be rescheduled; this is '{item.status}'"
        )
    item.status = "ready_for_pickup"
    await session.commit()
    return (await return_views(session, [item]))[0]


@router.get("/hubs/{hub_id}/gig-jobs", response_model=list[GigJobView])
async def list_gig_jobs(
    hub_id: str,
    status: str | None = None,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> list[GigJobView]:
    """Gig-platform jobs for this hub (docs/ROADMAP.md G3), newest offer
    first, optionally filtered by status (offered | accepted | picked_up |
    delivered | declined | cancelled).

    Distinct from every other list on this router: these are not orders. No
    client, no SLA tier we assigned, no rate-table fee - the platform set the
    windows and the pay, and we cannot hold one for a cluster-mate.

    This is the read the density instrumentation (G12) will build on: offers
    per day, jobs per driver per day, and the share delivered as part of a
    multi-job sequence. Those numbers are what turn "when does batching
    become possible" from an argument into a measurement - the pilot ran at
    roughly 1.8 jobs/driver/day, and pairing plausibly needs 10-15 drivers.
    """
    jobs = await gig_store.list_for_hub(
        session, hub_id, statuses=(status,) if status else None
    )
    return [gig_store.gig_job_view(job) for job in jobs]


@router.get("/hubs/{hub_id}/gig-density", response_model=GigDensityReport)
async def get_gig_density(
    hub_id: str,
    days: int = 14,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> GigDensityReport:
    """Volume and pairing figures for the gig path (docs/ROADMAP.md G12).

    The number to watch is `sequenced_share` - the fraction of delivered
    jobs the driver was holding concurrently with another. It is the
    difference between "we are sequencing work" and "we are doing jobs one
    at a time, faster." The roadmap expects it to stay near zero until
    roughly 10-15 drivers, and this endpoint exists so that expectation gets
    confirmed or overturned by data rather than argued.

    `offers_per_day` is separately the trigger for revisiting automated
    intake (G1/G2), which was deferred as a 30-driver problem.
    """
    return await hub_density_report(session, hub_id, days=days)


# ---------------------------------------------------------------------------
# Public-signup review queue (docs/LMX_LINK_PLAN.md)
#
# The gate that keeps a self-serve signup form compatible with LMX being a B2B
# operator rather than self-serve SaaS. Anyone can apply; nobody dispatches an
# LMX van until someone here approves them.
# ---------------------------------------------------------------------------


@router.get("/signups", response_model=list[PendingSignupView])
async def list_signups(
    status: str = "pending",
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> list[PendingSignupView]:
    """Applicants awaiting review, oldest first so nobody is left behind a
    newer one. Pass `status` to see approved or rejected history instead."""
    result = await session.execute(
        select(Client).where(Client.signup_status == status).order_by(Client.created_at)
    )
    clients = list(result.scalars().all())
    if not clients:
        return []

    # The applicant's own details live on their first admin user. Batched rather
    # than queried per client so the queue is one round trip.
    users_result = await session.execute(
        select(ClientUser)
        .where(ClientUser.client_id.in_([c.id for c in clients]))
        .order_by(ClientUser.created_at)
    )
    first_user: dict[uuid.UUID, ClientUser] = {}
    for user in users_result.scalars():
        first_user.setdefault(user.client_id, user)

    return [
        PendingSignupView(
            client_id=str(c.id),
            company_name=c.name,
            service_area=c.service_area,
            contact_name=first_user[c.id].name if c.id in first_user else None,
            contact_email=first_user[c.id].email if c.id in first_user else None,
            contact_phone=c.contact_phone,
            terms_version=c.terms_accepted_version,
            terms_accepted_at=c.terms_accepted_at,
            signup_status=c.signup_status,
            submitted_at=c.created_at,
            hub_id=str(c.hub_id),
        )
        for c in clients
    ]


@router.post("/signups/{client_id}/approve", response_model=SignupDecisionResult)
async def approve_signup(
    client_id: str,
    body: ApproveSignupBody,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> SignupDecisionResult:
    """Approve an applicant, set their rates, and let them in.

    **Rates are required here, and that is the design.** Approval is the only
    moment where somebody is already looking at this client and deciding
    commercial terms, so it is the natural place to set them - and doing it here
    means an active client always has rates. That removes the whole class of
    problem where a self-signed-up client submits orders that price as null
    (see Order.fee_cents, which insists null must never look like a free
    delivery). A client we approved but cannot bill is worse than one still
    waiting.

    Activating the client and its first user happens last, after the rates are
    in the session, so a failure part-way cannot leave someone able to order
    with no rates configured.
    """
    client = await session.get(Client, uuid.UUID(client_id))
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if client.signup_status == "active":
        # Idempotent rather than an error: two ops users clicking approve on the
        # same queue is an ordinary race, not a mistake worth surfacing.
        return SignupDecisionResult(client_id=client_id, signup_status="active", rates_created=0)
    if client.signup_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Only a pending signup can be approved; this one is '{client.signup_status}'",
        )

    bad_tiers = [r.sla_tier for r in body.rates if r.sla_tier not in VALID_SLA_TIERS]
    if bad_tiers:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sla_tier(s): {bad_tiers}. Valid tiers: {sorted(VALID_SLA_TIERS)}",
        )

    if body.hub_id is not None:
        hub = await session.get(Hub, uuid.UUID(body.hub_id))
        if hub is None:
            raise HTTPException(status_code=404, detail="Hub not found")
        client.hub_id = hub.id

    for rate in body.rates:
        session.add(
            ClientRate(
                client_id=client.id,
                sla_tier=rate.sla_tier,
                rate_per_drop_cents=rate.rate_per_drop_cents,
                # The first version, effective immediately (T2.5 A1).
                effective_from=datetime.now(timezone.utc),
            )
        )

    client.signup_status = "active"
    # The start of §3.4's headline metric ("customer says yes" -> first delivery).
    # Written here because this is the moment, and nothing else records it.
    client.approved_at = datetime.now(timezone.utc)

    # Their first login becomes usable. C4 re-checks is_active every request, so
    # this is the single switch that turns a pending applicant into a client who
    # can sign in and order.
    users = await session.execute(select(ClientUser).where(ClientUser.client_id == client.id))
    activated = list(users.scalars())
    for user in activated:
        user.is_active = True

    await session.commit()

    # Tell them, after the commit. Best-effort by design: an approval must stand
    # even when mail is down, because blocking onboarding on a mail outage is
    # worse than a client who has to be phoned. A failed send logs loudly and
    # the panel still shows them active, which is what lets someone notice.
    #
    # Sent to the first user - the one created by their own signup - rather than
    # to everyone, since ops can add colleagues later and they don't each need
    # an approval notice.
    if activated:
        first = min(activated, key=lambda u: u.created_at)
        await send_signup_approved_email(
            to=first.email, contact_name=first.name, company_name=client.name
        )

    return SignupDecisionResult(
        client_id=client_id, signup_status="active", rates_created=len(body.rates)
    )


@router.post("/signups/{client_id}/reject", response_model=SignupDecisionResult)
async def reject_signup(
    client_id: str,
    body: RejectSignupBody,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> SignupDecisionResult:
    """Decline an applicant.

    The row stays rather than being deleted, so a company that applies again is
    recognisable and ops isn't re-deciding from nothing. Their user stays
    inactive, so nothing can log in.
    """
    client = await session.get(Client, uuid.UUID(client_id))
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if client.signup_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Only a pending signup can be rejected; this one is '{client.signup_status}'",
        )

    client.signup_status = "rejected"
    # Starts the twelve-month retention clock the privacy policy commits to
    # (app/legal/retention.py). Without it the row is kept forever, because the sweep
    # refuses to guess a date it was never given.
    client.rejected_at = datetime.now(timezone.utc)
    # Deliberately not surfaced to the applicant - it is a note for whoever sees
    # them apply a second time.
    logger.info("signup_rejected", client_id=client_id, reason=body.reason)
    await session.commit()
    return SignupDecisionResult(client_id=client_id, signup_status="rejected")


# ---------------------------------------------------------------------------
# Driver compliance document review (docs/ROADMAP.md R4)
# ---------------------------------------------------------------------------
#
# The human step that makes the availability gate mean something. Before this
# existed, a driver set their own document expiry and the gate read it back to
# them - so "documents on file, none expired" was a claim the driver had made about
# themselves, dressed as a check the system had performed.


@router.get("/drivers/documents/pending", response_model=list[PendingDriverDocumentView])
async def list_pending_driver_documents(
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> list[PendingDriverDocumentView]:
    """Documents uploaded and waiting on a verdict.

    Not hub-scoped, unlike most of this router: compliance review is a
    whole-company function, and a driver whose license is unreviewed cannot work at
    any hub. Oldest first, because the driver who has waited longest is the one
    being kept off the road by us rather than by their paperwork.

    Only rows with a `file_url` - a document nobody has uploaded anything against
    has nothing to review, and listing it would put items in the queue that a
    reviewer can only skip.
    """
    result = await session.execute(
        select(DriverDocument, Driver.name)
        .join(Driver, Driver.id == DriverDocument.driver_id)
        .where(
            DriverDocument.review_status == REVIEW_PENDING,
            DriverDocument.file_url.is_not(None),
        )
        .order_by(DriverDocument.updated_at)
    )
    return [
        PendingDriverDocumentView(
            document_id=str(doc.id),
            driver_id=str(doc.driver_id),
            driver_name=driver_name,
            doc_type=doc.doc_type,
            claimed_expires_at=doc.claimed_expires_at,
            file_url=doc.file_url,
            review_status=doc.review_status,
            uploaded_at=doc.updated_at,
        )
        for doc, driver_name in result.all()
    ]


@router.post(
    "/drivers/documents/{document_id}/review", response_model=DriverDocumentReviewResult
)
async def review_driver_document(
    document_id: str,
    body: DriverDocumentReviewBody,
    session: AsyncSession = Depends(get_db),
    admin: AuthedOpsUser = Depends(require_admin),
) -> DriverDocumentReviewResult:
    """Record that a human read this document and what it said.

    **Approving REQUIRES an expiry date the reviewer read off the document.** It
    deliberately does not default to the driver's claimed date: a verdict that
    copied the claim would move the self-attestation problem one step later rather
    than fixing it, and the resulting row would look verified while carrying a
    number nobody had checked.

    Attributed to the reviewing ops user. An unattributed compliance decision is
    barely better than none - if a driver turns out to have been cleared on a bad
    document, "who cleared it" has to have an answer.

    Idempotent-unfriendly on purpose: re-reviewing an already-decided document is a
    409. A second verdict on the same evidence is either a mistake or a disagreement
    that should be resolved by the driver re-uploading, not by silently overwriting
    the first reviewer.
    """
    try:
        doc = await session.get(DriverDocument, uuid.UUID(document_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Document not found") from None
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.review_status != REVIEW_PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"This document was already {doc.review_status} - the driver must re-upload to reopen it",
        )
    if doc.file_url is None:
        raise HTTPException(
            status_code=409, detail="Nothing has been uploaded against this document yet"
        )

    now = datetime.now(timezone.utc)
    if body.decision == "verify":
        if body.verified_expires_at is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "verified_expires_at is required - it must be the date on the "
                    "document, not the date the driver claimed"
                ),
            )
        doc.review_status = REVIEW_VERIFIED
        doc.verified_expires_at = body.verified_expires_at
        doc.rejection_reason = None
    else:
        if not body.rejection_reason:
            raise HTTPException(
                status_code=422,
                detail="rejection_reason is required - a driver can't fix a rejection they can't read",
            )
        doc.review_status = REVIEW_REJECTED
        doc.verified_expires_at = None
        doc.rejection_reason = body.rejection_reason

    doc.reviewed_at = now
    doc.reviewed_by_ops_user_id = uuid.UUID(admin.ops_user_id)
    await session.commit()

    # Recomputed rather than inferred: clearing the second of two documents is what
    # actually unblocks a driver, and a reviewer working a queue should see that
    # happen instead of having to go and check.
    compliance = await evaluate_driver_documents(session, str(doc.driver_id))

    logger.info(
        "driver_document_reviewed",
        document_id=str(doc.id),
        driver_id=str(doc.driver_id),
        doc_type=doc.doc_type,
        decision=body.decision,
        reviewed_by=admin.ops_user_id,
        driver_can_go_on_shift=compliance.can_go_on_shift,
    )

    return DriverDocumentReviewResult(
        document_id=str(doc.id),
        doc_type=doc.doc_type,
        review_status=doc.review_status,
        verified_expires_at=doc.verified_expires_at,
        driver_can_go_on_shift=compliance.can_go_on_shift,
        outstanding_problems=[problem.detail for problem in compliance.problems],
    )


@router.get("/hubs/{hub_id}/cod-disputes", response_model=CodDisputeReportView)
async def cod_dispute_report(
    hub_id: str,
    window_days: int = 30,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> CodDisputeReportView:
    """Repeat COD disputes per account (docs/ROADMAP.md W2).

    **A single dispute is a bad afternoon; the same account disputing every month is a
    commercial problem** - and it is invisible unless somebody counts. Grouped by shop
    rather than by client, because a distributor can have forty branches and "your account
    has a dispute problem" is not actionable where "the Riverside branch does" is.

    Feeds the monthly owner report the roadmap asks for. `window_days` defaults to 30 to
    match that cadence.
    """
    report = await build_cod_dispute_report(session, hub_id=hub_id, window_days=window_days)
    return CodDisputeReportView(
        window_start=report.window_start,
        window_end=report.window_end,
        disputed_count=report.disputed_count,
        collected_count=report.collected_count,
        disputed_amount_cents=report.disputed_amount_cents,
        unescalated_count=report.unescalated_count,
        sms_configured=report.sms_configured,
        shops=[
            ShopDisputeRowView(
                shop_id=row.shop_id,
                shop_name=row.shop_name,
                client_id=row.client_id,
                client_name=row.client_name,
                disputed_count=row.disputed_count,
                collected_count=row.collected_count,
                disputed_amount_cents=row.disputed_amount_cents,
                dispute_rate=round(row.dispute_rate, 4),
            )
            for row in report.shops
        ],
    )


# ---------------------------------------------------------------------------
# Rate tables and SLA terms (docs/ROADMAP.md F5, W3)
# ---------------------------------------------------------------------------
#
# Both are CONTRACT data - what a client agreed to pay and what we agreed to deliver. They
# live together because a credit is a percentage of a fee, so the two cannot sensibly be
# maintained apart.


@router.get("/clients/{client_id}/rates", response_model=list[ClientRateView])
async def list_client_rates(
    client_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> list[ClientRateView]:
    # The rate *in force now*, one per tier - not every version (T2.5 A1). Since 0045 a
    # tier has a history, and returning all of it here would read as duplicate rates to
    # anyone looking at the list, including the dashboard. A future version is excluded
    # for the same reason pricing excludes it: a scheduled change is not today's rate.
    #
    # Ordered newest-first and de-duplicated in Python rather than with a window function,
    # because at pilot scale a client has a handful of tiers and a handful of versions, and
    # the obvious query is easier to be sure of than a clever one.
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(ClientRate)
        .where(
            ClientRate.client_id == uuid.UUID(client_id),
            ClientRate.effective_from <= now,
        )
        .order_by(ClientRate.sla_tier, ClientRate.effective_from.desc())
    )
    current: dict[str, ClientRate] = {}
    for rate in result.scalars().all():
        current.setdefault(rate.sla_tier, rate)

    return [
        ClientRateView(
            rate_id=str(rate.id),
            sla_tier=rate.sla_tier,
            rate_per_drop_cents=rate.rate_per_drop_cents,
            rate_per_mile_cents=rate.rate_per_mile_cents,
            rate_per_piece_cents=rate.rate_per_piece_cents,
            rate_per_weight_unit_cents=rate.rate_per_weight_unit_cents,
            minimum_charge_cents=rate.minimum_charge_cents,
        )
        for rate in sorted(current.values(), key=lambda r: r.sla_tier)
    ]


@router.put("/clients/{client_id}/rates", response_model=ClientRateView)
async def upsert_client_rate(
    client_id: str,
    body: ClientRateBody,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> ClientRateView:
    """Set one tier's rate, as a new version (docs/ROADMAP.md F5, T2.5 A1).

    **Changing a rate does not reprice anything already taken.** `fee_cents` and
    `fee_breakdown` are frozen on the order at ingestion, so a card edited mid-month
    affects the next order and not the last hundred - which is what keeps a quote a quote.
    Worth knowing when a client asks why today's edit didn't change this month's statement.

    **This inserts rather than overwrites, since migration 0045.** It used to UPDATE the
    single row for the pair, which left the orders correct and destroyed the card's own
    history: after an edit, nothing could say what the rate had been the week before, or
    which version priced a given drop. Both are the audit trail `H1` asks for. Every edit
    is now a new row, and the previous version stays exactly as it applied.

    Still a PUT, and still idempotent in the sense that matters - the current rate for a
    tier is whatever this endpoint was last called with. What changed is that the earlier
    answers survive.
    """
    effective_from = datetime.now(timezone.utc)

    # Guard the one case the unique constraint would otherwise reject with a 500: two
    # edits to the same tier inside the same clock tick. Treated as a correction to the
    # version just written rather than a second version, because two rates for one tier
    # starting at the same instant is a contradiction, not history.
    existing = (
        await session.execute(
            select(ClientRate).where(
                ClientRate.client_id == uuid.UUID(client_id),
                ClientRate.sla_tier == body.sla_tier,
                ClientRate.effective_from == effective_from,
            )
        )
    ).scalar_one_or_none()

    rate = existing or ClientRate(
        client_id=uuid.UUID(client_id),
        sla_tier=body.sla_tier,
        effective_from=effective_from,
    )
    if existing is None:
        session.add(rate)

    rate.rate_per_drop_cents = body.rate_per_drop_cents
    rate.rate_per_mile_cents = body.rate_per_mile_cents
    rate.rate_per_piece_cents = body.rate_per_piece_cents
    rate.rate_per_weight_unit_cents = body.rate_per_weight_unit_cents
    rate.minimum_charge_cents = body.minimum_charge_cents
    await session.commit()

    logger.info(
        "client_rate_version_created",
        client_id=client_id,
        sla_tier=body.sla_tier,
        rate_id=str(rate.id),
        effective_from=effective_from.isoformat(),
    )
    return ClientRateView(rate_id=str(rate.id), **body.model_dump())


@router.get("/clients/{client_id}/sla-terms", response_model=list[ClientSlaTermView])
async def list_client_sla_terms(
    client_id: str,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> list[ClientSlaTermView]:
    result = await session.execute(
        select(ClientSlaTerm)
        .where(ClientSlaTerm.client_id == uuid.UUID(client_id))
        .order_by(ClientSlaTerm.sla_tier)
    )
    return [
        ClientSlaTermView(
            term_id=str(term.id),
            sla_tier=term.sla_tier,
            delivery_target_minutes=term.delivery_target_minutes,
            credit_percent=term.credit_percent,
            credit_minimum_cents=term.credit_minimum_cents,
            credit_maximum_cents=term.credit_maximum_cents,
        )
        for term in result.scalars().all()
    ]


@router.put("/clients/{client_id}/sla-terms", response_model=ClientSlaTermView)
async def upsert_client_sla_term(
    client_id: str,
    body: ClientSlaTermBody,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> ClientSlaTermView:
    """Record what we promised this client for this tier, and what missing it costs (W3).

    **Contract data, not a constant.** LMX has no company-wide delivery SLA written down
    anywhere - `app/sla/engine.py` only defines hold windows - so hardcoding a target would
    have invented our service level in a Python file. Each row is what somebody actually
    agreed to, per client and per tier, because two distributors on T1 may still have
    signed different papers.

    A tier with no term is not credited, and the assessment reports that separately rather
    than treating it as nothing owed.
    """
    if (
        body.credit_minimum_cents is not None
        and body.credit_maximum_cents is not None
        and body.credit_minimum_cents > body.credit_maximum_cents
    ):
        raise HTTPException(
            status_code=422,
            detail="The credit minimum can't be more than the maximum",
        )

    result = await session.execute(
        select(ClientSlaTerm).where(
            ClientSlaTerm.client_id == uuid.UUID(client_id),
            ClientSlaTerm.sla_tier == body.sla_tier,
        )
    )
    term = result.scalar_one_or_none()
    if term is None:
        term = ClientSlaTerm(client_id=uuid.UUID(client_id), sla_tier=body.sla_tier)
        session.add(term)

    term.delivery_target_minutes = body.delivery_target_minutes
    term.credit_percent = body.credit_percent
    term.credit_minimum_cents = body.credit_minimum_cents
    term.credit_maximum_cents = body.credit_maximum_cents
    await session.commit()

    logger.info(
        "client_sla_term_set",
        client_id=client_id,
        sla_tier=body.sla_tier,
        target_minutes=body.delivery_target_minutes,
        credit_percent=body.credit_percent,
    )
    return ClientSlaTermView(term_id=str(term.id), **body.model_dump())
