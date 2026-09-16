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

**One driver.** The second-precision file is a single driver's 54 manifests. The
driver-history feature that the original carried is therefore dropped rather
than computed: with one driver it is a constant, and a constant dressed as a
feature invites somebody to conclude later that driver effects were modelled and
found not to matter. They were not modelled. See `ml/real/export.py`'s verdict.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from ml.real.export import DetailStop, usable_dwell


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
    # The answer.
    dwell_min: float


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(int(0.9 * len(ordered)), len(ordered) - 1)]


def build(stops: list[DetailStop]) -> list[DwellRow]:
    """One causal pass over the book in arrival order."""
    usable = sorted(usable_dwell(stops), key=lambda s: (s.arrived, s.stop_seq or 0))

    route_size: dict[str, int] = {}
    route_start: dict[str, datetime] = {}
    route_last_seq: dict[str, int] = {}
    for stop in usable:
        route_size[stop.route_id] = route_size.get(stop.route_id, 0) + 1
        if stop.route_id not in route_start or stop.arrived < route_start[stop.route_id]:
            route_start[stop.route_id] = stop.arrived
        route_last_seq[stop.route_id] = max(
            route_last_seq.get(stop.route_id, 0), stop.stop_seq or 0
        )

    history: dict[str, list[float]] = {}
    last_day: dict[str, str] = {}
    seen_today: set[tuple[str, str]] = set()

    rows: list[DwellRow] = []
    for stop in usable:
        day = stop.arrived.date().isoformat()
        prior = history.get(stop.receiver_id, [])
        gap = None
        if stop.receiver_id in last_day:
            previous = datetime.fromisoformat(last_day[stop.receiver_id]).date()
            gap = (stop.arrived.date() - previous).days

        rows.append(
            DwellRow(
                receiver_id=stop.receiver_id,
                node_class=stop.node_class,
                arrived=stop.arrived,
                day=day,
                hour=stop.arrived.hour,
                day_of_week=stop.arrived.weekday(),
                stop_seq=stop.stop_seq or 0,
                stops_on_route=route_size.get(stop.route_id, 1),
                minutes_into_route=(
                    stop.arrived - route_start[stop.route_id]
                ).total_seconds()
                / 60.0,
                is_first_stop=int((stop.stop_seq or 0) <= 1),
                is_last_stop=int((stop.stop_seq or 0) >= route_last_seq[stop.route_id]),
                is_repeat_visit_today=int((stop.receiver_id, day) in seen_today),
                pieces=stop.pieces or 0.0,
                revenue=stop.revenue or 0.0,
                recv_prior_n=len(prior),
                recv_prior_mean=sum(prior) / len(prior) if prior else None,
                recv_prior_p90=_p90(prior),
                recv_days_since_last=gap,
                dwell_min=stop.dwell_sec / 60.0,
            )
        )

        # After the row, never before.
        history.setdefault(stop.receiver_id, []).append(stop.dwell_sec / 60.0)
        last_day[stop.receiver_id] = day
        seen_today.add((stop.receiver_id, day))

    return rows


def time_split(rows: list[DwellRow], test_fraction: float = 0.2):
    """Chronological. Never a random split on this data."""
    days = sorted({r.day for r in rows})
    cut = days[int(len(days) * (1 - test_fraction))]
    return [r for r in rows if r.day < cut], [r for r in rows if r.day >= cut], cut


def coldstart_split(
    train: list[DwellRow], test: list[DwellRow], holdout_fraction: float = 0.15
):
    """Carve out receivers the model never sees.

    By hash rather than by a seeded shuffle, so the same dock lands on the same
    side next month and two runs are comparable. The original used a numpy seed,
    which is reproducible within a run and moves the moment anybody changes it.

    Returns (train without those receivers, warm test, cold test). The warm and
    cold numbers answer different questions and the brief is explicit that
    averaging them hides the only failure that matters.
    """
    def held(receiver_id: str) -> bool:
        digest = hashlib.sha256(f"m1-coldstart:{receiver_id}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / float(1 << 64) < holdout_fraction

    return (
        [r for r in train if not held(r.receiver_id)],
        [r for r in test if not held(r.receiver_id)],
        [r for r in test if held(r.receiver_id)],
    )
