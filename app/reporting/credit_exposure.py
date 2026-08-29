"""
What the service-level credits are actually costing us (docs/ROADMAP.md W3, E11).

`W3` made a missed commitment credit the client's statement automatically, which was the
right thing to build - a contractual credit that existed on paper and nowhere in the
system is not a commitment. What it did not come with is any way to see the total. A
credit appears on one invoice, for one client, after that invoice is generated; nobody
can ask "what are we paying out, to whom, and why".

**This exists to answer a question that is currently open.** `E11`'s row says the credit
percentages - 100/50/25/0 by tier - "encode one commercial judgement and have no
derivation at all". They are placeholders awaiting a real number, and the input that
would make that decision easy is what the placeholders have already cost. So each tier is
reported **with its configured percentage beside the money**, which is the difference
between a figure and an argument.

**Two halves, and the second is the point.**

*Issued* is history: `InvoiceCredit` rows already on statements. Easy, and by itself
misleading - it only shows what has been invoiced, so a month of breaches looks like zero
until somebody runs billing.

*Accruing* is exposure: delivered orders that have not been invoiced yet and would breach
if they were. Computed by calling `assess_credits` - the same function invoicing calls,
on the same candidate set it uses - so the number here is the number that will hit the
statement rather than a second opinion about it.

Iterates per client because `assess_credits` is per client, and service-level terms are
per client too. Fine at pilot scale and honest about not being a single query; if this
ever needs to be one, the fix is a real aggregate rather than a cached guess.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.credits import assess_credits
from app.models.client import Client
from app.models.client_sla_term import ClientSlaTerm
from app.models.invoice import Invoice
from app.models.invoice_credit import InvoiceCredit
from app.models.order import Order, OrderStatus

logger = structlog.get_logger(__name__)

DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class TierExposure:
    """Credits for one service tier, with the knob that produced them.

    `credit_percent` is here so the money and the placeholder that generated it are read
    together. A tier costing more than expected is a different conversation depending on
    whether its percentage is 100 or 25, and separating the two is how a number gets
    quoted without its cause.
    """

    sla_tier: str
    credit_percent: int | None
    credit_cents: int
    breach_count: int
    # Delivered orders in this tier over the window, so a breach count reads as a rate
    # rather than a bare number. 3 breaches out of 4 and out of 400 are different facts.
    delivered_count: int

    @property
    def breach_rate_percent(self) -> float | None:
        if not self.delivered_count:
            return None
        return round(100.0 * self.breach_count / self.delivered_count, 1)


@dataclass(frozen=True)
class ClientExposure:
    client_id: str
    client_name: str
    issued_cents: int
    accruing_cents: int

    @property
    def total_cents(self) -> int:
        return self.issued_cents + self.accruing_cents


@dataclass(frozen=True)
class CreditExposure:
    generated_at: datetime
    window_days: int
    window_start: datetime
    # Already on a statement.
    issued_cents: int
    # Delivered, not yet invoiced, and would breach if invoiced now.
    accruing_cents: int
    by_tier: list[TierExposure] = field(default_factory=list)
    by_client: list[ClientExposure] = field(default_factory=list)
    # Delivered orders whose lateness cannot be judged because no commitment is on file.
    # Surfaced rather than swallowed: `W11` established that this is not a success, and
    # here it is also not a zero-cost - it is an unknown cost.
    unassessable_orders: int = 0
    # Delivered orders with no price, which therefore cannot generate a percentage-based
    # credit. Invoicing already logs these loudly; counted here because an order that
    # will never be billed is a bigger problem than the credit it did not produce.
    unpriced_orders: int = 0

    @property
    def total_cents(self) -> int:
        return self.issued_cents + self.accruing_cents


async def build_credit_exposure(
    session: AsyncSession,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> CreditExposure:
    """Credits issued and accruing, by tier and by client."""
    reference = now or datetime.now(timezone.utc)
    since = reference - timedelta(days=window_days)

    clients = {
        row.id: row for row in (await session.execute(select(Client))).scalars().all()
    }

    # ---- issued: what is already on a statement -------------------------------
    issued_rows = (
        await session.execute(
            select(
                Invoice.client_id,
                InvoiceCredit.sla_tier,
                func.sum(InvoiceCredit.amount_cents),
                func.count(),
            )
            .join(Invoice, Invoice.id == InvoiceCredit.invoice_id)
            # By when the delivery happened, not when the invoice was cut. A window over
            # invoice dates would move every credit into whichever month billing ran,
            # which is precisely the distortion this report exists to remove.
            .where(InvoiceCredit.delivered_at >= since)
            .group_by(Invoice.client_id, InvoiceCredit.sla_tier)
        )
    ).all()

    tier_credit_cents: dict[str, int] = defaultdict(int)
    tier_breaches: dict[str, int] = defaultdict(int)
    issued_by_client: dict[uuid.UUID, int] = defaultdict(int)
    issued_total = 0
    for client_id, tier, amount, count in issued_rows:
        tier_credit_cents[tier] += int(amount)
        tier_breaches[tier] += int(count)
        issued_by_client[client_id] += int(amount)
        issued_total += int(amount)

    # ---- accruing: delivered, not yet invoiced, would breach ------------------
    accruing_by_client: dict[uuid.UUID, int] = defaultdict(int)
    accruing_total = 0
    unassessable = 0
    unpriced = 0

    for client_id in clients:
        # The same candidate set invoicing uses: delivered, in window, not yet on an
        # invoice. Priced and unpriced are separated the same way too.
        candidates = (
            (
                await session.execute(
                    select(Order).where(
                        Order.client_id == client_id,
                        Order.status == OrderStatus.delivered,
                        Order.delivered_at.is_not(None),
                        Order.delivered_at >= since,
                        Order.invoice_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not candidates:
            continue

        billable = [o for o in candidates if o.fee_cents is not None]
        unpriced += len(candidates) - len(billable)
        if not billable:
            continue

        assessment = await assess_credits(session, client_id=client_id, orders=billable)
        unassessable += len(assessment.unassessable_order_ids)
        if not assessment.breaches:
            continue

        accruing_by_client[client_id] += assessment.total_cents
        accruing_total += assessment.total_cents
        for breach in assessment.breaches:
            tier = getattr(breach.order.sla_tier, "value", breach.order.sla_tier) or "unspecified"
            tier_credit_cents[tier] += breach.amount_cents
            tier_breaches[tier] += 1

    # ---- denominators and the knob --------------------------------------------
    delivered_by_tier = {
        (getattr(tier, "value", tier) or "unspecified"): int(count)
        for tier, count in (
            await session.execute(
                select(Order.sla_tier, func.count())
                .where(
                    Order.status == OrderStatus.delivered,
                    Order.delivered_at.is_not(None),
                    Order.delivered_at >= since,
                )
                .group_by(Order.sla_tier)
            )
        ).all()
    }

    # The configured percentage per tier, so the money reads beside its cause. Distinct,
    # because it is per client: where clients disagree the report says so rather than
    # picking one, since "what does this tier cost us" has no single answer then.
    percent_rows = (
        await session.execute(
            select(ClientSlaTerm.sla_tier, ClientSlaTerm.credit_percent).distinct()
        )
    ).all()
    percents: dict[str, set[int]] = defaultdict(set)
    for tier, percent in percent_rows:
        percents[tier].add(int(percent))

    by_tier = [
        TierExposure(
            sla_tier=tier,
            credit_percent=(
                next(iter(percents[tier])) if len(percents.get(tier, ())) == 1 else None
            ),
            credit_cents=tier_credit_cents.get(tier, 0),
            breach_count=tier_breaches.get(tier, 0),
            delivered_count=delivered_by_tier.get(tier, 0),
        )
        for tier in sorted(set(tier_credit_cents) | set(delivered_by_tier))
    ]

    by_client = sorted(
        (
            ClientExposure(
                client_id=str(cid),
                client_name=clients[cid].name if cid in clients else "(unknown)",
                issued_cents=issued_by_client.get(cid, 0),
                accruing_cents=accruing_by_client.get(cid, 0),
            )
            for cid in set(issued_by_client) | set(accruing_by_client)
        ),
        key=lambda c: c.total_cents,
        reverse=True,
    )

    exposure = CreditExposure(
        generated_at=reference,
        window_days=window_days,
        window_start=since,
        issued_cents=issued_total,
        accruing_cents=accruing_total,
        by_tier=by_tier,
        by_client=by_client,
        unassessable_orders=unassessable,
        unpriced_orders=unpriced,
    )
    logger.info(
        "credit_exposure_built",
        window_days=window_days,
        issued_cents=issued_total,
        accruing_cents=accruing_total,
        unassessable_orders=unassessable,
        unpriced_orders=unpriced,
    )
    return exposure
