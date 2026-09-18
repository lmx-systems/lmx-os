"""Why is this order waiting? (`AGT-4`)

*"Every explanation cites `REC-1`'s decision log rather than narrating. No
explanation the record cannot support."*

That second sentence is the whole design. An explanation assembled from the
current state of the world would be a plausible story about the past, and a
dispatcher cannot tell a plausible story from a true one - which is precisely
when they would stop trusting the console. So every line here carries the
snapshot it came from, and where the record is silent this says so rather than
reasoning from what the order looks like now.

## It could not answer its own question until this was built

`run_hold_cycle` has always returned a reason for every order it looks at - hot
shot, deadline reached, no cluster mate, no driver available, conflict with a
more urgent order - and `run_cycle` kept only the set of ids it released. The
reasons went nowhere. So a product whose central claim is that **the hold is the
product** held orders and recorded no reason for any of it, and the only honest
explanation of a hold was "we do not know".

`decision_snapshots.hold_decisions` now carries them. Snapshots written before
that read as "no reasons recorded", which is true and is not backfilled -
inventing them would be the opposite of the point.

## What it will not do

It will not infer. An order that is `held` with a `hold_deadline` in the past
looks like it should have been released, and saying so would be narration: the
queue may not have run, the hub may have been closed, a driver may have gone off
shift between cycles. If no cycle recorded a decision about this order, the
answer is that no cycle recorded a decision about this order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_snapshot import DecisionSnapshot
from app.models.dispatcher_override import REASON_LABELS, DispatcherOverride
from app.models.order import Order

# How far back a single order's history is read. A cycle runs every few
# minutes, so this is days of them - and an unbounded scan would make an
# on-demand explanation a table scan of the whole decision log.
_DECISION_SCAN_LIMIT = 50

# What the batch-hold queue's reasons mean, in a sentence a dispatcher can act
# on. Keyed by the exact strings `app/batch_queue/queue.py` emits, so a reason
# it stops producing stops appearing here rather than being silently reworded.
_REASON_TEXT = {
    "hot_shot_immediate_release": "released immediately - HOT_SHOT never waits for a cluster mate",
    "sla_hold_deadline_reached": "released - the hold deadline arrived",
    "cluster_mate_found": "released - another order going the same way arrived",
    "would_conflict_with_higher_priority_order": (
        "released early - holding it risked a more urgent order's deadline"
    ),
    "no_available_drivers": "still held - no driver was on shift to dispatch to",
    "no_cluster_mate_and_drivers_available": (
        "still held - waiting for another order going the same way"
    ),
}


@dataclass(frozen=True)
class Fact:
    """One thing the record says, and where it says it."""

    at: datetime
    statement: str
    snapshot_id: object
    engine: str


@dataclass
class Explanation:
    order_id: object
    facts: list = field(default_factory=list)
    # Set when the record cannot answer. Carries why, because "no explanation"
    # and "nothing was recorded" are different facts and only the second tells
    # somebody what to fix.
    unexplained: str | None = None

    @property
    def is_explained(self) -> bool:
        return bool(self.facts)

    def render(self) -> str:
        if not self.facts:
            return self.unexplained or "Nothing is recorded about this order."
        lines = [f"Order {self.order_id}"]
        for fact in self.facts:
            lines.append(f"  {fact.at:%Y-%m-%d %H:%M}  {fact.statement}")
            lines.append(f"                     decision {fact.snapshot_id} ({fact.engine})")
        return "\n".join(lines)


def _describe(entry: dict) -> str:
    reason = entry.get("reason", "")
    text = _REASON_TEXT.get(reason)
    if text is None:
        # An unmapped reason is printed raw rather than smoothed into prose. A
        # reason this module has not been taught is still what the record says,
        # and paraphrasing it would be the narration AGT-4 forbids.
        action = entry.get("action", "decided")
        return f"{action} - {reason or 'no reason recorded'}"
    mates = entry.get("cluster_mate_ids") or []
    if mates and reason in (
        "cluster_mate_found",
        "no_cluster_mate_and_drivers_available",
    ):
        return f"{text} ({len(mates)} candidate(s) nearby)"
    return text


@dataclass(frozen=True)
class SystemDecision:
    """The last thing a dispatch cycle recorded about one order.

    `CON-3`'s half of a labelled override. Carried as values, because the label
    must not change when the snapshot does - and `known=False` is a real answer
    rather than a missing one, which is the distinction the whole of `AGT-4`
    turns on.
    """

    known: bool
    action: str | None = None
    reason: str | None = None
    snapshot_id: object = None
    at: datetime | None = None


async def latest_system_decision(session: AsyncSession, *, order_id) -> SystemDecision:
    """What the queue last decided about this order, if anything.

    Shares `explain_order`'s scan rather than re-deriving it, so "what the system
    decided" has one definition. A second implementation would drift, and the
    two places it is read - the explanation a dispatcher sees and the label an
    override is recorded against - are precisely the two that must agree.
    """
    order = await session.get(Order, order_id)
    if order is None:
        return SystemDecision(known=False)

    key = str(order_id)
    snapshots = list(
        await session.scalars(
            select(DecisionSnapshot)
            .where(
                DecisionSnapshot.hub_id == order.hub_id,
                DecisionSnapshot.decided_at >= order.requested_at,
            )
            .order_by(DecisionSnapshot.decided_at.desc())
            .limit(_DECISION_SCAN_LIMIT)
        )
    )
    for snapshot in snapshots:
        for entry in snapshot.hold_decisions or []:
            if entry.get("order_id") == key:
                return SystemDecision(
                    known=True,
                    action=entry.get("action"),
                    reason=entry.get("reason"),
                    snapshot_id=snapshot.id,
                    at=snapshot.decided_at,
                )
    return SystemDecision(known=False)


async def explain_order(
    session: AsyncSession, *, order_id, limit: int = _DECISION_SCAN_LIMIT
) -> Explanation:
    """Assemble what the decision log says about one order, oldest first.

    Bounded to cycles from the order's own arrival onwards. A cycle that ran
    before the order existed cannot have decided anything about it, and reading
    the whole history to prove that would make an on-demand explanation a table
    scan.
    """
    explanation = Explanation(order_id=order_id)
    order = await session.get(Order, order_id)
    if order is None:
        explanation.unexplained = (
            "No such order. Nothing can be explained about an order that is not "
            "in the system."
        )
        return explanation

    snapshots = list(
        await session.scalars(
            select(DecisionSnapshot)
            .where(
                DecisionSnapshot.hub_id == order.hub_id,
                DecisionSnapshot.decided_at >= order.requested_at,
            )
            .order_by(DecisionSnapshot.decided_at)
            .limit(limit)
        )
    )
    # No early return when there are no snapshots. A human override is a decision
    # about this order whether or not a cycle ever ran, and returning "nothing
    # has decided anything about it" while an override sits in the record would
    # be a refusal the record contradicts - the mirror image of the narration
    # this module exists to avoid.
    no_cycle_has_run = not snapshots

    key = str(order_id)
    for snapshot in snapshots:
        for entry in snapshot.hold_decisions or []:
            if entry.get("order_id") == key:
                explanation.facts.append(
                    Fact(
                        at=snapshot.decided_at,
                        statement=_describe(entry),
                        snapshot_id=snapshot.id,
                        engine=snapshot.engine,
                    )
                )

        for assignment in snapshot.assignments or []:
            # Read `visits`, not `stop_ids`. `RouteAssignment.stop_ids` is a
            # derived property, so it is absent from the stored blob entirely -
            # a version of this that read it found nothing and reported every
            # assigned order as unexplained, which is the exact failure mode
            # AGT-4 exists to prevent, arrived at from the other direction.
            legs = [
                visit.get("kind")
                for visit in assignment.get("visits") or []
                if visit.get("order_id") == key
            ]
            if legs:
                explanation.facts.append(
                    Fact(
                        at=snapshot.decided_at,
                        statement=(
                            f"assigned to driver {assignment.get('driver_id')}"
                            f" ({', '.join(legs)})"
                        ),
                        snapshot_id=snapshot.id,
                        engine=snapshot.engine,
                    )
                )

        if key in (snapshot.unassigned_stop_ids or []):
            explanation.facts.append(
                Fact(
                    at=snapshot.decided_at,
                    statement=(
                        "released from the hold queue and the solver could not "
                        "place it this cycle"
                    ),
                    snapshot_id=snapshot.id,
                    engine=snapshot.engine,
                )
            )

        # A cycle that ran and said nothing about this order is not reported.
        # Silence in one cycle out of forty is noise; silence in all of them is
        # the `unexplained` case below, which is the one worth saying.

    # The human decisions, from CON-2's record. Shown beside the system's rather
    # than in a panel of their own: "the queue held it, then someone released it
    # because the customer called" is one story, and split across two lists it
    # reads as two unrelated things that happened to the same order.
    for override in await session.scalars(
        select(DispatcherOverride)
        .where(DispatcherOverride.order_id == order_id)
        .order_by(DispatcherOverride.overridden_at)
    ):
        reason = REASON_LABELS.get(override.reason_code, override.reason_code)
        statement = (
            f"{override.action}d by {override.ops_user_email} - {reason.lower()}"
        )
        if override.note:
            statement += f' ("{override.note}")'
        explanation.facts.append(
            Fact(
                at=override.overridden_at,
                statement=statement,
                # An override cites itself. It is a record in its own right, not
                # a reading of one, and pointing at the snapshot it disagreed
                # with would credit the decision to the cycle that lost.
                snapshot_id=override.id,
                engine="dispatcher override",
            )
        )

    explanation.facts.sort(key=lambda fact: fact.at)

    if not explanation.facts:
        if no_cycle_has_run:
            explanation.unexplained = (
                "No dispatch cycle has run for this hub since the order arrived, "
                "so nothing has decided anything about it yet."
            )
        else:
            recorded = sum(1 for s in snapshots if s.hold_decisions)
            explanation.unexplained = (
                f"{len(snapshots)} cycle(s) ran since this order arrived and none "
                "recorded a decision about it."
                + (
                    ""
                    if recorded
                    else " None of them recorded hold reasons at all - they predate "
                    "the column that holds them, so the reasons were never captured "
                    "rather than lost."
                )
            )
    return explanation
