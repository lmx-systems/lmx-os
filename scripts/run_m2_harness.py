#!/usr/bin/env python3
"""Run the `M2` gates against a synthetic corpus and print what they say.

    python scripts/run_m2_harness.py
    python scripts/run_m2_harness.py --deliveries 125000 --arm-fraction 0.10

Exit code is 1 if any gate fails, so this can sit in front of a release. On the
synthetic corpus several of them are *expected* to fail - the arm is not
powered, the challenger does not beat the shrinkage baseline on both
populations - and those failures are the output, not a problem with the run.

Nothing here is evidence about urgency. See the banner.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.m2.corpus import DEFAULT_ARM_FRACTION, generate_corpus  # noqa: E402
from ml.m2.gates import OPEN_DECISIONS, leakage_demonstration, run_gates  # noqa: E402
from scripts.generate_m2_corpus import BANNER  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deliveries", type=int, default=78_000)
    parser.add_argument("--locations", type=int, default=240)
    parser.add_argument("--operating-days", type=int, default=380)
    parser.add_argument("--arm-fraction", type=float, default=DEFAULT_ARM_FRACTION)
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--leak-demo", action="store_true")
    args = parser.parse_args()

    corpus = generate_corpus(
        deliveries=args.deliveries,
        locations=args.locations,
        operating_days=args.operating_days,
        arm_fraction=args.arm_fraction,
        seed=args.seed,
    )
    print(BANNER)
    print()

    results = run_gates(corpus)
    for result in results:
        print(f"  {result}")

    print()
    for result in results:
        if result.numbers:
            print(f"  {result.name}")
            for key, value in result.numbers.items():
                shown = f"{value:.4f}" if isinstance(value, float) else value
                print(f"    {key:<34} {shown}")

    if args.leak_demo:
        print("\n  leakage demonstration (lower Brier is better)")
        for split, numbers in leakage_demonstration(corpus).items():
            print(f"    {split}")
            for key, value in numbers.items():
                print(f"      {key:<34} {value:.5f}")
        print(
            "\n    The leaky model wins every one of these. There is no offline\n"
            "    number that catches it - only reading how the feature was built."
        )

    print("\n  Open decisions this harness cannot settle:")
    for decision in OPEN_DECISIONS:
        print(f"    - {decision}")

    failed = [r for r in results if not r.passed]
    print(f"\n  {len(results) - len(failed)}/{len(results)} gates pass.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
