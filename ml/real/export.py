"""Reading the design partner's export, and saying plainly what it can support.

The files themselves live in `lmx-dwell/`, which is gitignored because this
repository is public and they hold the design partner's operational book - their
customers, their towns, their invoice numbers. Nothing here embeds any of it.
These loaders take a path and return rows; the fixtures the tests run against
are invented, with invented places.

**The reduced schema, not the raw one.** `lmx-dwell/run.py` turns two
block-structured vendor reports into two flat CSVs, and that is what this reads.
Re-implementing the vendor parsing here would move the design partner's column
layout and report structure into a public repo for no gain - the reduction is
stable, documented in that project, and the boundary is the right one.

**Two files, and they are not interchangeable.**

`stops_timing` is the whole book: 6,715 stops, 13 drivers, 230 receivers,
January to April. Full lifecycle timestamps - created, dispatched, arrived,
departed - and **minute resolution only**, which is why 65.5% of its stops
compute to exactly zero dwell. Those stops are not corrupt and no filter
recovers them; rounding destroyed the measurement. It is the right file for
anything about *when orders arrive and how they are batched*, and the wrong
file for anything about *how long a stop takes*.

`stops_detail` is one driver, 54 manifests, 1,451 stops, with stop time at true
second precision. It is the only trustworthy dwell in the export set, and it
carries revenue and pieces, which the timing file does not. One driver means no
driver effect can be separated and no held-out-driver split exists.

**Every reading carries its basis**, the same rule `app/baseline/metrics.py`
follows. A column that is present but never populated is a different fact from
a column that is absent, and both are different from a column that is full -
and only the third supports a model.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.identity.node_class import infer_node_class

_MONEY = re.compile(r"[$,\s]")

NODE_CLASS_UNKNOWN = "unknown"


def _classify(*texts: str | None) -> str:
    """Name in, class out, name dropped.

    `IDN-3` sits at 36% unlabelled against a done-when of under 2%, so a third
    of any per-class figure computed here lands in `unknown`. That is reported
    rather than dropped: silently excluding the unclassified would make every
    per-class number look better-supported than it is, and `unknown` is not a
    small residue.
    """
    result = infer_node_class(*texts)
    return result[0] if result else NODE_CLASS_UNKNOWN

# `lmx-dwell/dwell/config.py` integrity gates, re-applied here rather than
# trusted: real stops of three seconds exist, so do not floor at thirty, and a
# two-hour dwell is a data error rather than a long delivery.
MIN_DWELL_SEC = 1
MAX_DWELL_SEC = 120 * 60


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = _MONEY.sub("", raw)
    if not cleaned or cleaned.upper() in {"NA", "N/A"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _timestamp(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    for layout in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), layout)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class TimingStop:
    """One stop from the whole-book export. Dwell here is unreliable."""

    receiver_id: str
    route_id: str
    driver_id: str
    stop_seq: int | None
    stops_total: int | None
    created: datetime | None
    dispatched: datetime | None
    arrived: datetime | None
    departed: datetime | None
    dwell_sec: float | None
    hold_sec: float | None
    order_to_door_sec: float | None
    invoices_at_stop: int | None
    zip_code: str | None
    # Derived at load time from the account name, which is then discarded. The
    # name never reaches a field, a log or a test fixture - `CLAUDE.md`'s naming
    # rule covers all three, and a structure that cannot hold the name cannot
    # leak it. `PRD-1` needs the class and has no use for the name.
    node_class: str

    @property
    def was_inserted_in_flight(self) -> bool:
        """Created after its route had already been dispatched.

        Half the book. `ROADMAP_1.5.md` Phase 3 states it as the ceiling we are
        working against - *"50.2% of orders already get in-flight insertion by
        hand, and those reach customers faster"* - and this is the same
        measurement, recomputed. A negative hold is not a corrupt row; it is a
        dispatcher adding an order to a truck that had already left.
        """
        return self.hold_sec is not None and self.hold_sec < 0


@dataclass(frozen=True)
class DetailStop:
    """One stop from the second-precision export. Dwell here is real."""

    receiver_id: str
    route_id: str
    driver_id: str
    stop_seq: int | None
    arrived: datetime | None
    dwell_sec: float | None
    travel_sec: float | None
    revenue: float | None
    pieces: float | None
    weight: float | None
    # Same discipline as TimingStop: classified at load, name discarded.
    node_class: str


_BLANK = {"", "NA", "N/A", "-"}


@dataclass
class Coverage:
    """How much of a column is there, and how much of it says anything.

    Two counts, not one, because they are different facts with different
    consequences. `populated` is whether the vendor wrote something in the cell.
    `informative` is whether what they wrote was anything other than zero.

    The export has a column that is 100% populated and 0% informative - `weight`
    is filled in on every row and is always `0`. A single coverage number would
    have reported that column as perfect, and any eligibility rule built on it
    would have been built on nothing.
    """

    rows: int
    populated: dict[str, int] = field(default_factory=dict)
    informative: dict[str, int] = field(default_factory=dict)

    def share(self, column: str) -> float:
        return self.populated.get(column, 0) / self.rows if self.rows else 0.0

    def informative_share(self, column: str) -> float:
        return self.informative.get(column, 0) / self.rows if self.rows else 0.0

    def __str__(self) -> str:
        lines = [f"{self.rows} rows"]
        for column in sorted(self.populated):
            flag = ""
            if self.share(column) > 0.9 and self.informative_share(column) == 0.0:
                flag = "  <- present on every row and always zero"
            lines.append(
                f"  {column:22} {self.share(column):6.1%} populated"
                f"  {self.informative_share(column):6.1%} non-zero{flag}"
            )
        return "\n".join(lines)


def _coverage(raw: list[dict], columns: tuple[str, ...]) -> Coverage:
    cover = Coverage(rows=len(raw))
    for column in columns:
        present = 0
        informative = 0
        for row in raw:
            text = (row.get(column) or "").strip()
            if text in _BLANK:
                continue
            present += 1
            value = _number(text)
            # A non-numeric value that is present is informative by definition -
            # it is a receiver id or a timestamp, not a quantity.
            if value is None or value != 0.0:
                informative += 1
        cover.populated[column] = present
        cover.informative[column] = informative
    return cover


def load_timing(path: str | Path) -> tuple[list[TimingStop], Coverage]:
    raw = list(csv.DictReader(Path(path).open()))
    stops = [
        TimingStop(
            receiver_id=(row.get("receiver_id") or "").strip(),
            route_id=(row.get("route_id") or "").strip(),
            driver_id=(row.get("driver_id") or "").strip(),
            stop_seq=int(float(row["stop_seq"])) if _number(row.get("stop_seq")) is not None else None,
            stops_total=int(float(row["stops_total"])) if _number(row.get("stops_total")) is not None else None,
            created=_timestamp(row.get("created")),
            dispatched=_timestamp(row.get("dispatched")),
            arrived=_timestamp(row.get("arrived")),
            departed=_timestamp(row.get("departed")),
            dwell_sec=_number(row.get("dwell_sec")),
            hold_sec=_number(row.get("hold_sec")),
            order_to_door_sec=_number(row.get("order_to_door_sec")),
            invoices_at_stop=int(float(row["invoices_at_stop"]))
            if _number(row.get("invoices_at_stop")) is not None
            else None,
            zip_code=(row.get("zip") or "").strip() or None,
            node_class=_classify(row.get("receiver_name"), row.get("Site Name")),
        )
        for row in raw
    ]
    return stops, _coverage(
        raw, ("receiver_id", "created", "dispatched", "arrived", "dwell_sec", "zip")
    )


def load_detail(path: str | Path) -> tuple[list[DetailStop], Coverage]:
    raw = list(csv.DictReader(Path(path).open()))
    stops = [
        DetailStop(
            receiver_id=(row.get("receiver_id") or "").strip(),
            route_id=(row.get("route_id") or "").strip(),
            driver_id=(row.get("driver_id") or "").strip(),
            stop_seq=int(float(row["stop_seq"])) if _number(row.get("stop_seq")) is not None else None,
            arrived=_timestamp(row.get("arrived")),
            dwell_sec=_number(row.get("dwell_sec")),
            travel_sec=_number(row.get("travel_sec")),
            revenue=_number(row.get("revenue")),
            pieces=_number(row.get("pieces")),
            weight=_number(row.get("weight")),
            node_class=_classify(row.get("receiver_name")),
        )
        for row in raw
    ]
    return stops, _coverage(
        raw, ("receiver_id", "dwell_sec", "travel_sec", "revenue", "pieces", "weight")
    )


def usable_dwell(stops: list[DetailStop]) -> list[DetailStop]:
    """Stops whose dwell passes the integrity gates and has a timestamp."""
    return [
        s
        for s in stops
        if s.dwell_sec is not None
        and MIN_DWELL_SEC <= s.dwell_sec <= MAX_DWELL_SEC
        and s.arrived is not None
        and s.receiver_id
    ]


@dataclass(frozen=True)
class Verdict:
    """What one model can get from this export, stated so it can be argued."""

    model: str
    supported: bool
    reason: str


def usability(
    timing: list[TimingStop], detail: list[DetailStop]
) -> list[Verdict]:
    """What is and is not buildable from what we hold today.

    Written as verdicts rather than a coverage table because the useful output
    is not "column X is 65% full" - it is "you cannot train this, and here is
    the sentence to put in front of whoever asks why".
    """
    zero_dwell = sum(1 for s in timing if s.dwell_sec == 0) / max(len(timing), 1)
    good_dwell = usable_dwell(detail)
    drivers = {s.driver_id for s in detail if s.driver_id}
    weighed = sum(1 for s in detail if s.weight)
    with_revenue = sum(1 for s in detail if s.revenue)
    in_flight = sum(1 for s in timing if s.was_inserted_in_flight) / max(len(timing), 1)

    return [
        Verdict(
            "M1 dwell",
            supported=len(good_dwell) >= 500,
            reason=(
                f"{len(good_dwell)} second-precision stops across "
                f"{len({s.receiver_id for s in good_dwell})} receivers. The "
                f"whole-book file cannot be used: {zero_dwell:.1%} of its stops "
                "round to zero dwell and no filter recovers them"
            ),
        ),
        Verdict(
            "M1 dwell, driver effects",
            supported=len(drivers) > 1,
            reason=(
                f"{len(drivers)} driver in the second-precision file. A driver "
                "term cannot be separated from that driver's route, and there is "
                "no held-out-driver split - so any dwell model built here is "
                "about one person's pace"
            ),
        ),
        Verdict(
            "M3 batching",
            supported=True,
            reason=(
                f"{len(timing)} stops with order-creation and route-dispatch "
                f"times. {in_flight:.1%} were created after their route left, "
                "which is in-flight insertion rather than a corrupt hold - the "
                "same 50.2% the roadmap states as the ceiling. Batch value has "
                "to be simulated over creation times; the export records no "
                "deliberate hold to measure directly"
            ),
        ),
        Verdict(
            "M4 trip cost vs order value",
            supported=with_revenue >= 500,
            reason=(
                f"{with_revenue} stops carry revenue and travel time. No "
                "distance column anywhere in the export, so cost is time-based "
                "only and a per-mile vehicle charge cannot be included"
            ),
        ),
        Verdict(
            "the two files agree on what a route is",
            supported=False,
            reason=(
                f"the whole-book file records {len({s.route_id for s in timing})} "
                f"routes over {len(timing)} stops; the second-precision file "
                f"records {len({s.route_id for s in detail})} over {len(detail)}. "
                "About 3.7 stops a route against about 27 - the same operation, a "
                "factor of seven apart, because one counts manifests and the other "
                "counts driver-days. Every per-route figure in either file "
                "inherits this. It is REC-5"
            ),
        ),
        Verdict(
            "M5 modality fit",
            supported=weighed > 0,
            reason=(
                f"the weight column is present on every row and populated on "
                f"{weighed}. It is never filled in. The brief's 55%-of-orders-"
                "under-2.5kg figure did not come from here and this export "
                "cannot check it"
            ),
        ),
    ]
