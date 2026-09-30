"""Dwell features, built causally, and the two splits that keep them honest.

Promoted from `lmx-dwell/dwell/features.py` (`ROADMAP_1.5.md` PRD-4: *"promote
it"*), rewritten on the standard library so the harness runs wherever the tests
run, and with the analysis project's customer references left behind - a comment
there names a real account, and `CLAUDE.md`'s naming rule covers comments as
surely as it covers logs.

**The rule, again.** Every historical feature is an expanding window shifted by
one. `MODEL_AND_DATA_BRIEF.md` calls a whole-file average a build-breaking bug,
and `ml/m2/gates.py` records why a score cannot police it: the leak improves
every offline number, including the ones built to be honest. So the loop below
emits a row and *then* updates its accumulators, and a test perturbs the last
stop in the book to prove nothing earlier moved.

**Hold out first, then build (the brief's gate 5).** Built over the whole book
and split afterwards, a held-out dock's own earlier visits still fed its
history, so the "cold" test was warm for any model that reads history: on the
real export, 66 of 68 cold rows carried it. The baseline never reads history and
was scored honestly; the challenger was flattered. `build` therefore takes the
stops the model may not have seen and gives them no history and no row.

**A dock's history is shrunk before a tree sees it (gate 3).** The raw mean of
three visits speaks as loudly as the mean of three hundred. Each row also
carries its location type's history, built the same causal way, so
`shrunk_history` can pull a thin dock toward its type with the baseline's
`n / (n + k)` weight.

**A route is a route-day (gate 6).** Keyed on `route_id` alone, a placeholder id
reused across a season made one "route" of seventy-five days. The
second-precision file does not do that, but the whole-book file does, and
nothing here should depend on which file it is handed.

**One driver.** The second-precision file is a single driver's 54 manifests. The
driver-history feature that the original carried is therefore dropped rather
than computed: with one driver it is a constant, and a constant dressed as a
feature invites somebody to conclude later that driver effects were modelled and
found not to matter. They were not modelled. See `ml/real/export.py`'s verdict.
"""
from __future__ import annotations

import bisect
import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from ml.real.export import DetailStop, usable_dwell

COLDSTART_FRACTION = 0.15


@dataclass(frozen=True)
class DwellRow:
    receiver_id: str
    node_class: str
    arrived: datetime
    day: str
    # Knowable before the stop happens.
    hour: int
    day_of_week: int
    stop_seq: int
    stops_on_route: int
    minutes_into_route: float
    is_first_stop: int
    is_last_stop: int
    is_repeat_visit_today: int
    pieces: float
    revenue: float
    # Receiver history, expanding and shifted by one.
    recv_prior_n: int
    recv_prior_mean: float | None
    recv_prior_p90: float | None
    recv_days_since_last: int | None
    # The same for the receiver's location type: what a dock's own history is
    # shrunk toward, and all a dock with no history of its own has. A type with
    # no history yet borrows the whole book's.
    class_prior_mean: float | None
    class_prior_p90: float | None
    # The answer.
    dwell_min: float


@dataclass
class _Window:
    """An expanding window: what came before, in arrival order and sorted."""

    in_order: list[float] = field(default_factory=list)
    ordered: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        self.in_order.append(value)
        bisect.insort(self.ordered, value)

    @property
    def n(self) -> int:
        return len(self.in_order)

    def mean(self) -> float | None:
        return sum(self.in_order) / len(self.in_order) if self.in_order else None

    def p90(self) -> float | None:
        if not self.ordered:
            return None
        return self.ordered[min(int(0.9 * len(self.ordered)), len(self.ordered) - 1)]


def build(
    stops: list[DetailStop],
    *,
    unseen: Callable[[DetailStop], bool] | None = None,
) -> list[DwellRow]:
    """One causal pass over the book in arrival order.

    `unseen` names the stops the model may not have seen - a held-out dock's
    visits before the cut. They still count toward their route, because a
    route's size is known at dispatch whoever is on it, but they feed no history
    and produce no row, so a held-out dock enters the test the way a new
    customer's does: with nothing behind it.
    """
    usable = sorted(
        (
            (stop, stop.arrived, stop.dwell_sec)
            for stop in usable_dwell(stops)
            if stop.arrived is not None and stop.dwell_sec is not None
        ),
        key=lambda item: (item[1], item[0].stop_seq or 0),
    )

    route_size: dict[tuple[str, str], int] = {}
    route_start: dict[tuple[str, str], datetime] = {}
    route_last_seq: dict[tuple[str, str], int] = {}
    for stop, arrived, _ in usable:
        key = (stop.route_id, arrived.date().isoformat())
        route_size[key] = route_size.get(key, 0) + 1
        if key not in route_start or arrived < route_start[key]:
            route_start[key] = arrived
        route_last_seq[key] = max(route_last_seq.get(key, 0), stop.stop_seq or 0)

    receiver_history: dict[str, _Window] = {}
    class_history: dict[str, _Window] = {}
    book_history = _Window()
    last_day: dict[str, str] = {}
    seen_today: set[tuple[str, str]] = set()

    rows: list[DwellRow] = []
    for stop, arrived, dwell_sec in usable:
        if unseen is not None and unseen(stop):
            continue
        day = arrived.date().isoformat()
        key = (stop.route_id, day)
        own = receiver_history.get(stop.receiver_id, _Window())
        kind = class_history.get(stop.node_class)
        pool = kind if kind is not None else book_history
        gap = None
        if stop.receiver_id in last_day:
            previous = datetime.fromisoformat(last_day[stop.receiver_id]).date()
            gap = (arrived.date() - previous).days

        rows.append(
            DwellRow(
                receiver_id=stop.receiver_id,
                node_class=stop.node_class,
                arrived=arrived,
                day=day,
                hour=arrived.hour,
                day_of_week=arrived.weekday(),
                stop_seq=stop.stop_seq or 0,
                stops_on_route=route_size.get(key, 1),
                minutes_into_route=(arrived - route_start[key]).total_seconds() / 60.0,
                is_first_stop=int((stop.stop_seq or 0) <= 1),
                is_last_stop=int((stop.stop_seq or 0) >= route_last_seq[key]),
                is_repeat_visit_today=int((stop.receiver_id, day) in seen_today),
                pieces=stop.pieces or 0.0,
                revenue=stop.revenue or 0.0,
                recv_prior_n=own.n,
                recv_prior_mean=own.mean(),
                recv_prior_p90=own.p90(),
                recv_days_since_last=gap,
                class_prior_mean=pool.mean(),
                class_prior_p90=pool.p90(),
                dwell_min=dwell_sec / 60.0,
            )
        )

        # After the row, never before.
        minutes = dwell_sec / 60.0
        receiver_history.setdefault(stop.receiver_id, _Window()).add(minutes)
        class_history.setdefault(stop.node_class, _Window()).add(minutes)
        book_history.add(minutes)
        last_day[stop.receiver_id] = day
        seen_today.add((stop.receiver_id, day))

    return rows


def shrunk_history(
    row: DwellRow, prior_strength: float
) -> tuple[float | None, float | None]:
    """A dock's history mean and p90, pulled toward its location type.

    The weight is `n / (n + k)` - the one `baseline.py` gives a dock's own
    quantile - so the challenger trusts a thin dock exactly as little as the
    baseline does. A dock with no history gets its type's value, and with no
    history anywhere there is nothing to give.
    """
    n = row.recv_prior_n
    weight = n / (n + prior_strength) if n else 0.0

    def blend(own: float | None, pool: float | None) -> float | None:
        if own is None:
            return pool
        if pool is None:
            return own
        return weight * own + (1 - weight) * pool

    return (
        blend(row.recv_prior_mean, row.class_prior_mean),
        blend(row.recv_prior_p90, row.class_prior_p90),
    )


def chronological_cut(days: Iterable[str], test_fraction: float = 0.2) -> str:
    """The first test day: everything before it trains, everything from it tests."""
    ordered = sorted(set(days))
    return ordered[int(len(ordered) * (1 - test_fraction))]


def time_split(
    rows: list[DwellRow], test_fraction: float = 0.2, *, cut: str | None = None
):
    """Chronological. Never a random split on this data.

    `cut` is passed when it was decided before the rows existed, which is how
    the harness holds out before it builds.
    """
    if cut is None:
        cut = chronological_cut((r.day for r in rows), test_fraction)
    return [r for r in rows if r.day < cut], [r for r in rows if r.day >= cut], cut


def is_held_out(receiver_id: str, fraction: float = COLDSTART_FRACTION) -> bool:
    """Whether a dock is one the model never sees.

    By hash rather than by a seeded shuffle, so the same dock lands on the same
    side next month and two runs are comparable. The original used a numpy seed,
    which is reproducible within a run and moves the moment anybody changes it.
    """
    digest = hashlib.sha256(f"m1-coldstart:{receiver_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64) < fraction


def coldstart_split(
    train: list[DwellRow],
    test: list[DwellRow],
    holdout_fraction: float = COLDSTART_FRACTION,
):
    """Carve out receivers the model never sees.

    Returns (train without those receivers, warm test, cold test). The warm and
    cold numbers answer different questions and the brief is explicit that
    averaging them hides the only failure that matters.

    Splitting rows is not enough on its own: rows built before the split carry a
    held-out dock's history into the test. `evaluate.populations` holds out
    first and builds second, and is what the harness uses.
    """
    return (
        [r for r in train if not is_held_out(r.receiver_id, holdout_fraction)],
        [r for r in test if not is_held_out(r.receiver_id, holdout_fraction)],
        [r for r in test if is_held_out(r.receiver_id, holdout_fraction)],
    )
