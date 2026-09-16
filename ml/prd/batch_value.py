"""How much a longer hold buys, by what kind of place is waiting (`PRD-1`).

`ROADMAP_1.5.md` PRD-1 is done when it *"reproduces the sensitivity finding:
+3-7% at high-frequency shops, +195-271% at warehouse and transfer nodes."* The
gap between those is two orders of magnitude, and `MODEL_AND_DATA_BRIEF.md` §M3
rests `IDN-3` on it - node class is "a modelling prerequisite, not metadata"
because a batching policy that cannot tell a warehouse from a body shop is
averaging across that gap and is wrong at both ends.

**The export cannot test the half the claim rests on.** Classified by account
name, the design partner's 229 receivers contain **one** warehouse and **no**
transfer nodes, and the file's own `Transfer` flag is false on all 6,715 rows.
So the +195-271% end is not reproducible here, not because the analysis is
missing but because the nodes are. The shop end is testable - 82 receivers,
3,455 stops - and this computes it.

Saying so is the deliverable. A roadmap item defined by reproducing a figure
whose supporting data we do not hold should be visibly blocked rather than
quietly reported against the two classes that happened to be present.

**There is no recorded hold to measure, so the hold is simulated.** Half the
book was created after its route had already left - in-flight insertion, the
50.2% the roadmap names as the ceiling - and the rest shows dispatch lag rather
than a deliberate wait. The counterfactual is the only route to the number:
take each order's creation time and its destination, and ask what would have
ridden together under a hold of W minutes.

**A sweep, not time buckets.** Fixed buckets of width W split orders that
arrived four minutes apart across a boundary and report them as unbatchable,
which understates every window by an artifact of where the clock started. The
sweep opens a batch at the first unassigned order and absorbs everything to the
same place within W of it - which is what a hold policy actually does.

**Attribution is by the order's own destination**, which settles a question
`DATA_NEED_BRIEF.md` §4.4 note 3 leaves open: a commingle event is a property of
a customer *pair* while node class is a property of a *dock*, and the brief
notes nobody has defined which dock's class such an event belongs to. Measuring
per order rather than per pair makes it unambiguous - this order went to a
shop, and it rode with n others - at the cost of not being a statement about
pairs. It is the statement the hold-window policy needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ml.real.export import TimingStop

# The two windows the finding compares. `app/sla/engine.py` runs T2 at 90
# minutes, so this is not a hypothetical pair of numbers - it is the tier we
# operate against and the shorter window somebody will propose instead.
BASELINE_WINDOW_MINUTES = 45
EXTENDED_WINDOW_MINUTES = 90

# Windows worth seeing the whole curve across. A curve makes the shape visible;
# two points make it look linear, and §4.4 is explicit that these curves
# saturate.
CURVE_WINDOWS = (0, 15, 30, 45, 60, 90, 120, 180)


# "Buys +X%" is not defined in the source, and the reading changes the answer by
# an order of magnitude - so all three are computed and the finding is checked
# against each. Picking one and reporting it would have been a choice disguised
# as a measurement.
MEASURE_BATCH_SIZE = "mean_batch_size"
MEASURE_SHARE_BATCHED = "share_batched"
MEASURE_ROUTES = "routes_needed"
MEASURES = (MEASURE_BATCH_SIZE, MEASURE_SHARE_BATCHED, MEASURE_ROUTES)

# Routes shrink as batching improves, so a "gain" there is a negative number.
_LOWER_IS_BETTER = {MEASURE_ROUTES}


@dataclass
class ClassResult:
    node_class: str
    orders: int
    receivers: int = 0
    # measure -> window minutes -> value
    curve: dict[str, dict[int, float]] = field(default_factory=dict)

    def at(self, measure: str, window: int) -> float | None:
        return self.curve.get(measure, {}).get(window)

    def lift(
        self,
        measure: str = MEASURE_BATCH_SIZE,
        short: int = BASELINE_WINDOW_MINUTES,
        long: int = EXTENDED_WINDOW_MINUTES,
    ) -> float | None:
        """Percentage change from the shorter hold to the longer one."""
        a, b = self.at(measure, short), self.at(measure, long)
        if a in (None, 0) or b is None:
            return None
        change = (b / a - 1.0) * 100.0
        return -change if measure in _LOWER_IS_BETTER else change


def _batch_sizes(stops: list[TimingStop], window_minutes: int) -> dict[int, int]:
    """Sweep each destination's arrivals, returning batch size per stop index.

    Grouped by postcode rather than by receiver: two orders to different docks
    in the same postcode are the batch that matters, and grouping by receiver
    would only ever find repeat visits to one dock.
    """
    window = window_minutes * 60
    by_zip: dict[str, list[int]] = {}
    for index, stop in enumerate(stops):
        if stop.created is None or not stop.zip_code:
            continue
        by_zip.setdefault(stop.zip_code, []).append(index)

    sizes: dict[int, int] = {}
    for indices in by_zip.values():
        indices.sort(key=lambda i: stops[i].created)
        position = 0
        while position < len(indices):
            opener = stops[indices[position]].created
            end = position
            while (
                end + 1 < len(indices)
                and (stops[indices[end + 1]].created - opener).total_seconds() <= window
            ):
                end += 1
            size = end - position + 1
            for i in indices[position : end + 1]:
                sizes[i] = size
            position = end + 1
    return sizes


def batch_value_by_node_class(
    stops: list[TimingStop], windows: tuple[int, ...] = CURVE_WINDOWS
) -> dict[str, ClassResult]:
    """The hold-window curve, three ways, split by node class.

    `mean_batch_size`  orders riding together. The intuitive reading.
    `share_batched`    orders that ride with anyone at all. The one that shows
                       the mechanism: a class already batched at the shorter
                       window has little left to gain, a class barely batched at
                       all has everything to gain, and that ratio is where a
                       two-orders-of-magnitude spread comes from.
    `routes_needed`    trucks, which is what the saving is actually made of.
                       Reported as a gain, so a positive number is fewer routes.
    """
    eligible = [s for s in stops if s.created is not None and s.zip_code]
    results: dict[str, ClassResult] = {}
    seen: dict[str, set[str]] = {}
    for stop in eligible:
        result = results.setdefault(
            stop.node_class, ClassResult(stop.node_class, 0)
        )
        result.orders += 1
        seen.setdefault(stop.node_class, set()).add(stop.receiver_id)
    for node_class, receivers in seen.items():
        results[node_class].receivers = len(receivers)

    for window in windows:
        sizes = _batch_sizes(eligible, window)
        grouped: dict[str, list[int]] = {}
        for index, stop in enumerate(eligible):
            grouped.setdefault(stop.node_class, []).append(sizes.get(index, 1))
        for node_class, values in grouped.items():
            curve = results[node_class].curve
            curve.setdefault(MEASURE_BATCH_SIZE, {})[window] = sum(values) / len(values)
            curve.setdefault(MEASURE_SHARE_BATCHED, {})[window] = sum(
                1 for v in values if v > 1
            ) / len(values)
            # Each order contributes 1/size of a route, so this sums to the
            # number of routes those orders would occupy.
            curve.setdefault(MEASURE_ROUTES, {})[window] = sum(1 / v for v in values)
    return results


@dataclass(frozen=True)
class Reproduction:
    """Whether the finding PRD-1 is defined by can be checked, and by which
    reading of it."""

    node_class: str
    stated_low: float
    stated_high: float
    receivers: int
    measured: dict[str, float | None]
    reproducing_measures: tuple[str, ...]
    verdict: str


# `MODEL_AND_DATA_BRIEF.md` §M3, as quoted in `ROADMAP_1.5.md` PRD-1.
STATED_FINDING: dict[str, tuple[float, float]] = {
    "shop": (3.0, 7.0),
    "warehouse": (195.0, 271.0),
    "transfer": (195.0, 271.0),
}

# Below this, a class is a handful of docks and any percentage computed from it
# is noise with a decimal point.
MIN_RECEIVERS_TO_TEST = 5


def check_the_stated_finding(
    results: dict[str, ClassResult],
) -> list[Reproduction]:
    """Hold the measurement against what the roadmap says it should reproduce.

    Every row reports, including the ones that cannot be computed. A table that
    silently omitted warehouse and transfer would read as though the finding had
    been confirmed.
    """
    out = []
    for node_class, (low, high) in STATED_FINDING.items():
        result = results.get(node_class)
        receivers = result.receivers if result else 0
        measured = {
            measure: result.lift(measure) if result else None for measure in MEASURES
        }
        reproducing = tuple(
            measure
            for measure, value in measured.items()
            if value is not None and low <= value <= high
        )
        if receivers < MIN_RECEIVERS_TO_TEST:
            verdict = (
                f"not testable - {receivers} receiver(s) of this class in the "
                "export, so the half of the finding that carries the claim is "
                "unsupported here rather than refuted"
            )
        elif reproducing:
            verdict = f"reproduced under {', '.join(reproducing)}"
        else:
            shown = ", ".join(
                f"{m}={v:+.1f}%" for m, v in measured.items() if v is not None
            )
            verdict = (
                f"NOT reproduced under any reading (stated {low:+.0f}%..{high:+.0f}%; "
                f"measured {shown})"
            )
        out.append(
            Reproduction(
                node_class, low, high, receivers, measured, reproducing, verdict
            )
        )
    return out


def where_the_spread_actually_is(
    results: dict[str, ClassResult], *, measure: str = MEASURE_SHARE_BATCHED
) -> list[tuple[str, float, float]]:
    """The classes with the most and least to gain, whatever they are called.

    The finding's mechanism is real even where its labels do not apply: a node
    type barely batched at the shorter window gains enormously from a longer
    one, and a node type already batched gains little. Returning
    (class, starting share, lift) lets that be read off directly rather than
    inferred from a class name that this export happens not to contain.
    """
    rows = []
    for result in results.values():
        if result.receivers < MIN_RECEIVERS_TO_TEST:
            continue
        start = result.at(measure, BASELINE_WINDOW_MINUTES)
        lift = result.lift(measure)
        if start is None or lift is None:
            continue
        rows.append((result.node_class, start, lift))
    return sorted(rows, key=lambda row: -row[2])
