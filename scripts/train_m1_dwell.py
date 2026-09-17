#!/usr/bin/env python3
"""Run `M1`'s release gate against the real second-precision export (`PRD-5`).

    python scripts/train_m1_dwell.py                  # baseline only, stdlib
    .venv-ml/bin/python scripts/train_m1_dwell.py --challenger

The gate is `MODEL_AND_DATA_BRIEF.md` rule (3): *"Beat the baseline on both
populations, or ship the baseline."* Both, at every quantile, never on average -
the rule exists because the p90 model covered 66.5% of cold-start cases after
promising 90%, and an average across warm and cold hides exactly that.

`--challenger` needs an ML stack and so needs a different interpreter. The rest
of `ml/m1/` is standard library on purpose: a release gate that only runs where
scikit-learn is installed is a gate that stops running the day CI's dependency
cache changes.

**What this is a model of.** One driver's 54 manifests. No driver effect can be
separated and there is no held-out-driver split, so every figure below describes
one person's pace at 142 docks - see `ml/real/export.py`'s verdict.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.m1.evaluate import run  # noqa: E402
from ml.m1.features import build  # noqa: E402
from ml.real import load_detail, usable_dwell  # noqa: E402

DEFAULT_DETAIL = Path("lmx-dwell/out/stops_detail.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    parser.add_argument(
        "--challenger", action="store_true",
        help="also score the PRD-5 gradient-boosted challenger",
    )
    args = parser.parse_args()

    if not args.detail.exists():
        print(
            f"{args.detail} is not in this checkout. It is the design partner's "
            "export, which is gitignored because this repository is public."
        )
        return 1

    stops, _ = load_detail(args.detail)
    rows = build(stops)
    print(
        f"{len(rows)} usable stops of {len(stops)} across "
        f"{len({r.receiver_id for r in rows})} receivers, "
        f"{len({s.driver_id for s in usable_dwell(stops)})} driver(s)"
    )

    evaluation = run(rows, with_challenger=args.challenger)
    print(f"chronological cut at {evaluation.cut_day}\n")
    print(f"  {'population':12}{'model':22}{'n':>6}{'pinball':>10}{'coverage':>10}")
    for score in sorted(evaluation.scores, key=lambda s: (s.population, s.model)):
        print(
            f"  {score.population:12}{score.model:22}{score.n:6}"
            f"{score.pinball:10.4f}{score.coverage:10.3f}"
        )

    print("\n  the p90 promise, before and after conformal calibration:")
    for promise in evaluation.promises:
        print(
            f"    {promise.population:5} calibrated on {promise.calibrated_on}\n"
            f"          raw {promise.raw_coverage:.1%} -> conformal "
            f"{promise.conformal_coverage:.1%}, promise "
            f"{promise.raw_promise_minutes:.1f} -> "
            f"{promise.conformal_promise_minutes:.1f} min"
        )

    for note in evaluation.notes:
        print(f"\n  {note}")

    if args.challenger:
        ships, lost_on = evaluation.challenger_verdict()
        return 0 if ships else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
