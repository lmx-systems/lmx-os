"""Where LMX OS and the live operation disagreed, and what can be said about it.

`app/shadow/recorder.py` writes down what the optimizer would have decided and
stops there, deliberately:

> *"What this deliberately does not do: compare. Divergence is computed later, by
> joining these rows against what actually happened to the order."*

This is later. It is the second half of `DEC-0`, whose done-when is *"a full day
produces a plan and a recorded delta with zero operational change"* - a delta,
note, not a saving.

## The distinction this module exists to hold

A shadow plan is a **decision**. A delivery is an **outcome**. The shadow plan
was never executed, so it produced no outcomes at all - and that fact governs
what may be computed here.

**Decisions can be compared, because both sides are real.** LMX OS would have
given order X to driver D at 09:04; the operation gave it to driver E at 09:41.
Both of those happened. The disagreement is a fact, the 37 minutes is a fact,
and the shape of each plan - how many orders per driver, how many the solver
could place - is a fact on both sides.

**Outcomes cannot.** "Our plan would have delivered it eleven minutes sooner" is
not a measurement. It is our own solver's ETA estimate held against somebody
else's realised execution, which compares our optimism to their traffic. Every
error in the estimate lands on our side of the ledger, in our favour, and
nothing in the data can catch it. So this module **refuses** to produce a
cost-per-drop delta, an on-time delta or a savings figure from shadow rows, and
says why in the report rather than omitting the row - an absent metric reads as
an oversight and a refused one reads as a boundary.

`ROADMAP_1.5.md` already says this about the weaker cousin: `EXP-0`, the
historical baseline, is *"strictly weaker than EXP-1 and superseded by it - a
historical comparison is confounded by season, mix and volume, so this sizes a
prospect and seeds STL-2; it is not the counterfactual a savings statement rests
on."* Shadow mode is confounded in the same way and one worse, because its half
of the comparison never happened.

**What closes the gap is `EXP-1`.** A control arm produces two populations that
did both happen, randomised, so the difference between them is caused rather
than correlated. Divergence tells you the two systems disagree and how often;
the arm tells you which one was right. Stratifying this report by arm is the
follow-on once assignments exist - today nothing is enrolled, because the arm
ships off until a customer's contract records the clause.

## Grading, not counting

A disagreement about which driver is a different thing from a disagreement about
what order to visit in. `ShadowOrderDecision.sequence_index` exists for exactly
this - its own docstring says a divergence should be *"graded rather than just
counted"* - so the classes below run from full agreement to each system placing
an order the other would not have.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.route import Route
from app.models.shadow_decision import ShadowDecision, ShadowOrderDecision
from app.models.stop import Stop, StopOrder
from app.reporting.measurement import Measurement, Rate

# Ordered from agreement to disagreement. The string values are stable because
# STL-2 will version a baseline definition against them.
AGREED = "agreed"
DIFFERENT_SEQUENCE = "different_sequence"
DIFFERENT_DRIVER = "different_driver"
SHADOW_WOULD_HAVE_PLACED = "shadow_would_have_placed"
ONLY_THE_OPERATION_PLACED = "only_the_operation_placed"
NEITHER_PLACED = "neither_placed"

DIVERGENCE_CLASSES = (
    AGREED,
    DIFFERENT_SEQUENCE,
    DIFFERENT_DRIVER,
    SHADOW_WOULD_HAVE_PLACED,
    ONLY_THE_OPERATION_PLACED,
    NEITHER_PLACED,
)

# Metrics this module will not compute from shadow rows, and the reason each one
# is refused. Carried as data so the report can print them beside the metrics it
# does produce: a reader who cannot find "cost per drop" should find the
# sentence explaining why instead of assuming nobody got to it.
REFUSED: dict[str, str] = {
    "cost_per_drop_delta": (
        "the shadow plan was never driven, so its cost is our own solver's "
        "estimate. Comparing an estimate to a realised cost puts every "
        "optimism error on our side of the ledger. EXP-1's control arm is the "
        "measurement; this is not a weaker version of it"
    ),
    "on_time_delta": (
        "same reason. A shadow assignment has no arrival, only a predicted one, "
        "and an ETA compared against somebody else's traffic is not a result"
    ),
    "savings": (
        "a savings figure needs two populations that both happened. Shadow mode "
        "supplies one. STL-1 renders this from EXP-1, not from here"
    ),
}


@dataclass(frozen=True)
class OrderDivergence:
    """One order, as each system handled it."""

    order_id: object
    planned_at: datetime
    divergence: str
    shadow_driver_id: object | None
    real_driver_id: object | None
    shadow_sequence: int | None
    real_sequence: int | None
    sla_tier: str | None
    # Positive means LMX OS would have committed the order this many seconds
    # before the operation did. Both timestamps are decisions, so this is a
    # comparison rather than a projection - which is why it is the one timing
    # number in the report.
    dispatch_lead_seconds: float | None


@dataclass
class DivergenceReport:
    hub_id: object
    since: datetime
    until: datetime
    orders: list[OrderDivergence] = field(default_factory=list)
    cycles: int = 0
    engines: dict[str, int] = field(default_factory=dict)
    metrics: list[Measurement | Rate] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=lambda: dict(REFUSED))

    def counts(self) -> dict[str, int]:
        return {
            name: sum(1 for o in self.orders if o.divergence == name)
            for name in DIVERGENCE_CLASSES
        }

    @property
    def comparable(self) -> list[OrderDivergence]:
        """Orders both systems placed. The only ones a driver comparison means
        anything for."""
        return [
            o
            for o in self.orders
            if o.divergence in (AGREED, DIFFERENT_SEQUENCE, DIFFERENT_DRIVER)
        ]

    def agreement_rate(self) -> Rate:
        comparable = self.comparable
        if not comparable:
            return Rate(
                name="driver agreement",
                target="no target - this is a description, not a goal",
                not_measured="no order was placed by both systems in this window",
            )
        return Rate(
            name="driver agreement",
            target="no target - this is a description, not a goal",
            numerator=sum(
                1 for o in comparable if o.divergence in (AGREED, DIFFERENT_SEQUENCE)
            ),
            denominator=len(comparable),
        )


def _grade(
    shadow: ShadowOrderDecision, real_driver_id, real_sequence: int | None
) -> str:
    if shadow.decision == "unassigned":
        return ONLY_THE_OPERATION_PLACED if real_driver_id else NEITHER_PLACED
    if real_driver_id is None:
        return SHADOW_WOULD_HAVE_PLACED
    if str(shadow.driver_id) != str(real_driver_id):
        return DIFFERENT_DRIVER
    if (
        shadow.sequence_index is not None
        and real_sequence is not None
        and shadow.sequence_index != real_sequence
    ):
        return DIFFERENT_SEQUENCE
    return AGREED


async def _real_placements(session: AsyncSession, order_ids: list) -> dict:
    """What actually happened to each order: driver, position, commit time.

    **`Stop.sequence` is not comparable to `sequence_index` and using it
    directly would break every grade.** A real route numbers stops from 1 and
    puts two on the board for each order - a pickup at *n* and a dropoff at
    *n+1* (`app/optimizer/service.py`). A shadow decision records a zero-based
    position in that driver's list of *orders*. Compared as they are, the first
    order on a route is real sequence 1 against shadow index 0, and every single
    order reads as a sequence disagreement.

    So the real position is the order's rank among its route's **dropoffs**,
    zero-based, which is the same quantity the shadow index is. Dropoffs because
    that is the delivery; a pickup is how the parcel got on the van.

    Even corrected, sequence agreement is the weakest signal in the report: the
    two plans hold different sets of orders, so matching ranks is close to
    coincidence. It is graded because `ShadowOrderDecision.sequence_index` exists
    to let a divergence be *"graded rather than just counted"*, and it folds into
    the same side as full agreement when the driver matches.
    """
    if not order_ids:
        return {}
    rows = (
        await session.execute(
            select(
                StopOrder.order_id,
                Stop.route_id,
                Stop.id,
                Stop.stop_type,
                Stop.sequence,
                Route.driver_id,
                Order.assigned_at,
            )
            .join(Stop, Stop.id == StopOrder.stop_id)
            .join(Route, Route.id == Stop.route_id)
            .join(Order, Order.id == StopOrder.order_id)
            .where(StopOrder.order_id.in_(order_ids))
            .order_by(StopOrder.order_id, Stop.sequence)
        )
    ).all()
    if not rows:
        return {}

    route_ids = {row[1] for row in rows}
    dropoffs = (
        await session.execute(
            select(Stop.route_id, Stop.id)
            .where(Stop.route_id.in_(route_ids), Stop.stop_type == "dropoff")
            .order_by(Stop.route_id, Stop.sequence)
        )
    ).all()
    rank_of_stop: dict = {}
    seen: dict = {}
    for route_id, stop_id in dropoffs:
        rank_of_stop[stop_id] = seen.get(route_id, 0)
        seen[route_id] = seen.get(route_id, 0) + 1

    placements: dict = {}
    for order_id, _route_id, stop_id, stop_type, _sequence, driver_id, assigned_at in rows:
        existing = placements.get(order_id)
        # The dropoff wins if the order has both. Otherwise the first stop seen,
        # which keeps an order that somehow has only a pickup from vanishing.
        if existing is not None and existing[3] == "dropoff":
            continue
        placements[order_id] = (
            driver_id,
            rank_of_stop.get(stop_id),
            assigned_at,
            stop_type,
        )
    return {k: (v[0], v[1], v[2]) for k, v in placements.items()}


async def compute_divergence(
    session: AsyncSession, *, hub_id, since: datetime, until: datetime
) -> DivergenceReport:
    """Join a window of shadow decisions against what the operation did.

    **The earliest shadow decision per order wins.** A held order appears in
    every cycle until it is released, and the question DEC-0 asks is "when would
    LMX OS have committed this" - which is the first cycle that said assign, not
    the last. Taking the latest would flatter the lead time by discarding every
    cycle where we would have moved and the operation had not yet.
    """
    cycles = (
        await session.scalars(
            select(ShadowDecision).where(
                ShadowDecision.hub_id == hub_id,
                ShadowDecision.planned_at >= since,
                ShadowDecision.planned_at < until,
            )
        )
    ).all()
    report = DivergenceReport(hub_id=hub_id, since=since, until=until)
    report.cycles = len(cycles)
    for cycle in cycles:
        report.engines[cycle.engine] = report.engines.get(cycle.engine, 0) + 1
    if not cycles:
        return report

    decisions = (
        await session.scalars(
            select(ShadowOrderDecision)
            .where(ShadowOrderDecision.shadow_decision_id.in_([c.id for c in cycles]))
            .order_by(ShadowOrderDecision.planned_at)
        )
    ).all()

    first: dict = {}
    for decision in decisions:
        existing = first.get(decision.order_id)
        # An "assigned" decision beats an earlier "unassigned" one: the question
        # is when we would first have committed the order, and a cycle that
        # could not place it is not a commitment.
        if existing is None or (
            existing.decision == "unassigned" and decision.decision == "assigned"
        ):
            first[decision.order_id] = decision

    placements = await _real_placements(session, list(first))

    for order_id, decision in first.items():
        real_driver_id, real_sequence, real_assigned_at = placements.get(
            order_id, (None, None, None)
        )
        lead = None
        if real_assigned_at is not None and decision.decision == "assigned":
            lead = (real_assigned_at - decision.planned_at).total_seconds()
        report.orders.append(
            OrderDivergence(
                order_id=order_id,
                planned_at=decision.planned_at,
                divergence=_grade(decision, real_driver_id, real_sequence),
                shadow_driver_id=decision.driver_id,
                real_driver_id=real_driver_id,
                shadow_sequence=decision.sequence_index,
                real_sequence=real_sequence,
                sla_tier=decision.sla_tier,
                dispatch_lead_seconds=lead,
            )
        )

    report.metrics = _plan_shape(report, cycles)
    return report


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(int(q * len(ordered)), len(ordered) - 1)]


def _plan_shape(
    report: DivergenceReport, cycles: list[ShadowDecision]
) -> list[Measurement | Rate]:
    """The metrics that are comparisons of decisions rather than of outcomes.

    Three, and each is a fact on both sides of the comparison:

    `dispatch lead` - how much earlier LMX OS would have committed an order.
    Two timestamps, both real. Note the sign convention: positive means earlier,
    and a negative median would mean the human dispatcher was faster, which
    `ROADMAP_1.5.md` Phase 3 warns is the likely finding - *"50.2% of orders
    already get in-flight insertion by hand, and those reach customers faster."*

    `orders per planned route` - the batching claim, as the solver expressed it.
    Still a decision rather than a result: it says the plan grouped work, not
    that the grouping held up on the road.

    `placement rate` - how often the solver could place a released order at all.
    A low rate is a finding about the optimizer, not about the operation, and it
    caps how much the rest of the report can mean.
    """
    leads = [
        o.dispatch_lead_seconds
        for o in report.orders
        if o.dispatch_lead_seconds is not None
    ]
    metrics: list[Measurement | Rate] = []

    if leads:
        metrics.append(
            Measurement(
                name="dispatch lead over the operation",
                target="positive means LMX OS would have committed sooner",
                median=_median(leads),
                p90=_percentile(leads, 0.9),
                sample_size=len(leads),
                unit="seconds",
            )
        )
    else:
        metrics.append(
            Measurement(
                name="dispatch lead over the operation",
                target="positive means LMX OS would have committed sooner",
                not_measured=(
                    "no order in this window was both assigned in shadow and "
                    "committed by the operation"
                ),
            )
        )

    per_route: list[float] = []
    for cycle in cycles:
        drivers: dict = {}
        for entry in cycle.assignment_payload or []:
            driver = entry.get("driver_id")
            # `stop_ids` is the recorder's name for them and they are order ids -
            # `record_plan` does `order_id=uuid.UUID(stop_id)` on the same values.
            # Reading the key by its real meaning rather than renaming it, because
            # rows already written carry this shape.
            orders = entry.get("stop_ids") or []
            if driver is not None:
                drivers[driver] = drivers.get(driver, 0) + len(orders)
        per_route.extend(float(count) for count in drivers.values() if count)
    if per_route:
        metrics.append(
            Measurement(
                name="orders per planned route",
                target="DEC-2's measured stops-per-route improvement",
                median=_median(per_route),
                p90=_percentile(per_route, 0.9),
                sample_size=len(per_route),
                unit="orders",
            )
        )
    else:
        metrics.append(
            Measurement(
                name="orders per planned route",
                target="DEC-2's measured stops-per-route improvement",
                not_measured="no cycle in this window produced an assignment",
            )
        )

    assigned = sum(c.assigned_order_count for c in cycles)
    unassigned = sum(c.unassigned_order_count for c in cycles)
    metrics.append(
        Rate(
            name="orders the solver could place",
            target="a low rate caps what the rest of this report can mean",
            numerator=assigned,
            denominator=assigned + unassigned,
            not_measured=(
                None
                if assigned + unassigned
                else "no order was released from the hold queue in this window"
            ),
        )
    )
    return metrics


def render(report: DivergenceReport) -> str:
    """The report as text. Refusals print alongside the metrics, by design."""
    lines = [
        f"Shadow divergence, hub {report.hub_id}",
        f"  {report.since:%Y-%m-%d %H:%M} to {report.until:%Y-%m-%d %H:%M}",
        f"  {report.cycles} cycles, {len(report.orders)} orders",
    ]
    if report.engines:
        engines = ", ".join(f"{k}={v}" for k, v in sorted(report.engines.items()))
        lines.append(f"  solver: {engines}")
        # Substring, not equality: the stub identifies itself as
        # `stub_nearest_neighbor`, so an exact match silently skipped the
        # warning in precisely the case it exists for.
        if any("stub" in k for k in report.engines):
            lines.append(
                "  NOTE: some or all cycles ran on the stub router. A stub plan "
                "and a Google plan are not comparable evidence (DEC-3)."
            )

    lines.append("\n  how the two systems compared, per order")
    counts = report.counts()
    total = len(report.orders) or 1
    for name in DIVERGENCE_CLASSES:
        lines.append(f"    {name:28} {counts[name]:5}  {counts[name] / total:6.1%}")

    agreement = report.agreement_rate()
    if agreement.not_measured:
        lines.append(f"\n  driver agreement: {agreement.not_measured}")
    else:
        lines.append(
            f"\n  driver agreement: {agreement.percentage}% "
            f"({agreement.numerator}/{agreement.denominator})"
            + ("  [thin sample]" if agreement.is_thin else "")
        )

    lines.append("\n  decision comparisons")
    for metric in report.metrics:
        if isinstance(metric, Rate):
            if metric.not_measured:
                lines.append(f"    {metric.name:32} not measured: {metric.not_measured}")
            else:
                lines.append(
                    f"    {metric.name:32} {metric.percentage}% "
                    f"({metric.numerator}/{metric.denominator})"
                )
        elif metric.not_measured:
            lines.append(f"    {metric.name:32} not measured: {metric.not_measured}")
        else:
            lines.append(
                f"    {metric.name:32} median {metric.median:.1f} {metric.unit}, "
                f"p90 {metric.p90:.1f} (n={metric.sample_size})"
            )

    lines.append("\n  not computed from shadow data, and why")
    for name, reason in report.refused.items():
        lines.append(f"    {name}")
        lines.append(f"      {reason}")
    return "\n".join(lines)
