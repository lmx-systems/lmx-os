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
from app.record.decisions import snapshot_that_assigned
from app.sla.commitment import delivery_commitment, terms_for_client
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


async def record_delivery_outcomes(
    session: AsyncSession, orders: list[Order]
) -> list[OutcomeEntry]:
    """Record what happened for every order that just became delivered (`REC-3`).

    Called from `app/api/driver_routes.py` when a driver completes a dropoff.
    Until this existed the ledger had no writer at all: a delivery advanced the
    order, paid the driver and adjusted the vehicle load, and recorded nothing -
    so `REC-3` read `BUILT` with an empty table, and every measurement built on
    it had no rows to read (`docs/ROADMAP_AUDIT_2026-09.md`).

    **Pass only the orders that actually moved.** `advance_orders` returns
    exactly those, skipping any already delivered, which is what stops a
    replayed offline action writing a second outcome for one delivery. An
    append-only ledger cannot take that back afterwards.

    **The commitment is resolved here, now, against the client's current terms** -
    which is right at this moment and would not be later. `record_delivery_outcome`
    takes it as an argument for that reason: terms change, and an outcome
    recomputed afterwards would judge this delivery against a promise made after
    it happened, in whichever direction favoured whoever changed them.

    **In the caller's transaction, deliberately.** The file's pattern for payouts
    and notifications is "commit the delivery first, act after", because a failed
    SMS must never roll back a completed delivery. The ledger is not that: it is
    our own record of the delivery, and a delivered order with no outcome row is
    precisely the state this function exists to make impossible.
    """
    if not orders:
        return []

    # One query per distinct client rather than per order - a route can carry a
    # dozen drops for the same customer.
    terms_by_client: dict = {}
    entries: list[OutcomeEntry] = []
    for order in orders:
        if order.client_id is not None and order.client_id not in terms_by_client:
            terms_by_client[order.client_id] = await terms_for_client(
                session, order.client_id
            )
        terms = terms_by_client.get(order.client_id, {})
        commitment = delivery_commitment(order, terms.get(order.sla_tier))
        entries.append(
            await record_delivery_outcome(
                session,
                order,
                commitment,
                decision_snapshot_id=await snapshot_that_assigned(session, order),
            )
        )
    return entries
