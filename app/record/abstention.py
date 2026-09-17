"""Writing down that we deliberately did nothing (`EXP-3`'s missing check).

Every other entry in the ledger records something that happened. This one
records something that did not, on purpose, and it is the only evidence that
`EXP-1`'s control arm was honoured.

**Why the absence has to be written down.** The arm means "dispatched as the
customer would have" - no batching hold of ours, no waiting for a cluster-mate.
Afterwards, an order genuinely left alone and an order we quietly held look
identical: both were delivered, both have timestamps, and nothing distinguishes
them. `app/experiment/integrity.py` shipped reporting exactly that as a
limitation it could not fix, on every run, because a monitor that stayed silent
about it would have read as a clean bill.

**What the entry carries, and why it is the deadline.** `would_have_held_until`
is the hold the SLA engine classified and we then did not take. That is the
whole abstention in one field: it says what the alternative was, so somebody
reading the ledger later can see the size of what we gave up rather than only
that we gave something up. An entry saying "abstained" and nothing else would
prove the code ran, not that it did anything.

**It does not make the arm trustworthy on its own.** This proves intake declined
to hold the order. It does not prove nothing downstream picked it up and batched
it anyway - that would need the optimizer to record its own abstentions too, and
the optimizer never sees an arm label by design. What it closes is the gap that
mattered most and was cheapest to close: the hold, which is the thing the arm is
actually about.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outcome_entry import KIND_ARM_ABSTENTION, SUBJECT_ORDER, OutcomeEntry
from app.record.outcomes import record_outcome

# What we declined to do. One value today; named rather than implied so a
# second abstention - an optimizer that declines to batch, say - does not have
# to reinterpret these rows.
DECLINED_TO_HOLD = "hold"


async def record_arm_abstention(
    session: AsyncSession,
    *,
    hub_id,
    order_id,
    arm: str,
    occurred_at: datetime,
    would_have_held_until: datetime | None,
    declined: str = DECLINED_TO_HOLD,
    reason: str = "order is in the control arm; the batching hold does not apply",
) -> OutcomeEntry:
    """Record that this order was left alone, and what the alternative was."""
    return await record_outcome(
        session,
        hub_id=hub_id,
        subject_type=SUBJECT_ORDER,
        subject_id=order_id,
        kind=KIND_ARM_ABSTENTION,
        occurred_at=occurred_at,
        values={
            "arm": arm,
            "declined": declined,
            "reason": reason,
            "would_have_held_until": (
                would_have_held_until.isoformat() if would_have_held_until else None
            ),
        },
    )


async def orders_with_an_abstention(session: AsyncSession, order_ids: list) -> set:
    """Which of these orders we have written an abstention for."""
    if not order_ids:
        return set()
    return set(
        await session.scalars(
            select(OutcomeEntry.subject_id).where(
                OutcomeEntry.subject_id.in_(order_ids),
                OutcomeEntry.kind == KIND_ARM_ABSTENTION,
            )
        )
    )
