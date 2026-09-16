"""Attaching what happened to the decision that caused it (REC-3).

The done-when - *"outcomes attach by key without touching the decision row"* -
reads like a nicety and is not. REC-1's table rejects UPDATE at the database,
so the obvious shape (a `delivered_on_time` column filled in later) cannot be
built at all. This is the shape that can: a separate append-only ledger keyed
on the decision.

The reason to keep them apart is not tidiness. A decision is what was known at
an instant, and the moment you write an outcome into it you have a row that is
partly a record of the past and partly a running total - so "what did we know"
and "how did it turn out" stop being separable, which is the one question a
disputed savings statement turns on.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.outcome_entry import (
    KIND_DELIVERED,
    KIND_DISPUTED,
    KIND_DWELL,
    KIND_FAILED,
    KINDS,
    SUBJECT_ORDER,
    SUBJECT_TYPES,
    OutcomeEntry,
)
from app.sla.commitment import Commitment

__all__ = [
    "record_outcome",
    "record_delivery_outcome",
    "outcomes_for",
    "current_outcome",
    "supersede_outcome",
]


async def record_outcome(
    session: AsyncSession,
    *,
    hub_id,
    subject_type: str,
    subject_id,
    kind: str,
    occurred_at: datetime,
    values: dict | None = None,
    decision_snapshot_id=None,
    supersedes=None,
) -> OutcomeEntry:
    """Write one entry. Never updates anything, here or elsewhere.

    In particular it never touches `decision_snapshots` - the link runs one way,
    from the outcome to the decision, which is what lets the decision stay
    exactly as it was written.
    """
    if subject_type not in SUBJECT_TYPES:
        raise ValueError(f"subject_type must be one of {SUBJECT_TYPES}, got {subject_type!r}")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")

    entry = OutcomeEntry(
        hub_id=hub_id,
        decision_snapshot_id=decision_snapshot_id,
        subject_type=subject_type,
        subject_id=subject_id,
        kind=kind,
        occurred_at=occurred_at,
        recorded_at=datetime.now(timezone.utc),
        values=values or {},
        supersedes=supersedes,
    )
    session.add(entry)
    await session.flush()
    return entry


async def record_delivery_outcome(
    session: AsyncSession,
    order: Order,
    commitment: Commitment,
    *,
    decision_snapshot_id=None,
) -> OutcomeEntry:
    """Whether a delivered order beat the promise it was judged against.

    The promise is passed in rather than recomputed, and that is deliberate:
    `delivery_commitment` reads the client's current SLA terms, and terms
    change. An outcome computed against today's terms would judge a delivery
    made under last quarter's - quietly, and in whichever direction favoured
    whoever changed the terms. The caller resolves the commitment at the time
    it applied; this records what it was.

    `lateness_seconds` is signed: negative is early. Storing the sign rather
    than a boolean means the same rows answer "were we on time" and "by how
    much", and the second question is the one a savings statement needs.
    """
    if order.delivered_at is None:
        raise ValueError(f"order {order.id} is not delivered")

    values: dict = {
        "delivered_at": order.delivered_at.isoformat(),
        "promised_delivery_by": (
            commitment.promised_delivery_by.isoformat()
            if commitment.promised_delivery_by
            else None
        ),
        # Whose promise it was. An LMX commitment we missed is a breach; an
        # EXTERNAL one is somebody else's window we failed to hit, and the two
        # must not be summed into one on-time rate (§1.1's sla_owner split).
        "commitment_source": commitment.source,
        "sla_tier": order.sla_tier,
    }
    if commitment.promised_delivery_by is not None:
        lateness = (order.delivered_at - commitment.promised_delivery_by).total_seconds()
        values["lateness_seconds"] = lateness
        values["on_time"] = lateness <= 0
    else:
        # No promise means nothing to be late against. Recorded explicitly so a
        # later on-time rate can exclude these rather than counting them as
        # successes, which would flatter every figure that uses it.
        values["lateness_seconds"] = None
        values["on_time"] = None

    return await record_outcome(
        session,
        hub_id=order.hub_id,
        subject_type=SUBJECT_ORDER,
        subject_id=order.id,
        kind=KIND_DELIVERED,
        occurred_at=order.delivered_at,
        values=values,
        decision_snapshot_id=decision_snapshot_id,
    )


async def outcomes_for(
    session: AsyncSession, *, subject_id, include_superseded: bool = True
) -> list[OutcomeEntry]:
    """Everything recorded about one subject, oldest first.

    Superseded entries are included by default. The history is the point: a
    ledger that hid its corrections would be an overwrite with extra steps.
    """
    entries = list(
        await session.scalars(
            select(OutcomeEntry)
            .where(OutcomeEntry.subject_id == subject_id)
            .order_by(OutcomeEntry.recorded_at, OutcomeEntry.id)
        )
    )
    if include_superseded:
        return entries
    superseded = {e.supersedes for e in entries if e.supersedes is not None}
    return [e for e in entries if e.id not in superseded]


async def current_outcome(
    session: AsyncSession, *, subject_id, kind: str
) -> OutcomeEntry | None:
    """The entry of this kind that has not been superseded.

    Returns None rather than raising when there are none - an order with no
    recorded outcome is the ordinary state for anything still in flight.
    """
    live = await outcomes_for(session, subject_id=subject_id, include_superseded=False)
    matching = [entry for entry in live if entry.kind == kind]
    return matching[-1] if matching else None


async def supersede_outcome(
    session: AsyncSession,
    previous: OutcomeEntry,
    *,
    values: dict,
    reason: str,
    occurred_at: datetime | None = None,
) -> OutcomeEntry:
    """We learned better. Recorded as a new entry, with the old one intact.

    `reason` is required rather than optional. A correction with no stated
    cause is indistinguishable from a mistake, and the first question anybody
    asks of a changed number is why it changed.
    """
    if not reason.strip():
        raise ValueError("a correction needs a reason")
    return await record_outcome(
        session,
        hub_id=previous.hub_id,
        subject_type=previous.subject_type,
        subject_id=previous.subject_id,
        kind=previous.kind,
        occurred_at=occurred_at or previous.occurred_at,
        values={**values, "correction_reason": reason},
        decision_snapshot_id=previous.decision_snapshot_id,
        supersedes=previous.id,
    )


# Re-exported so callers have one import site for the vocabulary.
__all__ += ["KIND_DELIVERED", "KIND_DISPUTED", "KIND_DWELL", "KIND_FAILED"]
