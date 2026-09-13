"""
The baseline itself: what the operation did, before LMX OS decided anything.

This is the control group for W9 (docs/ROADMAP.md). The shadow-mode scorecard compares
LMX OS's parallel decisions against how the work actually ran, and "how the work
actually ran" has to come from somewhere. It comes from here - two historical exports,
reduced to a handful of numbers.

**Every metric carries its basis.** A `Metric` is not a float. It is a float plus the
denominator it came from, plus how many rows contributed, plus whatever caveat applies
to the way it was derived. `drops_per_driver_hour = 3.1` is not a finding;
`3.1 drops/hr over 412 drops and 133.0 engaged hours, hours derived from shift
clock-in/out on 94% of rows` is. The scorecard's bar for this metric is *strictly
beat*, so a baseline nobody can interrogate is a baseline nobody should bet a cutover
on.

**A metric that cannot be computed says so.** `Metric.unavailable(...)` is a first-class
result, and the reason it carries is the actionable part - usually "the export has no
such column", which tells whoever pulls the next export what to add. The alternative,
defaulting a missing metric to zero, produces a scorecard that quietly reports perfect
on-time performance for an operation that never recorded a promise time.

**Engaged hours are the subtle one.** Three sources, in descending order of trust:

  1. shift clock-in/out, taken as min/max per driver-day - unambiguous even when the
     export repeats the same shift on every stop row;
  2. a stated hours column, summed per driver-day unless every row in that day agrees,
     which means it is a repeated daily total and summing it would multiply the day by
     its stop count;
  3. the span from first to last completed drop, which is a *lower bound* - it excludes
     travel to the first stop and back from the last - and therefore biases
     drops-per-hour upward. It is flagged loudly wherever it is used, because an
     inflated baseline is the one error that makes LMX OS look worse than it is, and
     the scorecard would read as a failure to beat a number that was never real.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.baseline.loaders import ActivityRow, InvoiceRow, LoadResult

# The control group named in docs/ROADMAP.md (section G, "Baseline for measuring
# anything here"). These come from the *gig* pilot, which is a different demand path
# from a distributor's own fleet - so they are printed as orientation, never as a
# like-for-like target. See `report.py`, which labels them accordingly.
PILOT_COST_PER_JOB = 25.17
PILOT_COST_PER_MILE = 1.75
PILOT_PER_ENGAGED_HOUR = 70.74

# Hours derivation, most trustworthy first.
HOURS_FROM_SHIFT = "shift clock-in/out"
HOURS_FROM_STATED = "a stated hours column"
HOURS_FROM_SPAN = "the span of completed drops (LOWER BOUND - excludes travel to the first stop and back from the last)"


@dataclass
class Metric:
    name: str
    value: float | None
    unit: str = ""
    basis: str = ""
    contributing_rows: int = 0
    caveat: str = ""
    comparator: tuple[str, float] | None = None

    @classmethod
    def unavailable(cls, name: str, reason: str, unit: str = "") -> Metric:
        return cls(name=name, value=None, unit=unit, basis=reason)

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass
class EngagedHours:
    total: float
    source: str
    driver_days: int
    covered_days: int


@dataclass
class Baseline:
    metrics: list[Metric] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    activity: LoadResult | None = None
    invoices: LoadResult | None = None

    def by_name(self, name: str) -> Metric | None:
        return next((m for m in self.metrics if m.name == name), None)


def _driver_days(rows: list[ActivityRow]) -> dict[tuple[str, str], list[ActivityRow]]:
    groups: dict[tuple[str, str], list[ActivityRow]] = {}
    for row in rows:
        groups.setdefault((row.driver_id, row.day), []).append(row)
    return groups


def engaged_hours(rows: list[ActivityRow]) -> EngagedHours | None:
    """Total engaged hours across every driver-day, and how we got there.

    Each driver-day is resolved independently and the best available source for that
    day is used, so a file where only some drivers have shift times still produces a
    total. The reported `source` names the *weakest* source that contributed, because
    that is the one that bounds how much the total can be trusted.
    """
    groups = _driver_days(rows)
    if not groups:
        return None

    total = 0.0
    covered = 0
    used: set[str] = set()

    for members in groups.values():
        starts = [r.shift_start for r in members if r.shift_start is not None]
        ends = [r.shift_end for r in members if r.shift_end is not None]
        if starts and ends:
            span = (max(ends) - min(starts)).total_seconds() / 3600.0
            if span > 0:
                total += span
                covered += 1
                used.add(HOURS_FROM_SHIFT)
                continue

        stated = [r.hours for r in members if r.hours is not None and r.hours > 0]
        if stated:
            # All rows agreeing means the export repeats one daily total on every stop
            # row; summing it would multiply the day by its number of stops.
            day_hours = stated[0] if len(set(stated)) == 1 and len(stated) > 1 else sum(stated)
            total += day_hours
            covered += 1
            used.add(HOURS_FROM_STATED)
            continue

        if len(members) > 1:
            stamps = [r.completed_at for r in members]
            span = (max(stamps) - min(stamps)).total_seconds() / 3600.0
            if span > 0:
                total += span
                covered += 1
                used.add(HOURS_FROM_SPAN)

    if total <= 0:
        return None

    for candidate in (HOURS_FROM_SPAN, HOURS_FROM_STATED, HOURS_FROM_SHIFT):
        if candidate in used:
            source = candidate
            break
    else:
        source = "unknown"

    return EngagedHours(
        total=total, source=source, driver_days=len(groups), covered_days=covered
    )


def compute(activity: LoadResult | None, invoices: LoadResult | None) -> Baseline:
    """Reduce the two exports to the baseline metrics, with their bases attached."""
    baseline = Baseline(activity=activity, invoices=invoices)
    arows: list[ActivityRow] = list(activity.rows) if activity else []
    irows: list[InvoiceRow] = list(invoices.rows) if invoices else []

    drops = len(arows)
    billed_stops = _billed_stops(irows)
    total_charged = sum(r.amount for r in irows) if irows else 0.0

    if activity is not None:
        baseline.metrics.append(
            Metric("drops", float(drops), "drops", f"{activity.usable} usable rows of {activity.total_data_rows}", drops)
        )
        _add_hours_metrics(baseline, arows, drops)
        _add_miles_per_drop(baseline, arows, drops)
        _add_on_time(baseline, arows)
        _add_batching(baseline, arows, drops)
    if invoices is not None:
        _add_money(baseline, irows, billed_stops, total_charged)
    if activity is not None and invoices is not None:
        _add_reconciliation(baseline, arows, irows, drops, billed_stops, total_charged)

    _add_completeness(baseline, activity, invoices)
    return baseline


def _billed_stops(irows: list[InvoiceRow]) -> int:
    """Stops the invoice covers - the `stop_count` column when present, else one/row."""
    if not irows:
        return 0
    counted = [r.stop_count for r in irows if r.stop_count is not None and r.stop_count > 0]
    if len(counted) == len(irows):
        return int(sum(counted))
    return len(irows)


def _add_hours_metrics(baseline: Baseline, arows: list[ActivityRow], drops: int) -> None:
    hours = engaged_hours(arows)
    if hours is None or hours.total <= 0:
        baseline.metrics.append(
            Metric.unavailable(
                "engaged_hours",
                "no shift times, no hours column, and too few drops per driver-day to infer a span",
                "hr",
            )
        )
        baseline.metrics.append(
            Metric.unavailable("drops_per_driver_hour", "engaged hours could not be derived", "drops/hr")
        )
        return

    caveat = ""
    if hours.source == HOURS_FROM_SPAN:
        caveat = (
            "Engaged hours are a LOWER BOUND, so drops/hour is an OVERSTATEMENT of the "
            "baseline. Re-pull the activity export with shift clock-in/out before "
            "treating this as the number to beat."
        )
        baseline.warnings.append(caveat)
    if hours.covered_days < hours.driver_days:
        missed = hours.driver_days - hours.covered_days
        baseline.warnings.append(
            f"{missed} of {hours.driver_days} driver-days contributed no engaged hours "
            "and are excluded from the rate; their drops are still counted."
        )

    baseline.metrics.append(
        Metric(
            "engaged_hours",
            round(hours.total, 2),
            "hr",
            f"{hours.covered_days} of {hours.driver_days} driver-days, from {hours.source}",
            hours.covered_days,
            caveat,
        )
    )
    baseline.metrics.append(
        Metric(
            "drops_per_driver_hour",
            round(drops / hours.total, 3),
            "drops/hr",
            f"{drops} drops over {hours.total:.1f} engaged hours",
            drops,
            caveat,
        )
    )


def _add_miles_per_drop(baseline: Baseline, arows: list[ActivityRow], drops: int) -> None:
    with_miles = [r for r in arows if r.miles is not None and r.miles >= 0]
    total_miles = sum(r.miles for r in with_miles if r.miles is not None)
    if not with_miles or total_miles <= 0:
        baseline.metrics.append(
            Metric.unavailable("miles_per_drop", "the activity export carried no usable miles", "mi/drop")
        )
        return
    # The rate is computed over the rows that *have* miles, not all drops - otherwise a
    # partially-populated column reads as a genuinely shorter average trip.
    baseline.metrics.append(
        Metric(
            "miles_per_drop",
            round(total_miles / len(with_miles), 3),
            "mi/drop",
            f"{total_miles:,.0f} mi over {len(with_miles)} of {drops} drops carrying miles",
            len(with_miles),
            "" if len(with_miles) == drops else "Computed only over rows with a miles value.",
        )
    )
    baseline.metrics.append(
        Metric("total_miles", round(total_miles, 1), "mi", f"{len(with_miles)} rows", len(with_miles))
    )


def _add_on_time(baseline: Baseline, arows: list[ActivityRow]) -> None:
    comparable = [r for r in arows if r.promised_at is not None]
    if not comparable:
        baseline.metrics.append(
            Metric.unavailable(
                "on_time_rate",
                "no promise/due timestamp in the activity export - nothing to measure lateness against",
                "%",
            )
        )
        return
    on_time = sum(1 for r in comparable if r.completed_at <= r.promised_at)
    baseline.metrics.append(
        Metric(
            "on_time_rate",
            round(100.0 * on_time / len(comparable), 2),
            "%",
            f"{on_time} of {len(comparable)} drops with a promise time met it",
            len(comparable),
            "" if len(comparable) == len(arows) else
            f"Only {len(comparable)} of {len(arows)} drops carried a promise time.",
        )
    )


def _add_batching(baseline: Baseline, arows: list[ActivityRow], drops: int) -> None:
    grouped = [r for r in arows if r.route_ref]
    if not grouped:
        baseline.metrics.append(
            Metric.unavailable(
                "batch_rate",
                "no route/run/manifest column - drops cannot be grouped into trips",
                "%",
            )
        )
        return
    routes: dict[str, int] = {}
    for row in grouped:
        assert row.route_ref is not None
        routes[row.route_ref] = routes.get(row.route_ref, 0) + 1
    batched = sum(n for n in routes.values() if n > 1)
    caveat = (
        "Interpretation depends on what the route column counts. If it identifies a "
        "driver's whole day rather than a trip, this reads as near-100% and is not a "
        "batching signal - confirm the granularity before quoting it."
    )
    baseline.metrics.append(
        Metric(
            "batch_rate",
            round(100.0 * batched / len(grouped), 2),
            "%",
            f"{batched} of {len(grouped)} grouped drops sat on a multi-drop route",
            len(grouped),
            caveat,
        )
    )
    baseline.metrics.append(
        Metric(
            "drops_per_route",
            round(len(grouped) / len(routes), 3),
            "drops/route",
            f"{len(grouped)} drops across {len(routes)} distinct routes",
            len(grouped),
            caveat,
        )
    )


def _add_money(
    baseline: Baseline, irows: list[InvoiceRow], billed_stops: int, total_charged: float
) -> None:
    baseline.metrics.append(
        Metric("total_charged", round(total_charged, 2), "$", f"{len(irows)} invoice rows", len(irows))
    )
    baseline.metrics.append(
        Metric("billed_stops", float(billed_stops), "stops", f"{len(irows)} invoice rows", len(irows))
    )
    if billed_stops > 0:
        baseline.metrics.append(
            Metric(
                "cost_per_billed_stop",
                round(total_charged / billed_stops, 2),
                "$/stop",
                f"${total_charged:,.2f} over {billed_stops:,} billed stops",
                len(irows),
                comparator=("gig pilot $/job", PILOT_COST_PER_JOB),
            )
        )
    invoice_miles = sum(r.miles for r in irows if r.miles is not None and r.miles > 0)
    if invoice_miles > 0:
        baseline.metrics.append(
            Metric(
                "cost_per_billed_mile",
                round(total_charged / invoice_miles, 3),
                "$/mi",
                f"${total_charged:,.2f} over {invoice_miles:,.0f} billed miles",
                len(irows),
                comparator=("gig pilot $/mi", PILOT_COST_PER_MILE),
            )
        )


def _add_reconciliation(
    baseline: Baseline,
    arows: list[ActivityRow],
    irows: list[InvoiceRow],
    drops: int,
    billed_stops: int,
    total_charged: float,
) -> None:
    """Do the two exports describe the same work?

    Every downstream figure assumes they do. If the activity log holds work the
    invoices never billed, cost-per-drop is understated and the gap is unbilled
    delivery; the reverse means drops are missing from the operational record. Either
    way it has to surface before the numbers get quoted, not after.
    """
    hours_metric = baseline.by_name("engaged_hours")
    if hours_metric and hours_metric.available and hours_metric.value:
        baseline.metrics.append(
            Metric(
                "revenue_per_engaged_hour",
                round(total_charged / hours_metric.value, 2),
                "$/hr",
                f"${total_charged:,.2f} over {hours_metric.value:,.1f} engaged hours",
                len(irows),
                hours_metric.caveat,
                comparator=("gig pilot $/engaged hr", PILOT_PER_ENGAGED_HOUR),
            )
        )

    if drops > 0 and total_charged:
        baseline.metrics.append(
            Metric(
                "cost_per_completed_drop",
                round(total_charged / drops, 2),
                "$/drop",
                f"${total_charged:,.2f} over {drops:,} drops in the activity export",
                drops,
                "Cross-check on cost_per_billed_stop. A material gap between the two "
                "means the exports disagree about how much work happened.",
                comparator=("gig pilot $/job", PILOT_COST_PER_JOB),
            )
        )

    a_refs = {r.stop_ref for r in arows if r.stop_ref}
    i_refs = {r.stop_ref for r in irows if r.stop_ref}
    if not a_refs or not i_refs:
        baseline.metrics.append(
            Metric.unavailable(
                "stop_ref_match_rate",
                "a stop identifier is missing from one or both exports, so rows cannot be joined",
                "%",
            )
        )
        if billed_stops and drops:
            delta = drops - billed_stops
            if abs(delta) > max(1, 0.02 * max(drops, billed_stops)):
                baseline.warnings.append(
                    f"The exports disagree on volume: {drops:,} completed drops vs "
                    f"{billed_stops:,} billed stops (delta {delta:+,}). Without a shared "
                    "stop identifier this cannot be reconciled row by row."
                )
        return

    matched = a_refs & i_refs
    baseline.metrics.append(
        Metric(
            "stop_ref_match_rate",
            round(100.0 * len(matched) / len(a_refs), 2),
            "%",
            f"{len(matched):,} of {len(a_refs):,} activity stop refs appear in the invoices",
            len(matched),
        )
    )
    activity_only = len(a_refs - i_refs)
    invoice_only = len(i_refs - a_refs)
    if activity_only:
        noun = "stop was" if activity_only == 1 else "stops were"
        baseline.warnings.append(
            f"{activity_only:,} {noun} completed but never billed - cost per drop is "
            "understated by whatever they cost to serve."
        )
    if invoice_only:
        noun = "stop was" if invoice_only == 1 else "stops were"
        baseline.warnings.append(
            f"{invoice_only:,} {noun} billed but does not appear in the activity "
            "export - the operational record is incomplete."
        )


def _add_completeness(
    baseline: Baseline, activity: LoadResult | None, invoices: LoadResult | None
) -> None:
    """W9's tenth metric, computed rather than asserted.

    The denominator is every row in both files; the numerator is the rows that produced
    a usable record. This is the number that decides whether the rest of the scorecard
    is worth reading.
    """
    total = 0
    usable = 0
    for result in (activity, invoices):
        if result is not None:
            total += result.total_data_rows
            usable += result.usable
    if total == 0:
        baseline.metrics.append(Metric.unavailable("data_completeness", "no rows were read", "%"))
        return
    pct = 100.0 * usable / total
    baseline.metrics.append(
        Metric(
            "data_completeness",
            round(pct, 2),
            "%",
            f"{usable:,} of {total:,} data rows across both exports parsed into usable records",
            usable,
            "" if pct >= 99.0 else "Below 99% - see the rejected-row breakdown before trusting any rate above.",
        )
    )
    if pct < 99.0:
        baseline.warnings.append(
            f"Only {pct:.1f}% of rows were usable. Every rate above is computed over "
            "that subset."
        )
