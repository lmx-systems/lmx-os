#!/usr/bin/env python3
"""Run PRD-1, PRD-2 and the M1 dwell harness against the real export.

    python scripts/analyze_real_export.py --rate 45
    python scripts/analyze_real_export.py --timing path/to/stops_timing.csv

The files are gitignored and this prints no account name, no town and no
invoice number - receivers appear as ids and node classes only. Default paths
point at `lmx-dwell/out/`, which `lmx-dwell/run.py` produces.

Unlike `scripts/run_m2_harness.py` there is no synthetic banner here, because
this is the real book. The caveats are per-section and each one is printed with
the number it qualifies.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.m1.evaluate import run as run_dwell  # noqa: E402
from ml.m1.features import build as build_dwell  # noqa: E402
from ml.prd.batch_value import (  # noqa: E402
    CURVE_WINDOWS,
    MEASURE_BATCH_SIZE,
    MEASURE_ROUTES,
    MEASURE_SHARE_BATCHED,
    batch_value_by_node_class,
    check_the_stated_finding,
    where_the_spread_actually_is,
)
from ml.prd.trip_cost import by_route_size, compute as compute_cost  # noqa: E402
from ml.real import load_detail, load_timing, usability  # noqa: E402


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n {title}\n{'=' * 78}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timing", type=Path, default=Path("lmx-dwell/out/stops_timing.csv"))
    parser.add_argument("--detail", type=Path, default=Path("lmx-dwell/out/stops_detail.csv"))
    parser.add_argument(
        "--rate",
        type=float,
        required=True,
        help="loaded driver cost per hour. No default on purpose - nothing in "
        "this repository knows what it is, and borrowing the gig-pilot figure "
        "would produce a number that looked computed.",
    )
    args = parser.parse_args()

    timing, timing_coverage = load_timing(args.timing)
    detail, detail_coverage = load_detail(args.detail)

    _rule("What this export can support")
    print(f"\n whole book\n  {timing_coverage}".replace("\n", "\n  "))
    print(f"\n second-precision file\n  {detail_coverage}".replace("\n", "\n  "))
    print()
    for verdict in usability(timing, detail):
        mark = "yes" if verdict.supported else "NO "
        print(f"  [{mark}] {verdict.model}\n        {verdict.reason}")

    _rule("PRD-1 - batch value by node class")
    results = batch_value_by_node_class(timing)
    header = " ".join(f"{w:>6}" for w in CURVE_WINDOWS)
    print("\n  mean orders per batch, by hold window (minutes)\n")
    print(f"  {'class':13}{'docks':>6}{'orders':>8}  {header}")
    for result in sorted(results.values(), key=lambda r: -r.orders):
        curve = " ".join(
            f"{result.at(MEASURE_BATCH_SIZE, w):6.2f}" for w in CURVE_WINDOWS
        )
        print(f"  {result.node_class:13}{result.receivers:6}{result.orders:8}  {curve}")

    print("\n  lift from 45 to 90 minutes, three readings\n")
    print(f"  {'class':13}{'docks':>6}  {'batch size':>11}{'share batched':>15}{'routes saved':>14}")
    for result in sorted(results.values(), key=lambda r: -r.orders):
        def show(measure: str) -> str:
            value = result.lift(measure)
            return f"{value:+.1f}%" if value is not None else "-"
        print(
            f"  {result.node_class:13}{result.receivers:6}  "
            f"{show(MEASURE_BATCH_SIZE):>11}{show(MEASURE_SHARE_BATCHED):>15}"
            f"{show(MEASURE_ROUTES):>14}"
        )

    print("\n  against the finding PRD-1 is defined by:")
    for reproduction in check_the_stated_finding(results):
        print(f"    {reproduction.node_class:11} {reproduction.verdict}")

    print("\n  where the spread actually is (classes with 5+ docks):")
    for node_class, start, lift in where_the_spread_actually_is(results):
        print(f"    {node_class:13} {start:5.1%} batched at 45min  ->  {lift:+6.1f}%")

    _rule("PRD-2 - trip cost against order value")
    report = compute_cost(detail, rate_per_hour=args.rate)
    print()
    for key, value in report.summary().items():
        shown = f"{value:.3f}" if isinstance(value, float) else value
        print(f"  {key:26} {shown}")
    cut = by_route_size(report)
    print(f"\n  by route size: {cut if not cut.get('usable') else ''}")
    if cut.get("usable"):
        for key, stats in cut.items():
            if key != "usable":
                print(f"    {key:12} {stats}")
    print("\n  worst ten by absolute loss (receiver ids only):")
    for stop in report.worst(10):
        print(
            f"    {stop.receiver_id:12} ${stop.revenue:8.2f} revenue  "
            f"${stop.cost:8.2f} cost  {stop.minutes:6.1f} min"
        )

    _rule("M1 - dwell, on the only file with real dwell in it")
    rows = build_dwell(detail)
    print(
        f"\n  {len(rows)} usable stops across "
        f"{len({r.receiver_id for r in rows})} receivers, one driver"
    )
    evaluation = run_dwell(rows)
    print(f"  chronological cut at {evaluation.cut_day}\n")
    print(f"  {'population':12}{'model':20}{'n':>6}{'pinball':>10}{'coverage':>10}")
    for score in sorted(evaluation.scores, key=lambda s: (s.population, s.model)):
        print(
            f"  {score.population:12}{score.model:20}{score.n:6}"
            f"{score.pinball:10.4f}{score.coverage:10.3f}"
        )

    print("\n  the p90 promise, before and after conformal calibration:")
    for promise in evaluation.promises:
        print(
            f"    {promise.population:5} calibrated on {promise.calibrated_on}\n"
            f"          raw {promise.raw_coverage:.1%} -> conformal "
            f"{promise.conformal_coverage:.1%} (floor {promise.coverage_floor:.1%}), "
            f"promise {promise.raw_promise_minutes:.1f} -> "
            f"{promise.conformal_promise_minutes:.1f} min"
        )
    for note in evaluation.notes:
        print(f"\n  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
