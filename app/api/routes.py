"""
Health/ops endpoints + manual trigger endpoints for the Dispatch Optimizer
and the Learning Loop's nightly job.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.clustering import cluster_members
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.db import get_db
from app.fleet_state.manager import FleetStateManager
from app import metrics
from app.learning_loop.service import run_nightly_job
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order
from app.ops_auth.dependencies import (
    AuthedOpsUser,
    get_current_ops_user,
    require_admin,
)
from app.optimizer.event_trigger import dispatch_event_bus
from app.optimizer.last_cycle_store import LastCycleStore
from app.optimizer.service import DispatchOptimizerService
from app.reporting.lmx_link import build_scorecard
from app.reporting.credit_exposure import DEFAULT_WINDOW_DAYS as CREDIT_WINDOW_DAYS
from app.reporting.credit_exposure import build_credit_exposure
from app.models.dispatcher_override import REASON_CODES, REASON_CODES_REQUIRING_NOTE, REASON_LABELS
from app.models.linkage_flag import LinkageFlag
from app.models.location import Location
from app.models.receiver_profile import ReceiverProfile
from app.models.shop import Shop
from app.models.location_merge import LocationMerge
from app.identity.merge import confirm_merge, pending_merges, reject_merge, revert_merge
from app.identity.node_class import classification_coverage, set_node_class
from app.record.consequences import (
    CONSEQUENCE_LABELS,
    CONSEQUENCES,
    late_orders_awaiting_judgement,
    record_consequence,
)
from app.record.explain import explain_order
from app.record.linkage import open_flags, resolve_flag
from app.record.overrides import OverrideRefused, apply_override
from app.reporting.exceptions import build_exception_queue
from app.reporting.record_health import build_record_health
from app.reporting.operations import DEFAULT_WINDOW_DAYS, build_operations_scorecard
from app.schemas.batch_queue import HeldOrderView
from app.schemas.reporting import (
    ClientExposureView,
    CreditExposureView,
    DecisionFactView,
    ExceptionItemView,
    ExceptionQueueView,
    LinkScorecardView,
    MeasurementView,
    OperationsScorecardView,
    ConsequenceOptionView,
    ConsequenceRequest,
    LateOrderView,
    LinkageFlagView,
    OrderExplanationView,
    ClassificationCoverageView,
    MergeProposalView,
    NodeClassRequest,
    UnlabelledDockView,
    RecordHealthView,
    WriterHealthView,
    OverrideReasonOption,
    OverrideRequest,
    OverrideView,
    RateView,
    TierExposureView,
)
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.hub import HubSummary
from app.schemas.learning_loop import NightlyJobResult, ProposedRuleSummary
from app.schemas.optimizer import LastCycleSnapshot, OptimizationResult
from app.schemas.order import OrderStatusSummary

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus scrape target (docs/ROADMAP.md S4, app/metrics.py).
    Exempt from the ops-JWT gate (a scraper has no per-user login) - see
    OpsUserAuthMiddleware.EXEMPT_PATHS and app/metrics.py's auth note on
    restricting this to a private scrape network in production."""
    payload, content_type = metrics.render_latest()
    return Response(content=payload, media_type=content_type)


@router.get("/lmx-link/scorecard", response_model=LinkScorecardView)
async def lmx_link_scorecard(
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> LinkScorecardView:
    """LMX Link's success metrics, computed from real rows (LMX_LINK_PLAN §3.4).

    Those five targets have been quoted in updates since the plan was written and none
    of them was answerable. Three now are; the other two say why not, in the response,
    rather than being dropped or filled with something that looks like a number.

    Computed on request rather than exported as Prometheus counters, for the same
    reason `app/health/checks.py` evaluates server-side: per-process counters reset on
    cold start and differ per instance on an autoscaled deployment. These are
    distributions over durable rows.
    """
    scorecard = await build_scorecard(session)
    return LinkScorecardView(
        generated_at=scorecard.generated_at,
        measurements=[
            MeasurementView(
                name=m.name,
                target=m.target,
                unit=m.unit,
                median=m.median,
                p90=m.p90,
                sample_size=m.sample_size,
                not_measured=m.not_measured,
            )
            for m in scorecard.measurements
        ],
    )


@router.get("/operations/scorecard", response_model=OperationsScorecardView)
async def operations_scorecard(
    window_days: Annotated[int, Query(ge=1, le=365)] = DEFAULT_WINDOW_DAYS,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> OperationsScorecardView:
    """What the captured ground truth actually says (docs/ROADMAP.md I4).

    Four questions the records could always have answered and nobody was asking:
    deliveries per hour, service-level hit rate by tier, how often a hold window drew a
    "held wrong" flag, and ETA error. `Stop.planned_eta` was added specifically to make
    the last one possible and was read nowhere until this existed.

    **Expect `not_measured` before a pilot has run**, and read that as the correct answer
    rather than a broken endpoint. Each reason distinguishes "no data yet", which traffic
    fixes, from "nothing records this", which needs somebody to build something.

    Ops-admin only. These are fleet-wide operational figures, and the per-driver view
    that could be derived from the same rows is deliberately not built here - see
    `_deliveries_per_hour` and `W4`.
    """
    scorecard = await build_operations_scorecard(session, window_days=window_days)
    return OperationsScorecardView(
        generated_at=scorecard.generated_at,
        window_days=scorecard.window_days,
        window_start=scorecard.window_start,
        measurements=[
            MeasurementView(
                name=m.name,
                target=m.target,
                unit=m.unit,
                median=m.median,
                p90=m.p90,
                sample_size=m.sample_size,
                not_measured=m.not_measured,
            )
            for m in scorecard.measurements
        ],
        rates=[
            RateView(
                name=r.name,
                target=r.target,
                numerator=r.numerator,
                denominator=r.denominator,
                percentage=r.percentage,
                is_thin=r.is_thin,
                not_measured=r.not_measured,
            )
            for r in scorecard.rates
        ],
    )


@router.get("/operations/unlabelled-docks", response_model=list[UnlabelledDockView])
async def unlabelled_docks(
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> list[UnlabelledDockView]:
    """Docks nobody has classified (`IDN-3`).

    The rules are exhausted. Measured against the design partner's real account
    book they leave **28.7% unlabelled against a target of under 2%**, and the
    remainder is family and personal business names carrying no industry word at
    all - the export has no industry code, so there is nothing else to read. The
    row has always said the choice is *"a person labels the tail, or the target
    moves"*; this is the surface that makes the first possible.

    **Ordered by how busy the dock looks**, using the inherited dwell sample
    count as the only evidence of volume we have before delivering there
    ourselves. The tail is long and an arbitrary order gets worked from the top
    until somebody stops, so the order decides which docks get labelled.
    """
    rows = (
        await session.execute(
            select(Location, ReceiverProfile)
            .outerjoin(ReceiverProfile, ReceiverProfile.location_id == Location.id)
            .where(Location.node_class.is_(None), Location.merged_into_id.is_(None))
            .order_by(ReceiverProfile.inherited_dwell_sample_count.desc().nulls_last())
            .limit(limit)
        )
    ).all()

    views = []
    for location, profile in rows:
        names = list(
            await session.scalars(
                select(Shop.name).where(Shop.location_id == location.id).limit(5)
            )
        )
        views.append(
            UnlabelledDockView(
                location_id=location.id,
                address=location.address,
                shop_names=names,
                inherited_dwell_sample_count=(
                    profile.inherited_dwell_sample_count if profile else None
                ),
                inherited_dwell_p50_seconds=(
                    profile.inherited_dwell_p50_seconds if profile else None
                ),
            )
        )
    return views


@router.post("/operations/docks/{location_id}/node-class", response_model=UnlabelledDockView)
async def label_dock(
    location_id: uuid.UUID,
    body: NodeClassRequest,
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> UnlabelledDockView:
    """Say what kind of place a dock is (`IDN-3`).

    A human label outranks anything the rules inferred and is never overwritten
    by them. Any ops session: a dispatcher who has been to the door knows better
    than a regex over the account name, and making this admin-only would put the
    knowledge and the permission in different people.

    Not a free-text field. The seven classes are what `PRD-1` groups by, and an
    eighth appearing would split a group without anyone noticing - which is why
    `set_node_class` raises rather than storing an unknown one.
    """
    location = await session.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="No such dock")
    try:
        set_node_class(location, body.node_class)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await session.commit()

    names = list(
        await session.scalars(
            select(Shop.name).where(Shop.location_id == location.id).limit(5)
        )
    )
    return UnlabelledDockView(
        location_id=location.id, address=location.address, shop_names=names,
        inherited_dwell_sample_count=None, inherited_dwell_p50_seconds=None,
    )


@router.get("/operations/classification-coverage", response_model=ClassificationCoverageView)
async def classification_coverage_view(
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> ClassificationCoverageView:
    """How far `IDN-3` is from its done-when, measured rather than asserted.

    The denominator is shops that reach a dock, because the done-when is about
    accounts. A shop with no dock at all cannot be classified and is counted
    separately rather than dropped, which would flatter the figure.
    """
    coverage = await classification_coverage(session)
    return ClassificationCoverageView(
        shops=coverage["shops"],
        classified=coverage["classified"],
        without_dock=coverage["without_dock"],
        unlabelled=coverage["unlabelled"],
        unlabelled_percent=round(coverage["unlabelled_pct"], 1),
        meets_target=coverage["meets_target"],
    )


@router.get("/operations/merge-proposals", response_model=list[MergeProposalView])
async def merge_proposals(
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> list[MergeProposalView]:
    """Dock pairs waiting for somebody to say whether they are one place (`IDN-2`).

    *"Human-confirms the founding ~230, auto-merge after, every merge audited
    and reversible."* The queue had no producer and no reader, so it was
    permanently empty and the founding set could not be confirmed
    (`docs/ROADMAP_AUDIT_2026-09.md`). `propose_duplicate_locations` now fills it
    once a night; this is where it is worked.

    Not hub-scoped, because the queue is not: the same physical dock can be
    reached from two hubs, and that pair is the most valuable merge to catch.
    """
    proposals = await pending_merges(session)
    return [await _merge_view(session, proposal) for proposal in proposals]


async def _merge_view(session: AsyncSession, proposal) -> MergeProposalView:
    """Both addresses, resolved. "Are these the same place" cannot be answered
    from two UUIDs, and a reviewer who has to look each one up will not."""
    source = await session.get(Location, proposal.source_location_id)
    target = await session.get(Location, proposal.target_location_id)
    return MergeProposalView(
        id=proposal.id,
        status=proposal.status,
        reason=proposal.reason,
        source_location_id=proposal.source_location_id,
        source_address=source.address if source else "(dock no longer exists)",
        target_location_id=proposal.target_location_id,
        target_address=target.address if target else "(dock no longer exists)",
        proposed_at=proposal.created_at,
        decided_at=proposal.decided_at,
        decision_source=proposal.decision_source,
    )


@router.post("/operations/merge-proposals/{proposal_id}/confirm", response_model=MergeProposalView)
async def confirm_merge_proposal(
    proposal_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    admin: AuthedOpsUser = Depends(require_admin),
) -> MergeProposalView:
    """These two docks are one place. Applies the merge (`IDN-2`).

    **Admin, unlike the queue itself.** Confirming rewrites which dock a shop
    points at, and every per-dock statistic - dwell, node class, the receiver
    profile - moves with it. That is the shape `require_admin`'s docstring
    describes: a mutating action a viewer should not reach. Reading the queue is
    open to anyone, because a dispatcher spotting a duplicate is how good
    proposals get noticed.

    Reversible, and the audit row records who decided and on what evidence.
    """
    proposal = await session.get(LocationMerge, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="No such merge proposal")
    try:
        await confirm_merge(session, proposal, ops_user_id=uuid.UUID(admin.ops_user_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await session.commit()
    return await _merge_view(session, proposal)


@router.post("/operations/merge-proposals/{proposal_id}/reject", response_model=MergeProposalView)
async def reject_merge_proposal(
    proposal_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    admin: AuthedOpsUser = Depends(require_admin),
) -> MergeProposalView:
    """These are different places. Recorded, not deleted (`IDN-2`).

    The rejection is the useful artefact: it stops the pair being re-proposed
    every time the detector runs, and it is the evidence that somebody looked.
    A queue that re-asks a question already answered becomes noise that gets
    cleared without being read.
    """
    proposal = await session.get(LocationMerge, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="No such merge proposal")
    try:
        await reject_merge(session, proposal, ops_user_id=uuid.UUID(admin.ops_user_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await session.commit()
    return await _merge_view(session, proposal)


@router.post("/operations/merges/{merge_id}/revert", response_model=MergeProposalView)
async def revert_applied_merge(
    merge_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    admin: AuthedOpsUser = Depends(require_admin),
) -> MergeProposalView:
    """Undo an applied merge (`IDN-2`).

    *"Every merge audited and reversible"* is a clause of the done-when, and it
    was reversible in code with nothing able to call it. Puts back exactly the
    shops the merge moved - shops that already pointed at the target were never
    moved and are not touched.
    """
    merge = await session.get(LocationMerge, merge_id)
    if merge is None:
        raise HTTPException(status_code=404, detail="No such merge")
    try:
        await revert_merge(session, merge, ops_user_id=uuid.UUID(admin.ops_user_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await session.commit()
    return await _merge_view(session, merge)


@router.get("/operations/record-health", response_model=RecordHealthView)
async def record_health(
    hub_id: uuid.UUID,
    window_days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> RecordHealthView:
    """Is the record being written, and how far is the label set from usable?

    `REC-1`..`REC-4` all have writers now, and every one of them was wired in
    the last few changes. **A writer that silently stops looks exactly like a
    quiet week** - this is the thing that would notice, which is why it reports
    each writer's last-written timestamp rather than a health boolean.

    Computed from the ledger, never recomputed from `orders`. A reader that fell
    back to recomputing would keep showing a healthy number after the ledger
    stopped being written, which is the one failure it exists to catch.
    """
    health = await build_record_health(session, hub_id=hub_id, window_days=window_days)
    link = health.decision_link_rate
    return RecordHealthView(
        window_days=health.window_days,
        on_time_percentage=health.on_time.percentage,
        on_time_numerator=health.on_time.numerator,
        on_time_denominator=health.on_time.denominator,
        on_time_interval=health.on_time_interval,
        on_time_not_measured=health.on_time.not_measured,
        on_time_is_thin=health.on_time.is_thin,
        decisions_recorded=health.decisions_recorded,
        outcomes_recorded=health.outcomes_recorded,
        outcomes_linked_to_a_decision=health.outcomes_linked_to_a_decision,
        decision_link_percentage=link.percentage,
        open_flags=health.open_flags,
        dwell_docks=health.dwell.docks,
        dwell_from_our_own=health.dwell.from_our_own,
        dwell_inherited=health.dwell.inherited,
        dwell_unknown=health.dwell.unknown,
        dwell_thin=health.dwell.thin,
        writers=[
            WriterHealthView(
                name=w.name,
                rows_in_window=w.rows_in_window,
                last_written_at=w.last_written_at,
                note=w.note,
            )
            for w in health.writers
        ],
        labels=health.labels,
    )


@router.get("/operations/late-orders", response_model=list[LateOrderView])
async def late_orders(
    hub_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> list[LateOrderView]:
    """Late deliveries whose window has closed and that nobody has judged (`REC-2`).

    The worklist the consequence label depends on. Without it the only way to
    record what happened after a late delivery is to already know which ones
    were late - which nobody does fourteen days later, which is why the label
    set was empty.

    "Late" comes from `REC-3`'s delivered outcome rather than being recomputed,
    so a delivery stays judged by the terms that applied to it even after the
    client's SLA terms change.
    """
    orders = await late_orders_awaiting_judgement(session, hub_id=hub_id)
    return [
        LateOrderView(
            order_id=order.id,
            external_ref=order.external_order_ref,
            client_id=order.client_id,
            delivered_at=order.delivered_at,
            minutes_late=(
                int((order.delivered_at - order.promised_at).total_seconds() // 60)
                if order.delivered_at and order.promised_at
                else None
            ),
            sla_tier=order.sla_tier,
        )
        for order in orders
    ]


@router.get("/operations/consequence-kinds", response_model=list[ConsequenceOptionView])
async def consequence_kinds(
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> list[ConsequenceOptionView]:
    """The six the brief names, served rather than duplicated in the console."""
    return [
        ConsequenceOptionView(code=code, label=CONSEQUENCE_LABELS[code])
        for code in CONSEQUENCES
    ]


@router.post("/orders/{order_id}/consequence", response_model=dict)
async def record_order_consequence(
    order_id: uuid.UUID,
    body: ConsequenceRequest,
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> dict:
    """Record what actually happened after a late delivery (`REC-2`).

    **This had to exist before the nightly close could run.**
    `close_consequence_windows` records *silence* for every late order nobody
    judged inside the window. With no way to judge one, every late delivery
    would become a "nothing happened" label - false labels, in an append-only
    ledger, indistinguishable from true ones by the time anyone trained on
    them. The scheduler was not wired until this was.

    Any ops session, like the exception queue and the override: the person who
    took the angry phone call is the person who should record it, and a
    consequence recorded a week later by somebody senior is a worse label.
    """
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="No such order")
    try:
        entry = await record_consequence(
            session,
            order,
            body.kind,
            occurred_at=body.occurred_at or datetime.now(timezone.utc),
            detail=body.detail,
            amount_cents=body.amount_cents,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await session.commit()
    return {"outcome_id": str(entry.id), "consequence": body.kind}


@router.get("/operations/linkage-flags", response_model=list[LinkageFlagView])
async def linkage_flags(
    hub_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> list[LinkageFlagView]:
    """The open questions the linkage detectors raised (`REC-4`), oldest first.

    A flag is a question, not a finding - *"a return has been waiting at this
    dock since Tuesday"*, not an accusation. A dispatcher who reads them as
    accusations stops reading them.

    The detectors ran nowhere and were read nowhere until this. Wiring the
    runner without this would have raised flags into a table no one opens,
    which is the same failure as not running them with more disk use.
    """
    flags = await open_flags(session, hub_id=hub_id)
    return [
        LinkageFlagView(
            id=flag.id,
            kind=flag.kind,
            subjects=flag.subjects,
            detail=flag.detail,
            detected_at=flag.detected_at,
            resolved_at=flag.resolved_at,
            resolution_note=flag.resolution_note,
        )
        for flag in flags
    ]


@router.post("/operations/linkage-flags/{flag_id}/resolve", response_model=LinkageFlagView)
async def resolve_linkage_flag(
    flag_id: uuid.UUID,
    note: str | None = None,
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> LinkageFlagView:
    """Somebody looked. Recorded rather than deleted (`REC-4`).

    A dismissed flag is evidence that a person considered the case, which is
    worth as much as the flag was - and deleting it would let the detector raise
    the same question again tomorrow.
    """
    flag = await session.get(LinkageFlag, flag_id)
    if flag is None:
        raise HTTPException(status_code=404, detail="No such flag")
    await resolve_flag(session, flag, note=note)
    await session.commit()
    return LinkageFlagView(
        id=flag.id, kind=flag.kind, subjects=flag.subjects, detail=flag.detail,
        detected_at=flag.detected_at, resolved_at=flag.resolved_at,
        resolution_note=flag.resolution_note,
    )


@router.get("/operations/override-reasons", response_model=list[OverrideReasonOption])
async def override_reasons(
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> list[OverrideReasonOption]:
    """The reason codes an override may carry (`docs/ROADMAP_1.5.md` CON-2).

    Served rather than duplicated in the dashboard. A hardcoded copy drifts from
    the CHECK constraint in migration `0060`, and the symptom of that drift is a
    dispatcher choosing a reason the database rejects - at the moment they are
    least able to absorb it.
    """
    return [
        OverrideReasonOption(
            code=code,
            label=REASON_LABELS[code],
            note_required=code in REASON_CODES_REQUIRING_NOTE,
        )
        for code in REASON_CODES
    ]


@router.post("/orders/{order_id}/override", response_model=OverrideView)
async def override_order(
    order_id: uuid.UUID,
    body: OverrideRequest,
    session: AsyncSession = Depends(get_db),
    ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> OverrideView:
    """Overrule the queue on one order, with a reason (`CON-2`, `CON-3`).

    *"No override completes without a reason."* *"Every override lands in the
    decision log as a labelled example."*

    **Any ops session, not admin-only** - and that is a deliberate reading of
    `require_admin`, whose docstring scopes it to *"the specific mutating
    endpoints a viewer shouldn't reach"* and names running a cycle, onboarding a
    client, revoking a device. An override is none of those: it is the ordinary
    work of the person answering the phone, and a dispatcher who cannot release
    an order when the customer calls cannot run a day. What makes that safe is
    not the role but the record - every override is attributed to an email and
    append-only, so this is accountable rather than unguarded.

    Refusals come back as 409 with a sentence a dispatcher can act on, not a 500.
    The common one is that the order moved since the screen was loaded.
    """
    try:
        outcome = await apply_override(
            session,
            order_id=order_id,
            action=body.action,
            reason_code=body.reason_code,
            note=body.note,
            ops_user_id=ops.ops_user_id,
            ops_user_email=ops.email,
        )
    except OverrideRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    override = outcome.override
    return OverrideView(
        id=override.id,
        order_id=override.order_id,
        overridden_at=override.overridden_at,
        action=override.action,
        reason_code=override.reason_code,
        reason_label=REASON_LABELS.get(override.reason_code, override.reason_code),
        note=override.note,
        by=override.ops_user_email,
        system_action=override.system_action,
        system_reason=override.system_reason,
        system_decision_known=override.system_decision_known,
        contradicted_the_system=outcome.contradicted_the_system,
        order_status_before=outcome.previous_status.value,
        order_status_after=outcome.new_status.value,
    )


@router.get("/orders/{order_id}/explanation", response_model=OrderExplanationView)
async def order_explanation(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> OrderExplanationView:
    """Why is this order waiting? (`docs/ROADMAP_1.5.md` AGT-4.)

    *"Every explanation cites `REC-1`'s decision log rather than narrating. No
    explanation the record cannot support."*

    The pair to `/operations/exceptions`: that says which orders need attention,
    this says why each one is where it is. Every line carries the
    `decision_snapshots` row it came from, because an explanation assembled from
    current state would be a plausible story about the past and a dispatcher
    cannot tell a plausible story from a true one.

    **It refuses rather than infers.** An order held past its deadline looks
    like it should have been released, and saying so would be narration - the
    queue may not have run, the hub may have closed, a driver may have gone off
    shift. When no cycle recorded a decision about the order, that is the answer.

    Any ops session, like the exception queue it sits beside.
    """
    explanation = await explain_order(session, order_id=order_id)
    return OrderExplanationView(
        order_id=order_id,
        is_explained=explanation.is_explained,
        facts=[
            DecisionFactView(
                at=fact.at,
                statement=fact.statement,
                snapshot_id=fact.snapshot_id,
                engine=fact.engine,
            )
            for fact in explanation.facts
        ],
        unexplained=explanation.unexplained,
    )


@router.get("/operations/exceptions", response_model=ExceptionQueueView)
async def operations_exceptions(
    hub_id: Annotated[uuid.UUID | None, Query()] = None,
    session: AsyncSession = Depends(get_db),
    _ops: AuthedOpsUser = Depends(get_current_ops_user),
) -> ExceptionQueueView:
    """What to look at before the phone rings (`docs/ROADMAP_1.5.md` CON-4).

    Not the health check. `app/health/checks.py` counts stuck orders for a
    monitor, answering "is the system healthy". This answers "which of my
    customers is about to ring and what do I do about it", which needs the
    order, the customer, how long, and the next action.

    Four kinds, and they are not equally loud. A driver's flag on an open stop
    is the earliest warning there is - somebody was physically there. A failed
    delivery they may already know about. Past the promise and still moving is
    the easy one to miss. Released from the hold queue and never placed is the
    quietest and the worst, because it looks like an order in transit from every
    other view in the system.

    **Sorted by the clock, which is a heuristic and not a prediction.** The model
    that would rank these properly is `M2` - P(a consequence | this order is
    late) - and it needs hundreds of observed consequences that do not exist
    yet. So the field is called `minutes_waiting` rather than a score, because a
    score would look like the model it is standing in for.

    An empty queue is the correct and common answer. The counts make "nothing is
    outstanding" legible as distinct from "nothing was checked".

    **Any ops session, not admin only.** This was admin-gated when it was
    written, copied from the scorecard beside it, and building the dashboard
    panel showed that to be wrong: `require_admin`'s own docstring says it is
    "for the specific mutating endpoints a viewer shouldn't reach", and this is
    a read. A dispatcher on a viewer account who cannot see their own exceptions
    cannot run a day, which is `CON-1`'s whole bar. It names customers, but so
    do the hold queue and the fleet roster every ops user already reads.
    """
    queue = await build_exception_queue(session, hub_id=hub_id)
    return ExceptionQueueView(
        generated_at=queue.generated_at,
        counts=queue.by_kind(),
        worst_wait_minutes=queue.worst_wait_minutes,
        items=[
            ExceptionItemView(
                kind=item.kind,
                order_id=item.order_id,
                client_id=item.client_id,
                external_ref=item.external_ref,
                sla_tier=item.sla_tier,
                minutes_waiting=item.minutes_waiting,
                promised_at=item.promised_at,
                detail=item.detail,
                next_action=item.next_action,
            )
            for item in queue.items
        ],
    )


@router.get("/operations/credit-exposure", response_model=CreditExposureView)
async def credit_exposure(
    window_days: Annotated[int, Query(ge=1, le=365)] = CREDIT_WINDOW_DAYS,
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> CreditExposureView:
    """What the service-level credits are costing us (docs/ROADMAP.md W3, E11).

    `W3` made a missed commitment credit a client's statement automatically. Nothing made
    the total visible - a credit shows up on one invoice, for one client, after billing
    runs, so a month of breaches reads as zero until somebody generates an invoice.

    **`accruing` is the half worth looking at**: delivered work not yet invoiced that
    would breach if it were, computed by calling the same `assess_credits` invoicing
    calls on the same candidate set. What ops sees is what will hit the statement.

    Each tier carries its **configured credit percentage** beside the money, because
    `E11` is an open decision about exactly those numbers and the useful input is what
    the current placeholders have already cost.

    Ops-admin only: this is a cross-client view of money owed.
    """
    exposure = await build_credit_exposure(session, window_days=window_days)
    return CreditExposureView(
        generated_at=exposure.generated_at,
        window_days=exposure.window_days,
        window_start=exposure.window_start,
        issued_cents=exposure.issued_cents,
        accruing_cents=exposure.accruing_cents,
        total_cents=exposure.total_cents,
        by_tier=[
            TierExposureView(
                sla_tier=t.sla_tier,
                credit_percent=t.credit_percent,
                credit_cents=t.credit_cents,
                breach_count=t.breach_count,
                delivered_count=t.delivered_count,
                breach_rate_percent=t.breach_rate_percent,
            )
            for t in exposure.by_tier
        ],
        by_client=[
            ClientExposureView(
                client_id=c.client_id,
                client_name=c.client_name,
                issued_cents=c.issued_cents,
                accruing_cents=c.accruing_cents,
                total_cents=c.total_cents,
            )
            for c in exposure.by_client
        ],
        unassessable_orders=exposure.unassessable_orders,
        unpriced_orders=exposure.unpriced_orders,
    )


@router.get("/hubs", response_model=list[HubSummary])
async def list_hubs(session: AsyncSession = Depends(get_db)) -> list[HubSummary]:
    """Backs the dashboard's hub picker (docs/ROADMAP.md D1) - hub
    selection was a raw UUID paste field until now, since no read endpoint
    existed for the `hubs` table at all. Excludes inactive hubs - nothing
    in ops tooling should be able to select one to act on."""
    result = await session.execute(select(Hub).where(Hub.active.is_(True)).order_by(Hub.name))
    return [HubSummary(hub_id=str(hub.id), name=hub.name) for hub in result.scalars().all()]


@router.post("/fleet/{hub_id}/drivers/state")
async def upsert_driver_state(
    hub_id: str, state: DriverState, _admin: AuthedOpsUser = Depends(require_admin)
) -> dict:
    manager = FleetStateManager()
    await manager.upsert_driver_state(state)
    # A status change (available/en_route/off_shift/on_break) changes what
    # the optimizer can assign - a raw location ping (below) doesn't, so
    # only this endpoint publishes.
    await dispatch_event_bus.publish(hub_id, "driver_status_changed")
    return {"ok": True}


@router.post("/fleet/{hub_id}/drivers/location")
async def upsert_driver_location(
    hub_id: str, location: DriverLocation, _admin: AuthedOpsUser = Depends(require_admin)
) -> dict:
    manager = FleetStateManager()
    await manager.update_driver_location(location, hub_id)
    return {"ok": True}


@router.get("/fleet/{hub_id}/drivers", response_model=list[DriverState])
async def list_fleet_overview(hub_id: str, session: AsyncSession = Depends(get_db)) -> list[DriverState]:
    """
    Full driver roster for a hub - available, en_route, on_break, and
    off_shift alike. Built for the orchestrator dashboard; the optimizer
    itself only ever reads the narrower available-drivers view
    (FleetStateManager.get_fleet_snapshot), which is why the display name
    join and the location fetch below live here and not in
    FleetStateManager/DriverState's Redis round-trip - the hot path has no
    reason to pay for either.

    Last reported position is included (docs/ROADMAP.md F1) so the
    dashboard can place drivers on a map (F2) rather than only listing
    their assigned stops. A driver who has never reported one comes back
    with lat/lng null, which is also precisely why the optimizer would skip
    them - so this view doubles as the diagnostic for "why is nobody being
    assigned work."
    """
    manager = FleetStateManager()
    roster = await manager.get_fleet_overview(hub_id)
    if not roster:
        return roster

    driver_ids = [uuid.UUID(d.driver_id) for d in roster]
    result = await session.execute(select(Driver.id, Driver.name).where(Driver.id.in_(driver_ids)))
    names = {str(driver_id): name for driver_id, name in result.all()}

    # One Redis read per driver: location lives under its own per-driver key,
    # not in the state hash get_fleet_overview already bulk-read. Gathered
    # concurrently so a full roster costs one round-trip's latency rather
    # than one per driver.
    locations = await asyncio.gather(
        *(manager.get_driver_location(hub_id, d.driver_id) for d in roster)
    )

    for driver, location in zip(roster, locations):
        driver.name = names.get(driver.driver_id)
        if location is not None:
            driver.lat = location.lat
            driver.lng = location.lng
            driver.location_recorded_at = location.recorded_at
    return roster


@router.get("/batch-queue/{hub_id}/held-orders", response_model=list[HeldOrderView])
async def list_held_orders(hub_id: str) -> list[HeldOrderView]:
    """
    Everything currently sitting in the Batch-Hold Queue for a hub.
    cluster_mate_ids is computed fresh here from the same clustering logic
    the Dispatch Optimizer uses (app.batch_queue.clustering.cluster_members)
    against the rest of this response's rows - it isn't persisted, since
    it changes as soon as a sibling order is added/removed/released.
    """
    store = HoldQueueStore()
    held = await store.get_all(hub_id)
    radius = settings.batch_hold_cluster_radius_miles
    views: list[HeldOrderView] = []
    for order in held:
        candidates = [(o.order_id, o.shop_lat, o.shop_lng) for o in held if o.order_id != order.order_id]
        cluster_mate_ids = cluster_members(order.shop_lat, order.shop_lng, candidates, radius)
        views.append(
            HeldOrderView(
                order_id=order.order_id,
                shop_lat=order.shop_lat,
                shop_lng=order.shop_lng,
                sla_tier=order.sla_tier,
                hold_deadline=order.hold_deadline,
                held_since=order.held_since,
                shop_name=order.shop_name,
                cluster_mate_ids=cluster_mate_ids,
            )
        )
    return views


@router.get("/orders/{hub_id}/summary", response_model=OrderStatusSummary)
async def get_order_status_summary(
    hub_id: str, session: AsyncSession = Depends(get_db)
) -> OrderStatusSummary:
    """Order counts by status for a hub - dashboard quick-glance widget."""
    result = await session.execute(
        select(Order.status, func.count())
        .where(Order.hub_id == uuid.UUID(hub_id))
        .group_by(Order.status)
    )
    counts = {status.value: count for status, count in result.all()}
    return OrderStatusSummary(hub_id=hub_id, counts=counts)


@router.post("/optimizer/{hub_id}/run-cycle", response_model=OptimizationResult)
async def run_optimizer_cycle(hub_id: str, _admin: AuthedOpsUser = Depends(require_admin)) -> OptimizationResult:
    """
    Manually trigger one Dispatch Optimizer cycle for a hub. Real cycles
    are now event-triggered (see app/optimizer/event_trigger.py) off order
    ingestion and driver status changes rather than polled - this endpoint
    remains for manual triggering, testing, and ops (e.g. forcing a cycle
    after an out-of-band fleet-state fix). Admin-only (docs/ROADMAP.md S1) -
    a viewer can watch a cycle happen but not force one.
    """
    service = DispatchOptimizerService()
    return await service.run_cycle(hub_id)


@router.get("/optimizer/{hub_id}/last-cycle", response_model=LastCycleSnapshot | None)
async def get_last_cycle(hub_id: str) -> LastCycleSnapshot | None:
    """
    The most recently completed Dispatch Optimizer cycle for this hub,
    whether it was triggered manually or automatically off an event - see
    app/optimizer/last_cycle_store.py. Returns null if no cycle has run
    for this hub yet (e.g. a brand new hub, or right after a Redis flush).
    """
    store = LastCycleStore()
    return await store.get(hub_id)


@router.post("/learning-loop/{hub_id}/run-nightly-job", response_model=NightlyJobResult)
async def run_learning_loop_nightly_job(
    hub_id: str, session: AsyncSession = Depends(get_db), _admin: AuthedOpsUser = Depends(require_admin)
) -> NightlyJobResult:
    """
    Manually trigger the Learning Loop's pattern-detection job for a hub
    (component 6). In production this runs on a schedule (nightly, per the
    design doc) rather than on demand - this endpoint exists for manual
    triggering, testing, and as the hook a scheduler would call into.
    Admin-only (docs/ROADMAP.md S1).

    Detected patterns become `proposed_rules` rows - nothing is
    auto-promoted to `active_rules`. A human reviews and promotes.
    """
    created = await run_nightly_job(session, hub_id=hub_id)
    return NightlyJobResult(
        hub_id=hub_id,
        proposals_created=[
            ProposedRuleSummary(
                proposed_rule_id=str(rule.id),
                shop_id=rule.scope.get("shop_id", ""),
                rule_type=rule.rule_type,
                proposed_change=rule.proposed_change,
                confidence=float(rule.confidence),
                supporting_annotation_count=rule.supporting_annotation_count,
            )
            for rule in created
        ],
    )
