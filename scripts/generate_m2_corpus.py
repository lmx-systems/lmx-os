#!/usr/bin/env python3
"""Generate a synthetic `M2` corpus and print what it contains.

    python scripts/generate_m2_corpus.py --out /tmp/m2.csv
    python scripts/generate_m2_corpus.py --deliveries 125000 --seed 7

Deterministic: the same seed gives the same corpus, so nothing needs to be
committed for a result to be reproducible. That is also why there is no
checked-in sample - 78,000 rows is several megabytes of invented data in a
public repository, and the eleven characters of a seed reproduce it exactly.

The banner is not decoration. A CSV of eighty thousand plausible deliveries is
the easiest thing in this repository to mistake for the design partner's book,
and the second easiest to quote a number out of.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.m2.corpus import (  # noqa: E402
    DEFAULT_ARM_FRACTION,
    generate_corpus,
)

BANNER = """\
================================================================================
 SYNTHETIC. Generated from the assumptions in docs/DATA_NEED_BRIEF.md §4.2 and
 the coefficients in ml/m2/population.py. Every one of those is a guess with a
 reason attached.

 A model trained on this has learned our guesses. A score computed from it says
 whether the harness recovers a function we wrote down - nothing about urgency,
 nothing about the business, and nothing that belongs in front of an investor.
================================================================================"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deliveries", type=int, default=78_000)
    parser.add_argument("--locations", type=int, default=240)
    parser.add_argument("--operating-days", type=int, default=380)
    parser.add_argument("--arm-fraction", type=float, default=DEFAULT_ARM_FRACTION)
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--out", type=Path, default=None, help="write the corpus as CSV")
    parser.add_argument("--json", action="store_true", help="summary as JSON, no banner")
    args = parser.parse_args()

    corpus = generate_corpus(
        deliveries=args.deliveries,
        locations=args.locations,
        operating_days=args.operating_days,
        arm_fraction=args.arm_fraction,
        seed=args.seed,
    )
    summary = corpus.summary()

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(BANNER)
        print()
        for key, value in summary.items():
            shown = f"{value:.4f}" if isinstance(value, float) else value
            print(f"  {key:<44} {shown}")
        print()
        print("  Read these two together:")
        print(
            f"    {summary['observed_consequences']} observed consequences overall, but only "
            f"{summary['observed_consequences_in_control_arm']} inside the control arm."
        )
        print(
            "    If M2 may only train on arm labels, this volume is two orders of "
            "magnitude short."
        )

    if args.out:
        path = corpus.write_csv(args.out)
        print(f"\n  wrote {len(corpus.deliveries):,} rows to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
