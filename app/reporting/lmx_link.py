"""
The LMX Link scorecard (docs/LMX_LINK_PLAN.md §3.4).

§3.4 names five success metrics. They have been unanswerable, which is the ordinary
fate of a metrics table in a plan document - the targets get quoted in updates and
nobody can say whether they are being hit. This computes the three that are real
measurements, and **reports the other two as not measured rather than substituting
something that looks like a number**, which is the part that keeps the rest
trustworthy.

  1. Time from "customer says yes" to first delivery   MEASURED (needed approved_at)
  2. Order entry time, second order onward             MEASURED (needed entry_seconds)
  3. Orders needing manual orchestrator correction     NOT MEASURED - see below
  4. Status write-back latency                         MEASURED (from F4's deliveries)
  5. Adapter changes requiring core code changes       NOT MEASURABLE from data

Computed from Postgres on request rather than exported as Prometheus counters, for
the same reason `app/health/checks.py` evaluates server-side: those counters live in
process memory, and on an autoscaled deployment they reset on cold start and differ
per instance. These are distributions over durable rows, so the answer is the same
whoever asks.

**Percentiles, not averages.** A mean entry time is dominated by the one order
somebody left open over lunch; the target is about what entry normally feels like, so
the median is the honest headline and p90 is where the tail shows up.

`Measurement` and the percentile helper now live in `app/reporting/measurement.py`,
shared with the operations scorecard - including the part that matters, which is that a
metric may refuse to answer rather than report a zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog
from sqlalchemy import Float, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.client_webhook import DELIVERY_DELIVERED, WebhookDelivery
from app.models.order import Order, OrderStatus
from app.reporting.measurement import Measurement, percentiles as _percentiles

logger = structlog.get_logger(__name__)

# §3.4's stated targets, kept next to the computation so a drifting target and a
# drifting number can't quietly diverge.
ENTRY_SECONDS_TARGET = 30
WRITE_BACK_SECONDS_TARGET = 30
ONBOARDING_HOURS_TARGET = 24  # "same day"

# Which paths involve a human typing an order. Epicor and the public API have nobody
# to time, so including them would dilute the entry-time distribution with nulls and,
# worse, make it look like it improved every time API volume grew.
_HUMAN_ENTRY_SOURCES = ("client_portal",)


@dataclass(frozen=True)
class LinkScorecard:
    generated_at: datetime
    measurements: list[Measurement] = field(default_factory=list)


async def _entry_time(session: AsyncSession) -> Measurement:
    """How long a client takes to enter an order, EXCLUDING their first.

    The exclusion is what makes this the number §3.4 targets. A client's first order
    also creates their pickup shop and teaches them the form; including it measures
    onboarding, not entry, and would make the metric look worse the more new clients
    we win - exactly backwards.
    """
    # Rank each client's orders by when they arrived, so "not their first" is
    # expressible in one pass rather than a query per client.
    ranked = (
        select(
            Order.entry_seconds.label("entry_seconds"),
            func.row_number()
            .over(partition_by=Order.client_id, order_by=Order.requested_at.asc())
            .label("order_number"),
        )
        .where(
            Order.entry_seconds.is_not(None),
            Order.source_system.in_(_HUMAN_ENTRY_SOURCES),
        )
        .subquery()
    )
    result = await session.execute(
        select(
            func.percentile_cont(0.5).within_group(ranked.c.entry_seconds.asc()),
            func.percentile_cont(0.9).within_group(ranked.c.entry_seconds.asc()),
            func.count(),
        ).where(ranked.c.order_number > 1)
    )
    median, p90, count = result.one()

    if not count:
        return Measurement(
            name="Order entry time, second order onward",
            target=f"under {ENTRY_SECONDS_TARGET}s",
            not_measured=(
                "No client has entered a second order through the portal yet. This "
                "fills in on its own with use - nothing needs building."
            ),
        )
    return Measurement(
        name="Order entry time, second order onward",
        target=f"under {ENTRY_SECONDS_TARGET}s",
        median=float(median),
        p90=float(p90) if p90 is not None else None,
        sample_size=int(count),
    )


async def _write_back_latency(session: AsyncSession) -> Measurement:
    """How long from a status change to a consumer having been told.

    Enqueue-to-delivered on `webhook_deliveries`, which is exactly the span §3.4
    means by "event to visible": the row is written in the same transaction as the
    status change, and `delivered_at` is when the consumer acknowledged.

    Only successful deliveries count. Including failures would mix "we told them in
    two seconds" with "their server was down for a day", and the second is a fact
    about their infrastructure rather than about our write-back.
    """
    seconds = cast(
        func.extract("epoch", WebhookDelivery.delivered_at - WebhookDelivery.created_at),
        Float,
    )
    median, p90, count = await _percentiles(
        session,
        seconds,
        and_(
            WebhookDelivery.status == DELIVERY_DELIVERED,
            WebhookDelivery.delivered_at.is_not(None),
        ),
    )
    if not count:
        return Measurement(
            name="Status write-back latency",
            target=f"under {WRITE_BACK_SECONDS_TARGET}s",
            not_measured=(
                "No webhook has been delivered yet. Either no client has configured "
                "an endpoint, or none of their orders has changed status."
            ),
        )
    return Measurement(
        name="Status write-back latency",
        target=f"under {WRITE_BACK_SECONDS_TARGET}s",
        median=median,
        p90=p90,
        sample_size=count,
    )


async def _onboarding_time(session: AsyncSession) -> Measurement:
    """Approval to that client's first delivered order.

    §3.4 calls this "the entire point of LMX Link" and targets same day. Measured from
    `clients.approved_at` - the moment we said yes - to the earliest `delivered_at`
    among their orders.

    Clients approved before migration 0036 have no `approved_at` and are excluded
    rather than estimated: `updated_at` would produce a plausible-looking number that
    is really "when someone last edited this row".
    """
    first_delivery = (
        select(
            Order.client_id.label("client_id"),
            func.min(Order.delivered_at).label("first_delivered_at"),
        )
        .where(Order.status == OrderStatus.delivered, Order.delivered_at.is_not(None))
        .group_by(Order.client_id)
        .subquery()
    )
    hours = cast(
        func.extract(
            "epoch", first_delivery.c.first_delivered_at - Client.approved_at
        )
        / 3600.0,
        Float,
    )
    result = await session.execute(
        select(
            func.percentile_cont(0.5).within_group(hours.asc()),
            func.percentile_cont(0.9).within_group(hours.asc()),
            func.count(),
        )
        .select_from(Client)
        .join(first_delivery, first_delivery.c.client_id == Client.id)
        .where(Client.approved_at.is_not(None))
    )
    median, p90, count = result.one()

    if not count:
        return Measurement(
            name="Approval to first delivery",
            target=f"same day (under {ONBOARDING_HOURS_TARGET}h)",
            unit="hours",
            not_measured=(
                "No client approved since this began recording has had an order "
                "delivered yet."
            ),
        )
    return Measurement(
        name="Approval to first delivery",
        target=f"same day (under {ONBOARDING_HOURS_TARGET}h)",
        unit="hours",
        median=float(median),
        p90=float(p90) if p90 is not None else None,
        sample_size=int(count),
    )


def _manual_correction_rate() -> Measurement:
    """Not measured, and saying so is the point.

    §3.4 targets "under 5% of orders requiring manual orchestrator correction" as the
    test of whether the normalizer works. Nothing records a correction: ops can edit
    an order, and that edit is indistinguishable in the data from any other update.

    Measuring it needs a deliberate signal - an ops action that says *this order
    arrived wrong* - not a heuristic over `updated_at`, which would count every
    ordinary status change as a correction and produce a number worse than no number.
    """
    return Measurement(
        name="Orders needing manual correction",
        target="under 5%",
        unit="percent",
        not_measured=(
            "Nothing records a correction. Needs an explicit ops action meaning 'this "
            "order arrived wrong' - a heuristic over updated_at would count every "
            "status change and be worse than nothing."
        ),
    )


def _adapter_coupling() -> Measurement:
    """Not a measurement at all, and it would be dishonest to render it as one.

    §3.4's "adapter changes requiring core code changes: zero" is a claim about the
    architecture, answerable by reading a diff, not by querying rows. Listed here so
    the scorecard covers all five and nobody assumes the missing one was forgotten.
    """
    return Measurement(
        name="Adapter changes requiring core changes",
        target="zero",
        unit="count",
        not_measured=(
            "A property of the code, not of the data - answerable from a diff. Two "
            "adapters have been added since the contract landed (client portal, "
            "client API) and neither changed the hold queue, optimizer or driver app."
        ),
    )


async def build_scorecard(session: AsyncSession) -> LinkScorecard:
    return LinkScorecard(
        generated_at=datetime.now(timezone.utc),
        measurements=[
            await _onboarding_time(session),
            await _entry_time(session),
            _manual_correction_rate(),
            await _write_back_latency(session),
            _adapter_coupling(),
        ],
    )
