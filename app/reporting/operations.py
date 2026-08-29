"""
Descriptive analytics on the ground truth we already capture (docs/ROADMAP.md I4).

**The data has been accumulating and nothing read it.** `I1` added the fields that make
operations measurable - `Stop.arrived_at`, `Stop.planned_eta`, `Order.delivered_at`,
shift transitions, driver flags - and `planned_eta` in particular was added *specifically*
so ETA accuracy could be scored. It was read in zero places. Capturing truth nobody
consumes is the same class of defect as the ones that produced it: a field served but
never written, a proof requirement never checked.

Four questions, which is what `I4` names:

  1. **Deliveries per hour**, per driver per day. The figure the peer review flagged as
     a model assumption rather than an established fact (`E9`).
  2. **Service-level hit rate by tier** - measured against the same commitment billing
     credits against, via `app/sla/commitment.py`, so a hit rate and a credit can never
     disagree about what was promised.
  3. **Hold-window effectiveness** - how often a released order drew a driver flag
     saying it was held too long or not long enough. Batching is the economic engine, so
     whether the windows are right is the question underneath it.
  4. **ETA accuracy** - `arrived_at` against `planned_eta`, which is the comparison
     `planned_eta` exists for.

**This is descriptive, and deliberately not predictive.** No baselines, no trends, no
scores. `I5` (calibration) and `I6` (predictive models) are downstream of having these
numbers at all, and a mechanism that quietly started ranking drivers would pre-empt the
trust conversation `W4` asks for - see the note on `_deliveries_per_hour`.

**Everything can refuse to answer.** With no pilot data the honest output of this module
is four `not_measured` reasons, and that is a correct result rather than a failure. A
scorecard that reports 0% on an empty table gets quoted.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import structlog
from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# The two flags that say the hold window was wrong in one direction or the other.
# Imported from the learning loop rather than restated, so a rename lands in one place -
# these strings are still that module's proposed convention pending E6's sign-off.
from app.learning_loop.detection import HOLD_TOO_LONG_FLAG, HOLD_TOO_SHORT_FLAG
from app.models.driver_shift_event import DriverShiftEvent
from app.models.order import Order, OrderStatus
from app.models.stop import Stop, StopFlag
from app.reporting.measurement import (
    Measurement,
    Rate,
    no_data,
    percentiles,
)
from app.sla.commitment import delivery_commitment, terms_for_client

logger = structlog.get_logger(__name__)

# The window every measurement covers unless a caller says otherwise. Long enough for a
# pilot's worth of pattern, short enough that a change made last week is visible rather
# than averaged into a quarter.
DEFAULT_WINDOW_DAYS = 30

# The DPH figure the financial model assumes (`E9`). Kept here beside the computation so
# a drifting assumption and a drifting measurement cannot quietly diverge - the same
# reason `lmx_link.py` keeps §3.4's targets next to its own.
ASSUMED_DELIVERIES_PER_HOUR = 2.5

# Which shift transitions start and stop the clock. `en_route` also counts as on duty: a
# driver carrying a route is working, and treating it as a gap would inflate DPH by
# shrinking the denominator - the direction of error that would make the assumption look
# validated when it was not.
_ON_DUTY = ("available", "en_route")
# There is deliberately no _OFF_DUTY list: an on-duty interval is closed by the next
# event of ANY kind. Enumerating the off-duty statuses would mean a status added later
# (`on_break` was) silently stopped closing intervals, and an unclosed interval runs to
# the next shift - inflating the denominator by hours.


@dataclass(frozen=True)
class OperationsScorecard:
    generated_at: datetime
    window_days: int
    window_start: datetime
    measurements: list[Measurement] = field(default_factory=list)
    rates: list[Rate] = field(default_factory=list)


def _tier_label(tier) -> str:
    """The tier as a plain string, whichever form it arrives in.

    **`Order.sla_tier` is not consistently one type**, which is the trap here. A row
    loaded from Postgres carries an `SLATier` member; an `Order` constructed in Python
    and not yet reloaded carries whatever string was assigned. `SLATier` subclasses str,
    so both compare and hash identically and the difference is invisible - until an
    f-string renders the member as "SLATier.T2" and ships that to an API reader as the
    tier's name.

    `.value` alone fixes the member and breaks the string. `getattr` handles both, which
    is what a value that can legitimately be either requires.
    """
    if tier is None:
        return "unspecified"
    return getattr(tier, "value", tier)


def _window_start(window_days: int, now: datetime | None = None) -> datetime:
    """UTC, always.

    Building a window from a local date against timezone-aware columns is a bug that
    only appears in the evening - it has happened here before, in the billing tests,
    where five passed all afternoon and failed after 5pm Pacific.
    """
    return (now or datetime.now(timezone.utc)) - timedelta(days=window_days)


# ---------------------------------------------------------------------------
# 1. Deliveries per hour
# ---------------------------------------------------------------------------


async def _on_duty_seconds_by_driver_day(
    session: AsyncSession, since: datetime, *, hub_id: uuid.UUID | None = None
) -> dict[tuple[uuid.UUID, date], float]:
    """How long each driver was on duty, per day, from their shift transitions.

    Pairs each on-duty event with the next event of any kind. A trailing on-duty event
    with nothing after it is a driver **still on shift**, and it is deliberately dropped
    rather than closed at `now`: counting a partial shift would divide today's handful of
    deliveries by a few minutes and produce a DPH figure that looks spectacular. The
    honest denominator is a shift that finished.

    Attributed to the day the interval *started*. A shift crossing midnight is rare in
    this operation and splitting it would complicate every reader of this number for a
    case that mostly does not happen; the choice is recorded here rather than hidden.
    """
    rows = (
        (
            await session.execute(
                select(DriverShiftEvent)
                .where(
                    DriverShiftEvent.occurred_at >= since,
                    *(
                        [DriverShiftEvent.hub_id == hub_id] if hub_id is not None else []
                    ),
                )
                .order_by(DriverShiftEvent.driver_id, DriverShiftEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )

    by_driver: dict[uuid.UUID, list[DriverShiftEvent]] = defaultdict(list)
    for row in rows:
        by_driver[row.driver_id].append(row)

    totals: dict[tuple[uuid.UUID, date], float] = defaultdict(float)
    for driver_id, events in by_driver.items():
        for current, following in zip(events, events[1:]):
            if current.event_type not in _ON_DUTY:
                continue
            seconds = (following.occurred_at - current.occurred_at).total_seconds()
            if seconds <= 0:
                continue
            totals[(driver_id, current.occurred_at.date())] += seconds
    return totals


async def _deliveries_by_driver_day(
    session: AsyncSession, since: datetime, *, hub_id: uuid.UUID | None = None
) -> dict[tuple[uuid.UUID, date], int]:
    """Completed dropoff stops per driver per day.

    Dropoffs only. A pickup is work but it is not a delivery, and counting both would
    roughly double the figure being compared against an assumption stated in deliveries.
    """
    rows = (
        await session.execute(
            select(Stop.route_id, Stop.completed_at, Stop.id)
            .where(
                Stop.stop_type == "dropoff",
                Stop.status == "completed",
                Stop.completed_at.is_not(None),
                Stop.completed_at >= since,
            )
        )
    ).all()
    if not rows:
        return {}

    # The driver is on the route, so one lookup rather than a join per stop.
    from app.models.route import Route

    route_ids = {r[0] for r in rows}
    drivers = dict(
        (
            await session.execute(
                select(Route.id, Route.driver_id).where(
                    Route.id.in_(route_ids),
                    *([Route.hub_id == hub_id] if hub_id is not None else []),
                )
            )
        ).all()
    )

    counts: dict[tuple[uuid.UUID, date], int] = defaultdict(int)
    for route_id, completed_at, _stop_id in rows:
        driver_id = drivers.get(route_id)
        if driver_id is None:
            continue
        counts[(driver_id, completed_at.date())] += 1
    return counts


async def _deliveries_per_hour(
    session: AsyncSession,
    since: datetime,
    *,
    driver_id: uuid.UUID | None = None,
    hub_id: uuid.UUID | None = None,
) -> Measurement:
    """DPH across driver-days, as a distribution.

    **A distribution rather than one number, and that is not a presentation choice.**
    `E9` asks whether 2.5 is real; a single fleet-wide average answers a different and
    easier question, because one long shift with few drops and one short shift with many
    average to something plausible while describing neither. The median driver-day is
    what an operator would recognise.

    `driver_id` narrows the same computation to one driver, which is how `W4` gets to say
    the driver sees the identical metric rather than a reduced one - there is one
    implementation and a filter, not two functions that have to be kept in step. The
    fleet-wide report deliberately never passes it: keyed by driver, this is a performance
    ranking, and that view belongs to `W4`'s trust conversation rather than arriving as a
    side effect of answering `E9`.
    """
    on_duty = await _on_duty_seconds_by_driver_day(session, since, hub_id=hub_id)
    if not on_duty:
        return no_data(
            "Deliveries per hour",
            f"{ASSUMED_DELIVERIES_PER_HOUR} assumed (E9)",
            detail="no completed shifts recorded in the window",
        )

    deliveries = await _deliveries_by_driver_day(session, since, hub_id=hub_id)

    if driver_id is not None:
        on_duty = {k: v for k, v in on_duty.items() if k[0] == driver_id}
        if not on_duty:
            return no_data(
                "Deliveries per hour",
                f"{ASSUMED_DELIVERIES_PER_HOUR} assumed (E9)",
                detail="no completed shifts recorded for you in the window",
            )

    # Only driver-days with real on-duty time. A day with deliveries and no recorded
    # shift is a data gap, not an infinite rate, and dividing by zero to get a headline
    # is exactly how a metric stops being trusted.
    rates: list[float] = []
    for key, seconds in on_duty.items():
        hours = seconds / 3600.0
        if hours < 0.25:
            # Under fifteen minutes is a toggle, not a shift.
            continue
        rates.append(deliveries.get(key, 0) / hours)

    if not rates:
        return no_data(
            "Deliveries per hour",
            f"{ASSUMED_DELIVERIES_PER_HOUR} assumed (E9)",
            detail="no shift longer than 15 minutes in the window",
        )

    rates.sort()
    median = rates[len(rates) // 2]
    p90 = rates[min(len(rates) - 1, int(len(rates) * 0.9))]
    return Measurement(
        name="Deliveries per hour",
        target=f"{ASSUMED_DELIVERIES_PER_HOUR} assumed (E9)",
        median=round(median, 2),
        p90=round(p90, 2),
        sample_size=len(rates),
        unit="deliveries/hour",
    )


# ---------------------------------------------------------------------------
# 2. Service-level hit rate, by tier
# ---------------------------------------------------------------------------


async def _sla_hit_rates(
    session: AsyncSession, since: datetime, *, client_id: uuid.UUID | None = None
) -> list[Rate]:
    """Delivered on time, by tier.

    `client_id` narrows the same computation to one client, which is how `F7` shows a
    distributor their own on-time rate. One implementation and a filter, for the same
    reason `W4`'s driver view works that way: a client's dashboard, our dashboard and
    their invoice can then never disagree about whether we were late.

    **Measured against `app/sla/commitment.py`**, which is the same function
    `app/billing/credits.py` assesses a breach against. That sharing is the point: a hit
    rate computed from its own idea of the promise could report 98% while the invoice
    credited a breach, and nobody would know which was wrong.

    An order with no commitment on file is excluded rather than counted as a hit. `W11`
    established that "nobody wrote down what we owe this client" is not a time; it is
    equally not a success, and treating it as one would make the rate improve every time
    a client was onboarded without terms.
    """
    orders = (
        (
            await session.execute(
                select(Order).where(
                    Order.status == OrderStatus.delivered,
                    Order.delivered_at.is_not(None),
                    Order.delivered_at >= since,
                    *([Order.client_id == client_id] if client_id is not None else []),
                )
            )
        )
        .scalars()
        .all()
    )
    if not orders:
        return [
            Rate(
                name="Service level hit rate (all tiers)",
                target="per client contract",
                not_measured="no delivered orders in the window",
            )
        ]

    # One terms lookup per client, not per order.
    terms_by_client: dict[uuid.UUID, dict] = {}
    hits: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    unassessable = 0

    for order in orders:
        if order.client_id is None:
            unassessable += 1
            continue
        if order.client_id not in terms_by_client:
            terms_by_client[order.client_id] = await terms_for_client(session, order.client_id)

        tier = _tier_label(order.sla_tier)
        commitment = delivery_commitment(order, terms_by_client[order.client_id].get(order.sla_tier))
        if not commitment.exists:
            unassessable += 1
            continue

        totals[tier] += 1
        if order.delivered_at <= commitment.promised_delivery_by:
            hits[tier] += 1

    rates = [
        Rate(
            name=f"Service level hit rate ({tier})",
            target="per client contract",
            numerator=hits[tier],
            denominator=totals[tier],
        )
        for tier in sorted(totals)
    ]

    if not rates:
        rates.append(
            Rate(
                name="Service level hit rate (all tiers)",
                target="per client contract",
                not_measured=(
                    f"{unassessable} delivered order(s) have no commitment on file - "
                    "no service-level terms for their tier and nothing promised explicitly"
                ),
            )
        )
    elif unassessable:
        # Named rather than folded in, so a rate cannot silently rest on a subset.
        logger.info("sla_hit_rate_partial", unassessable_orders=unassessable)
    return rates


# ---------------------------------------------------------------------------
# 3. Hold-window effectiveness
# ---------------------------------------------------------------------------


async def _hold_window_flag_rate(session: AsyncSession, since: datetime) -> Rate:
    """How often a released order drew a "held wrong" flag from the driver.

    Batching is the economic engine, so whether the windows are set right is the
    question underneath it. A driver arriving before the shop is ready
    (`hold_window_too_short`) and a shop that had been waiting (`hold_window_too_long`)
    are the two ways to be wrong, and both are already captured.

    Reported as one rate rather than split by direction, because the useful headline is
    "how often were we wrong at all". The learning loop already reads the directions
    separately to propose per-shop rule changes, which is where the split matters.

    The denominator is completed dropoffs, not flags - a rate over flags would be 100%
    by construction.
    """
    completed = (
        await session.execute(
            select(func.count())
            .select_from(Stop)
            .where(
                Stop.stop_type == "dropoff",
                Stop.status == "completed",
                Stop.completed_at >= since,
            )
        )
    ).scalar_one()

    if not completed:
        return Rate(
            name="Orders flagged as held wrong",
            target="lower is better",
            not_measured="no completed deliveries in the window",
        )

    flagged = (
        await session.execute(
            select(func.count())
            .select_from(StopFlag)
            .join(Stop, Stop.id == StopFlag.stop_id)
            .where(
                StopFlag.flag_type.in_((HOLD_TOO_SHORT_FLAG, HOLD_TOO_LONG_FLAG)),
                StopFlag.created_at >= since,
            )
        )
    ).scalar_one()

    return Rate(
        name="Orders flagged as held wrong",
        target="lower is better",
        numerator=int(flagged),
        denominator=int(completed),
    )


# ---------------------------------------------------------------------------
# 4. ETA accuracy
# ---------------------------------------------------------------------------


async def _eta_accuracy(
    session: AsyncSession,
    since: datetime,
    *,
    driver_id: uuid.UUID | None = None,
    hub_id: uuid.UUID | None = None,
) -> Measurement:
    """`arrived_at` against `planned_eta` - the comparison `planned_eta` exists for.

    **Against `planned_eta`, never `eta`.** `eta` is refreshed as the route progresses,
    so comparing an arrival against it measures the last few minutes of a route and
    reports near-perfect accuracy forever. That is precisely why the two columns are
    separate, and getting this wrong would have made the whole measurement flattering
    and useless.

    Signed, not absolute. Early and late are different operational problems - a driver
    consistently early means the model is pessimistic and the client is being quoted
    worse than reality, which is not the same failure as being late. An absolute value
    would average the two into "accurate".
    """
    delta = cast(
        func.extract("epoch", Stop.arrived_at - Stop.planned_eta), Float
    )
    where = (
        (Stop.arrived_at.is_not(None))
        & (Stop.planned_eta.is_not(None))
        & (Stop.arrived_at >= since)
    )
    if driver_id is not None or hub_id is not None:
        # Through the route, which is where both the driver and the hub live. Same
        # expression, same percentiles - only the population changes.
        from app.models.route import Route

        route_filter = select(Route.id).where(
            *([Route.driver_id == driver_id] if driver_id is not None else []),
            *([Route.hub_id == hub_id] if hub_id is not None else []),
        )
        where = where & Stop.route_id.in_(route_filter)
    median, p90, count = await percentiles(session, delta, where)
    if not count:
        return no_data(
            "ETA error (actual minus predicted)",
            "within 10 minutes",
            detail=(
                "no stop has both a planned ETA and a recorded arrival yet - "
                "planned_eta is written at offer acceptance, so this needs a route "
                "accepted and driven"
            ),
        )
    return Measurement(
        name="ETA error (actual minus predicted)",
        target="within 10 minutes",
        median=round(median / 60.0, 1) if median is not None else None,
        p90=round(p90 / 60.0, 1) if p90 is not None else None,
        sample_size=count,
        unit="minutes",
    )


# ---------------------------------------------------------------------------


async def build_operations_scorecard(
    session: AsyncSession, *, window_days: int = DEFAULT_WINDOW_DAYS, now: datetime | None = None
) -> OperationsScorecard:
    """All four measurements over one window.

    Sequential rather than concurrent: they share a session, and each is one or two
    indexed queries. Correctness over a few milliseconds on a report nobody polls.
    """
    reference = now or datetime.now(timezone.utc)
    since = _window_start(window_days, reference)

    measurements = [
        await _deliveries_per_hour(session, since),
        await _eta_accuracy(session, since),
    ]
    rates = [*await _sla_hit_rates(session, since), await _hold_window_flag_rate(session, since)]

    scorecard = OperationsScorecard(
        generated_at=reference,
        window_days=window_days,
        window_start=since,
        measurements=measurements,
        rates=rates,
    )
    logger.info(
        "operations_scorecard_built",
        window_days=window_days,
        measured=[m.name for m in measurements if m.not_measured is None],
        not_measured=[m.name for m in measurements if m.not_measured is not None],
    )
    return scorecard


# ---------------------------------------------------------------------------
# The driver's own view (docs/ROADMAP.md W4)
# ---------------------------------------------------------------------------

# How many OTHER drivers must contribute before a fleet median is shown to a driver.
#
# **This is a privacy guard between colleagues, not a statistical one.** With one other
# driver, "the fleet median" plus your own number is that person's number - so showing it
# would quietly turn a trust feature into a way to read a colleague's performance. Three
# others is where a median stops being attributable to any individual.
#
# It also happens to be where the number becomes worth reading, but that is a bonus
# rather than the reason: a thin median is misleading, whereas a two-person median is a
# disclosure.
MIN_OTHER_DRIVERS_FOR_COMPARISON = 3


@dataclass(frozen=True)
class DriverScorecard:
    """What one driver sees about their own work, beside the same figure for the fleet.

    `fleet_*` is None when there are too few colleagues for a median to be non-
    identifying - see MIN_OTHER_DRIVERS_FOR_COMPARISON. The driver's own numbers are
    always shown; it is only the comparison that can be withheld.
    """

    generated_at: datetime
    window_days: int
    window_start: datetime
    own_deliveries_per_hour: Measurement
    own_eta_error: Measurement
    fleet_deliveries_per_hour: Measurement | None
    fleet_eta_error: Measurement | None
    comparison_withheld: str | None = None


async def _other_driver_count(
    session: AsyncSession, since: datetime, *, hub_id: uuid.UUID, driver_id: uuid.UUID
) -> int:
    """How many drivers other than this one were on duty in the window."""
    on_duty = await _on_duty_seconds_by_driver_day(session, since, hub_id=hub_id)
    return len({key[0] for key in on_duty} - {driver_id})


async def build_driver_scorecard(
    session: AsyncSession,
    *,
    driver_id: uuid.UUID,
    hub_id: uuid.UUID,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> DriverScorecard:
    """A driver's own DPH and ETA accuracy, beside the same figures for their hub.

    **The same computation, not a reduced one**, which is the requirement `W4` actually
    states. `_deliveries_per_hour` and `_eta_accuracy` are the functions the operations
    scorecard calls; this passes a `driver_id` and they narrow. There is no second
    implementation to drift, and a test asserts the driver's own figure equals what the
    fleet-wide computation produces for that driver.

    **The fleet median includes this driver.** Excluding them would make the comparison
    "everyone except you", which is a subtly adversarial number and not what a shared
    standard means - and it would also be more identifying, since the driver could
    subtract their own contribution.

    **Ratings and flag rates are deliberately absent.** Both exist and both could be
    keyed by driver; showing a recipient rating back to a driver is the sharpest edge in
    this data, because a single bad rating lands hard and recipients rate for reasons
    outside a driver's control. That is a decision to take deliberately rather than by
    including everything available.

    Scoped to the driver's hub, because density and geography differ between hubs and
    comparing across them is not a shared standard - it is a comparison to a different
    job.
    """
    reference = now or datetime.now(timezone.utc)
    since = _window_start(window_days, reference)

    own_dph = await _deliveries_per_hour(session, since, driver_id=driver_id, hub_id=hub_id)
    own_eta = await _eta_accuracy(session, since, driver_id=driver_id, hub_id=hub_id)

    others = await _other_driver_count(session, since, hub_id=hub_id, driver_id=driver_id)
    if others < MIN_OTHER_DRIVERS_FOR_COMPARISON:
        logger.info(
            "driver_scorecard_comparison_withheld",
            driver_id=str(driver_id),
            other_drivers=others,
        )
        return DriverScorecard(
            generated_at=reference,
            window_days=window_days,
            window_start=since,
            own_deliveries_per_hour=own_dph,
            own_eta_error=own_eta,
            fleet_deliveries_per_hour=None,
            fleet_eta_error=None,
            comparison_withheld=(
                "Too few drivers on shift to show a team average without it pointing at "
                "one person."
            ),
        )

    return DriverScorecard(
        generated_at=reference,
        window_days=window_days,
        window_start=since,
        own_deliveries_per_hour=own_dph,
        own_eta_error=own_eta,
        fleet_deliveries_per_hour=await _deliveries_per_hour(session, since, hub_id=hub_id),
        fleet_eta_error=await _eta_accuracy(session, since, hub_id=hub_id),
    )


# ---------------------------------------------------------------------------
# The client's own view (docs/ROADMAP.md F7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientPerformance:
    """What a distributor sees about the service they are buying.

    `F7` calls this the retention proof rather than an ops nicety, and that is the right
    read: a distributor who has moved to per-drop pricing has given up their own
    visibility of a fleet they used to run, so "how are you actually doing for me" is a
    question they will ask whether or not there is a screen for it.

    Deliberately their **own** numbers and no comparison to anyone else's. A distributor
    benchmarked against other distributors would learn something about our other
    customers, and there is no version of that a client should be able to read.
    """

    generated_at: datetime
    window_days: int
    window_start: datetime
    delivered_count: int
    hit_rates: list[Rate]


async def build_client_performance(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> ClientPerformance:
    """One client's delivery volume and on-time rate.

    On-time comes from `_sla_hit_rates` narrowed to this client - the same function the
    operations report uses and, through `app/sla/commitment.py`, the same promise
    `app/billing/credits.py` assesses a breach against. So the number on a client's
    dashboard, the number on ours, and the credit on their invoice are three views of one
    computation rather than three chances to disagree.

    Volume is a plain count of delivered orders, which is the other half of what `F7`
    names. It is reported even when the hit rate cannot be - "we delivered 40 things for
    you and we cannot yet say how many were on time" is a coherent and honest answer,
    whereas suppressing both would look like an outage.
    """
    reference = now or datetime.now(timezone.utc)
    since = _window_start(window_days, reference)

    delivered = (
        await session.execute(
            select(func.count())
            .select_from(Order)
            .where(
                Order.client_id == client_id,
                Order.status == OrderStatus.delivered,
                Order.delivered_at.is_not(None),
                Order.delivered_at >= since,
            )
        )
    ).scalar_one()

    return ClientPerformance(
        generated_at=reference,
        window_days=window_days,
        window_start=since,
        delivered_count=int(delivered),
        hit_rates=await _sla_hit_rates(session, since, client_id=client_id),
    )
