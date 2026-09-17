"""Does every order we ingested reach an invoice? (`STL-3`)

`ROADMAP_1.5.md` STL-3: *"Billable events reconcile to ingested orders, to the
unit."* `app/billing/` generates invoices and nothing has ever checked that what
it billed matches what came in. The failure mode is the worst kind: silent,
compounding, and in whichever direction nobody is watching.

## The unit mismatch this exists to surface

Decision 0.5 sets the fee unit at **per order ingested**. `generate_invoice`
bills **per order delivered**: its candidates are `status == delivered` with a
`delivered_at` inside the period. Those are different populations, and the gap
between them is orders we took in and never charged for - a cancelled order, a
failed delivery, an order still moving when the period closed.

That is not a bug to fix here. It is a commercial question with an owner: if the
unit is genuinely per ingested order then a failed delivery is billable and
today it is free, and if it is not then 0.5 says something other than what the
invoice does. §2.3 reopens the licence alongside it, so this is live rather than
settled. What this module does is **count it**, so the decision is made against
a number rather than an impression.

## The other silence

An order priced at ingestion carries `fee_cents`; one whose tier had no
`ClientRate` configured carries null, and `generate_invoice` excludes it and
logs a warning. A log line is not a control - nobody reads warnings from three
weeks ago - so an unpriced order is a category here, with its orders listed.
Every one is revenue that was earned and never asked for.

## Direction matters

Under-billing and over-billing are both failures and only one of them gets
noticed, because the customer checks one side. So both directions are reported:
orders that should have been billed and were not, and invoice lines that cannot
be traced back to an ingest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.order import Order, OrderStatus

# What the fee unit says we charge for (decision 0.5), against what
# `generate_invoice` actually bills for. Named rather than implied so the
# mismatch is legible in the code that measures it.
FEE_UNIT = "order ingested"
BILLED_ON = "order delivered"

# Statuses an order can still reach delivery from. An order sitting in one of
# these when the period closed is not missing revenue - it is in flight, and
# counting it as a discrepancy would cry wolf every month end.
# Derived rather than listed. A hand-written set silently misclassifies any
# status added later - a new one would land in "never delivered" and read as
# lost revenue - and `en_route_drop` was already missing from the first draft of
# exactly such a list.
_TERMINAL = {
    OrderStatus.delivered,
    OrderStatus.cancelled,
    OrderStatus.delivery_failed,
    OrderStatus.returned,
}
_STILL_MOVING = {status for status in OrderStatus if status not in _TERMINAL}


@dataclass
class Reconciliation:
    client_id: object
    since: datetime
    until: datetime
    ingested: int = 0
    billed: int = 0
    # Ingested, delivered, priced - and still not on an invoice. The category
    # that is unambiguously money we earned and did not ask for.
    delivered_not_invoiced: list = field(default_factory=list)
    # Delivered with no rate configured for its tier at ingestion.
    unpriced: list = field(default_factory=list)
    # Never delivered. Free today; billable if 0.5 means what it says.
    not_delivered: list = field(default_factory=list)
    # Still moving when the period closed. Not a discrepancy.
    in_flight: int = 0
    # An invoice line with no traceable ingest. Should be impossible.
    billed_without_ingest: list = field(default_factory=list)

    @property
    def unbilled(self) -> int:
        return (
            len(self.delivered_not_invoiced)
            + len(self.unpriced)
            + len(self.not_delivered)
        )

    @property
    def reconciles(self) -> bool:
        """True only when nothing is unexplained.

        `not_delivered` counts against this deliberately. Under the stated fee
        unit those orders are billable, so a period containing them does not
        reconcile - it reveals the mismatch, which is the point.
        """
        return (
            not self.delivered_not_invoiced
            and not self.unpriced
            and not self.not_delivered
            and not self.billed_without_ingest
        )

    def summary(self) -> dict:
        return {
            "client_id": str(self.client_id),
            "fee_unit": FEE_UNIT,
            "billed_on": BILLED_ON,
            "ingested": self.ingested,
            "billed": self.billed,
            "unbilled": self.unbilled,
            "delivered_not_invoiced": len(self.delivered_not_invoiced),
            "unpriced": len(self.unpriced),
            "not_delivered": len(self.not_delivered),
            "in_flight": self.in_flight,
            "billed_without_ingest": len(self.billed_without_ingest),
            "reconciles": self.reconciles,
        }


async def reconcile_period(
    session: AsyncSession, *, client_id, since: datetime, until: datetime
) -> Reconciliation:
    """Every order ingested in the window, against what was billed for it.

    Windowed on ingestion rather than on delivery, because the unit is the
    ingest. Billing windows on `delivered_at`, which is the mismatch - an order
    taken in on the last day of a month and delivered on the first of the next
    is ingested in one period and billed in another, and only a check anchored
    to the ingest can see it at all.
    """
    orders = list(
        await session.scalars(
            select(Order).where(
                Order.client_id == client_id,
                Order.requested_at >= since,
                Order.requested_at < until,
            )
        )
    )
    report = Reconciliation(
        client_id=client_id, since=since, until=until, ingested=len(orders)
    )

    for order in orders:
        if order.invoice_id is not None:
            report.billed += 1
            continue
        if order.status == OrderStatus.delivered:
            if order.fee_cents is None:
                report.unpriced.append(order.id)
            else:
                report.delivered_not_invoiced.append(order.id)
        elif order.status in _STILL_MOVING:
            report.in_flight += 1
        else:
            # Failed, cancelled, returned - reached an end state that is not
            # delivery, so nothing bills it.
            report.not_delivered.append(order.id)

    report.billed_without_ingest = await _billed_without_ingest(
        session, client_id=client_id, since=since, until=until
    )
    return report


async def _billed_without_ingest(
    session: AsyncSession, *, client_id, since: datetime, until: datetime
) -> list:
    """Invoice lines whose order was never ingested in any window.

    A foreign key makes this close to impossible, which is why it is worth
    checking: the categories nobody expects to fire are the ones that go
    unnoticed for a year when they do.
    """
    return list(
        await session.scalars(
            select(Order.id).where(
                Order.client_id == client_id,
                Order.invoice_id.is_not(None),
                Order.requested_at.is_(None),
            )
        )
    )


async def invoiced_total_cents(
    session: AsyncSession, *, client_id, since: datetime, until: datetime
) -> int:
    """What the invoices for this window actually charged."""
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(Invoice.total_cents), 0)).where(
                Invoice.client_id == client_id,
                Invoice.period_start >= since.date(),
                Invoice.period_start < until.date(),
            )
        )
        or 0
    )


def render(report: Reconciliation) -> str:
    lines = [
        f"Billing reconciliation, client {report.client_id}",
        f"  {report.since:%Y-%m-%d} to {report.until:%Y-%m-%d}, windowed on ingestion",
        "",
        f"  ingested            {report.ingested}",
        f"  billed              {report.billed}",
        f"  still moving        {report.in_flight}  (not a discrepancy)",
        "",
    ]
    if report.reconciles:
        lines.append("  Everything ingested is accounted for.")
        return "\n".join(lines)

    lines.append("  Unaccounted for:")
    if report.delivered_not_invoiced:
        lines.append(
            f"    {len(report.delivered_not_invoiced)} delivered, priced, and on no "
            "invoice - money earned and never asked for"
        )
    if report.unpriced:
        lines.append(
            f"    {len(report.unpriced)} delivered with no rate configured for their "
            "tier at ingestion, so nothing could bill them"
        )
    if report.not_delivered:
        lines.append(
            f"    {len(report.not_delivered)} never delivered. Free today, and "
            f"billable if the fee unit is the {FEE_UNIT!r} that 0.5 says it is - "
            f"the invoice bills per {BILLED_ON!r}. That gap is a commercial "
            "decision rather than a defect, and this is the number to make it on"
        )
    if report.billed_without_ingest:
        lines.append(
            f"    {len(report.billed_without_ingest)} billed with no traceable "
            "ingest, which should not be possible"
        )
    return "\n".join(lines)
