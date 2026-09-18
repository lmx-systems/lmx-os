"""Freezing a dispatch decision so it can be replayed exactly (REC-1).

The done-when is two clauses, and the second is the hard one:

  1. A decision replays to reproduce its inputs exactly.
  2. No field may reference data created after the decision.

Clause 2 is what rules out the obvious implementation. Storing `order_id` and
joining back to `orders` at replay time would reproduce *today's* tier and
deadline, not the ones the optimizer had - and it would do so silently, which
is worse than failing. So the inputs are copied out as values at decision time
and never read through a reference again. Ids are kept alongside for tracing,
but no replay depends on them still resolving to anything - including
`hub_id`, which is a plain column rather than a foreign key for the same
reason.

`CyclePlan` already carries the inputs it decided on - its own docstring says
the plan owns the snapshot, because re-fetching after planning would read state
that had moved. That makes this a matter of persisting what is already in hand
rather than re-querying the world, which is the only version that could be
correct.
"""
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_snapshot import MODE_LIVE, MODE_SHADOW, DecisionSnapshot
from app.models.order import Order
from app.schemas.optimizer import CyclePlan

__all__ = [
    "MODE_LIVE",
    "MODE_SHADOW",
    "canonical_inputs_hash",
    "freeze_plan_inputs",
    "record_decision",
    "replay_inputs",
]


def freeze_plan_inputs(plan: CyclePlan) -> dict:
    """Everything the cycle saw, as plain values.

    Ordered deterministically - stops and drivers by id - so two cycles that
    saw the same world hash the same regardless of what order the queries
    happened to return. Without that the hash would be a nonce and the
    comparison it exists for would never match.
    """
    return {
        # Schema version, because this blob will be read by code that does not
        # exist yet. A reader that cannot recognise the shape should say so
        # rather than guess at a field that moved.
        "version": 1,
        "hub_id": plan.hub_id,
        "planned_at": plan.planned_at.isoformat(),
        "hub_closed": plan.hub_closed,
        "held_order_count": plan.held_order_count,
        "released_order_ids": sorted(plan.released_order_ids),
        "stops": sorted(
            (
                {
                    "stop_id": stop.stop_id,
                    "order_ids": sorted(stop.order_ids),
                    "lat": stop.lat,
                    "lng": stop.lng,
                    "delivery_lat": stop.delivery_lat,
                    "delivery_lng": stop.delivery_lng,
                    "weight_units": stop.weight_units,
                    # The two that move. An order can be re-tiered and a
                    # deadline can be extended, and a replay that read either
                    # from `orders` would reconstruct a promise the optimizer
                    # was never working to.
                    "sla_tier": stop.sla_tier,
                    "collect_by": stop.collect_by.isoformat() if stop.collect_by else None,
                }
                for stop in plan.stops
            ),
            key=lambda row: row["stop_id"],
        ),
        "drivers": sorted(
            (
                {
                    "driver_id": driver.driver_id,
                    "lat": driver.lat,
                    "lng": driver.lng,
                    "capacity_remaining_units": driver.capacity_remaining_units,
                }
                for driver in plan.drivers
            ),
            key=lambda row: row["driver_id"],
        ),
        # The shop names the plan carried. Copied rather than joined for the
        # same reason as everything else here: a shop can be renamed, and a
        # decision that reads back a name it never saw is not a record of that
        # decision.
        "shop_name_by_order_id": dict(sorted(plan.shop_name_by_order_id.items())),
        "engine": plan.engine,
    }


def canonical_inputs_hash(inputs: dict) -> str:
    """SHA-256 over a canonical JSON form.

    `sort_keys` and no whitespace, so the digest depends on the content and not
    on how Python happened to serialise it. Floats are left as floats: rounding
    them here would make two genuinely different driver positions hash alike,
    and this digest is used to answer "did these cycles see the same world".
    """
    return hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


async def record_decision(
    session: AsyncSession, plan: CyclePlan, *, mode: str = MODE_LIVE
) -> DecisionSnapshot:
    """Persist one cycle's inputs and decision, append-only.

    Called from the optimizer after planning and before - or instead of -
    acting, so the record exists whether or not the decision was carried out.
    A shadow cycle that recorded nothing would leave the comparison W9 depends
    on with one side missing.

    Never updates. The table's trigger would refuse it anyway; this function
    not having an update path is the first line of that.
    """
    if mode not in (MODE_LIVE, MODE_SHADOW):
        raise ValueError(f"mode must be {MODE_LIVE!r} or {MODE_SHADOW!r}, got {mode!r}")

    inputs = freeze_plan_inputs(plan)
    snapshot = DecisionSnapshot(
        hub_id=plan.hub_id,
        decided_at=plan.planned_at,
        mode=mode,
        engine=plan.engine,
        inputs=inputs,
        hold_decisions=[decision.model_dump() for decision in plan.hold_decisions],
        inputs_hash=canonical_inputs_hash(inputs),
        assignments=[assignment.model_dump(mode="json") for assignment in plan.assignments],
        unassigned_stop_ids=sorted(plan.unassigned_stop_ids),
        plan_duration_seconds=plan.plan_duration_seconds,
        hub_closed=plan.hub_closed,
        stop_count=len(plan.stops),
        driver_count=len(plan.drivers),
        assigned_count=len(plan.assignments),
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def replay_inputs(session: AsyncSession, snapshot_id) -> dict:
    """The world as that decision saw it.

    Reads only the snapshot row. That is the whole guarantee: no join, no
    current state, nothing that could have changed since. If this ever needs a
    second query to answer, clause 2 of the done-when has been broken.

    Raises if the stored hash does not match the stored inputs, rather than
    returning them. A mismatch means the row was altered after the fact - by a
    restore, a migration, or someone with enough privilege to get past the
    trigger - and a tampered decision log that answers anyway is worse than one
    that refuses.
    """
    snapshot = await session.scalar(
        select(DecisionSnapshot).where(DecisionSnapshot.id == snapshot_id)
    )
    if snapshot is None:
        raise LookupError(f"no decision snapshot {snapshot_id}")

    recomputed = canonical_inputs_hash(snapshot.inputs)
    if recomputed != snapshot.inputs_hash:
        raise ValueError(
            f"decision snapshot {snapshot_id} does not match its own hash - "
            f"stored {snapshot.inputs_hash[:12]}, recomputed {recomputed[:12]}. "
            "The row has been altered since it was written."
        )
    return snapshot.inputs


async def snapshot_that_assigned(session: AsyncSession, order: Order):
    """The decision snapshot whose plan put this order on a route, if any.

    `REC-1` records what each cycle decided and `REC-3` records what happened.
    They are joinable only if something writes the link, and until this existed
    `outcome_ledger.decision_snapshot_id` was null on every row - two tables
    built to be compared, with nothing connecting them.

    Bounded to cycles from the order's arrival onwards, the same window
    `explain_order` uses and for the same reason: a cycle that ran before the
    order existed cannot have assigned it, and proving that by reading the whole
    log would make a delivery completion a table scan.

    Returns None when no recorded cycle assigned it - a live-route insertion, a
    hand-built route, or a cycle from before the column existed. Null is the
    honest answer there; picking the nearest snapshot would manufacture a
    provenance link that reads exactly like a real one.
    """
    key = str(order.id)
    snapshots = await session.scalars(
        select(DecisionSnapshot)
        .where(
            DecisionSnapshot.hub_id == order.hub_id,
            DecisionSnapshot.decided_at >= order.requested_at,
        )
        .order_by(DecisionSnapshot.decided_at.desc())
        .limit(50)
    )
    for snapshot in snapshots:
        for assignment in snapshot.assignments or []:
            for visit in assignment.get("visits") or []:
                if visit.get("order_id") == key:
                    return snapshot.id
    return None
