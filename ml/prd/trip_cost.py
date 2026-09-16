"""What a drop cost us against what it was worth (`PRD-2`).

`ROADMAP_1.5.md`: *"Flags the $5.88-part-on-a-35-mile-trip class. A join and a
rule, not a model."* `MODEL_AND_DATA_BRIEF.md` §M4 agrees and is blunter - it is
not a model, it is loaded driver rate times minutes against invoice value, and
it surfaces that class of decision on day one with no training at all.

**The rate is an argument with no default.** Nothing in this repository knows
what a loaded driver-hour costs. `app/baseline/metrics.py` carries a gig-pilot
figure of $70.74 per engaged hour and labels it orientation rather than a
like-for-like target, because the gig path is not the fleet path. Defaulting to
it here would have produced a number that looked computed and was borrowed, so
whoever runs this states the rate and owns it.

**Time only. There is no distance in the export.** Not a missing column in one
file - there is no mileage anywhere in the set. So the cost here is driver time
and nothing else, which leaves out fuel, wear and the vehicle itself. Every
figure this produces is therefore a **lower bound**, and the direction matters:
it understates how unprofitable the worst drops are, which is the opposite of
the error that would embarrass us.

**Fully attributed, not marginal - and the difference is the whole batching
argument.** The travel leg to a stop is charged to that stop in full. On a
six-stop route that overstates what dropping the order would actually save,
because the truck was going that way. The honest framing is that this answers
*"should this order have been a trip"* rather than *"should we have taken this
order"*, and the second needs `M3`'s with-and-without solve. Route size is
reported alongside so the dilution is visible rather than assumed.

**A zero-revenue stop is not an infinitely bad one.** A third of the
second-precision file carries no revenue - returns, pickups, unbilled work. A
ratio would divide by zero and a filter would quietly drop a third of the book,
so they are counted and reported separately, and every ratio below is over the
stops that were billed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ml.real.export import DetailStop

# `app/baseline/metrics.py`, from the gig pilot. Available as a reference point
# and deliberately not a default: the gig path is a different demand path with a
# different cost structure, and the baseline module labels it accordingly.
GIG_PILOT_PER_ENGAGED_HOUR = 70.74


@dataclass(frozen=True)
class StopEconomics:
    receiver_id: str
    revenue: float | None
    minutes: float
    cost: float
    stops_on_route: int

    @property
    def margin(self) -> float | None:
        return None if self.revenue is None else self.revenue - self.cost

    @property
    def cost_ratio(self) -> float | None:
        """Cost as a multiple of revenue. Above 1.0 the drop lost money."""
        if not self.revenue:
            return None
        return self.cost / self.revenue


@dataclass
class TripCostReport:
    rate_per_hour: float
    billed: list[StopEconomics] = field(default_factory=list)
    unbilled: list[StopEconomics] = field(default_factory=list)

    @property
    def loss_making(self) -> list[StopEconomics]:
        return [s for s in self.billed if s.cost > (s.revenue or 0)]

    def summary(self) -> dict:
        billed = self.billed
        if not billed:
            return {"billed_stops": 0}
        revenues = sorted(s.revenue for s in billed)
        ratios = sorted(s.cost_ratio for s in billed if s.cost_ratio is not None)
        losses = self.loss_making
        return {
            "rate_per_hour": self.rate_per_hour,
            "billed_stops": len(billed),
            "unbilled_stops": len(self.unbilled),
            "median_revenue": revenues[len(revenues) // 2],
            "median_cost": sorted(s.cost for s in billed)[len(billed) // 2],
            "median_cost_ratio": ratios[len(ratios) // 2] if ratios else None,
            "loss_making_stops": len(losses),
            "loss_making_share": len(losses) / len(billed),
            "total_loss_on_those": round(sum(s.cost - s.revenue for s in losses), 2),
            # The class the roadmap names. A cheap part on a long trip.
            "cheap_part_long_trip": len(
                [s for s in billed if s.revenue < 25 and s.minutes > 30]
            ),
            "median_stops_on_route": sorted(s.stops_on_route for s in billed)[
                len(billed) // 2
            ],
            "cost_is_a_lower_bound": "no distance column in the export; driver time only",
        }

    def worst(self, count: int = 10) -> list[StopEconomics]:
        """Biggest absolute losses. Identified by receiver id, never by name."""
        return sorted(self.loss_making, key=lambda s: (s.revenue or 0) - s.cost)[:count]


def compute(stops: list[DetailStop], *, rate_per_hour: float) -> TripCostReport:
    """Join the leg to the invoice and apply the rule. No model anywhere."""
    if rate_per_hour <= 0:
        raise ValueError("a loaded driver rate is required and must be positive")

    per_route: dict[str, int] = {}
    for stop in stops:
        if stop.route_id:
            per_route[stop.route_id] = per_route.get(stop.route_id, 0) + 1

    report = TripCostReport(rate_per_hour=rate_per_hour)
    for stop in stops:
        if stop.dwell_sec is None or stop.travel_sec is None:
            continue
        minutes = (stop.dwell_sec + stop.travel_sec) / 60.0
        economics = StopEconomics(
            receiver_id=stop.receiver_id,
            revenue=stop.revenue if stop.revenue else None,
            minutes=minutes,
            cost=round(rate_per_hour * minutes / 60.0, 2),
            stops_on_route=per_route.get(stop.route_id, 1),
        )
        if economics.revenue is None:
            report.unbilled.append(economics)
        else:
            report.billed.append(economics)
    return report


def by_route_size(report: TripCostReport) -> dict:
    """The same numbers cut by how many stops shared the trip.

    The point of batching, stated as data: a drop that loses money alone stops
    losing it when three others ride along.

    **It does not cut usefully on the second-precision file**, and that is worth
    surfacing rather than presenting a table with one row in it. That file is
    one driver's day-manifests - 54 routes over 1,451 stops, about 27 stops each
    - while the whole-book file records 1,839 routes over 6,715 stops, about
    3.65 each. The same operation, two files, route sizes differing by a factor
    of seven. `DATA_NEED_BRIEF.md` names this: *"Stop counts differ between
    those two sources and are not reconciled - that is REC-5, and every figure
    here inherits it."* So does this one.
    """
    sizes = sorted(s.stops_on_route for s in report.billed)
    if not sizes:
        return {"usable": False, "reason": "no billed stops"}

    buckets: dict[str, list[StopEconomics]] = {}
    for stop in report.billed:
        if stop.stops_on_route <= 1:
            key = "alone"
        elif stop.stops_on_route <= 3:
            key = "2-3 stops"
        elif stop.stops_on_route <= 7:
            key = "4-7 stops"
        else:
            key = "8+ stops"
        buckets.setdefault(key, []).append(stop)

    # Usable only if the cut actually cuts. Checked on the buckets rather than
    # on min-and-max, because one two-stop route among nine hundred day-long
    # manifests widens the range and splits nothing.
    largest = max(len(group) for group in buckets.values())
    if largest / len(report.billed) > 0.85:
        return {
            "usable": False,
            "reason": (
                f"{largest} of {len(report.billed)} billed stops fall in one "
                "bucket, so the cut says nothing. Routes in this file are "
                f"day-manifests - median {sizes[len(sizes) // 2]} stops - while "
                "the whole-book file averages under four. See REC-5"
            ),
            "median_stops_on_route": sizes[len(sizes) // 2],
        }

    out: dict = {"usable": True}
    for key in ("alone", "2-3 stops", "4-7 stops", "8+ stops"):
        group = buckets.get(key)
        if not group:
            continue
        losses = [s for s in group if s.cost > (s.revenue or 0)]
        out[key] = {
            "stops": len(group),
            "loss_making_share": len(losses) / len(group),
            "median_cost": sorted(s.cost for s in group)[len(group) // 2],
        }
    return out
