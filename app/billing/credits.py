"""
What a missed delivery costs us (docs/ROADMAP.md W3, story DO-3).

**A breach costs nothing today.** C3's billing sums delivered orders and stops, so a
delivery three hours late bills identically to one on time - the contractual credit exists
on paper and nowhere in the system.

**The harder half was that "late" was not computable.** `app/sla/engine.py` defines HOLD
windows, which are when we must set off, not when the customer gets their part.
`hold_deadline` is ours and internal. `promised_at` exists but is only populated when a
source hands us one, which for an LMX-owned order is never. So before a credit could be
charged, a commitment had to exist - `app/models/client_sla_term.py` is that, recorded as
contract data per client and per tier rather than as a constant chosen in this file.

Three rules worth stating, because each one is a way this could quietly be wrong:

1. **No term means no credit, and it is reported.** "We owe nothing" and "nobody wrote down
   what we promised" are different answers, and only one is safe to put on a statement.
   `unassessable` carries the second out to the caller instead of letting it read as clean.
2. **`promised_at` wins over the computed target.** If we told this customer a specific
   time, that is the promise - a per-tier default cannot override something said out loud.
   That rule moved to `app/sla/commitment.py` so the client-facing views apply the same
   one; showing a customer a different target from the one we credit against was the
   defect that prompted it.
3. **A credit never exceeds the fee.** Crediting more than an order was billed turns a
   statement into a payment, which is not what a service-level credit is and not something
   this should be able to do by arithmetic.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client_sla_term import ClientSlaTerm
from app.models.order import Order
from app.sla.commitment import delivery_commitment, terms_for_client

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Breach:
    order: Order
    promised_by: datetime
    delivered_at: datetime
    minutes_late: int
    amount_cents: int
    reason: str


@dataclass(frozen=True)
class CreditAssessment:
    breaches: list[Breach] = field(default_factory=list)
    # Orders whose lateness cannot be judged because no term is on file for their tier and
    # no explicit promise was made. Surfaced rather than swallowed - see rule 1.
    unassessable_order_ids: list[str] = field(default_factory=list)

    @property
    def total_cents(self) -> int:
        return sum(breach.amount_cents for breach in self.breaches)


def _credit_for(term: ClientSlaTerm, fee_cents: int) -> int:
    """The credit on one breached order, clamped.

    Percentage of the fee rather than a flat sum, so it scales with what was charged - a
    $12 drop and a $90 hot shot are not equally bad to miss, and one figure would
    over-credit the first and under-credit the second.
    """
    amount = round(fee_cents * term.credit_percent / 100)
    if term.credit_minimum_cents is not None:
        amount = max(amount, term.credit_minimum_cents)
    if term.credit_maximum_cents is not None:
        amount = min(amount, term.credit_maximum_cents)
    # Rule 3. A minimum written for a premium tier must not turn a cheap drop into a
    # payment out.
    return max(0, min(amount, fee_cents))


async def assess_credits(
    session: AsyncSession, *, client_id: uuid.UUID, orders: list[Order]
) -> CreditAssessment:
    """Which of these delivered orders breached their commitment, and what that costs."""
    terms = await terms_for_client(session, client_id)

    breaches: list[Breach] = []
    unassessable: list[str] = []

    for order in orders:
        if order.delivered_at is None or order.fee_cents is None:
            continue

        term = terms.get(order.sla_tier)
        # Rule 2 lives in app/sla/commitment.py now, not here. The client-facing views
        # show the same figure from the same function, which is the only way the number
        # on a statement and the number on a screen cannot drift apart - a customer owed
        # a credit could previously not see the target it was assessed against.
        commitment = delivery_commitment(order, term)
        if commitment.promised_delivery_by is None:
            unassessable.append(str(order.id))
            continue
        promised_by = commitment.promised_delivery_by

        if order.delivered_at <= promised_by:
            continue

        if term is None or term.credit_percent <= 0 and term.credit_minimum_cents is None:
            # Late against a promise, but this client's contract attaches no credit to
            # that tier. A real and common case - not every SLA has teeth - and it is a
            # breach with a zero credit rather than something to hide.
            logger.info(
                "sla_breach_without_a_credit_term",
                order_id=str(order.id),
                sla_tier=order.sla_tier,
            )
            continue

        amount = _credit_for(term, order.fee_cents)
        if amount <= 0:
            continue

        minutes_late = int((order.delivered_at - promised_by).total_seconds() // 60)
        breaches.append(
            Breach(
                order=order,
                promised_by=promised_by,
                delivered_at=order.delivered_at,
                minutes_late=minutes_late,
                amount_cents=amount,
                reason=(
                    f"{order.sla_tier} delivered {minutes_late} min late "
                    f"({term.credit_percent}% credit)"
                ),
            )
        )

    if unassessable:
        # Loud, because it means a statement is being produced for orders whose service
        # level nobody has recorded - an onboarding gap, not a billing one.
        logger.warning(
            "sla_credits_unassessable",
            client_id=str(client_id),
            order_count=len(unassessable),
            detail="no SLA term on file for these orders' tiers and no explicit promise",
        )

    return CreditAssessment(breaches=breaches, unassessable_order_ids=unassessable)
