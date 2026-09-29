#!/usr/bin/env python3
"""Hold mypy at a baseline that can only shrink.

    python -m scripts.mypy_baseline           # check; non-zero if anything grew
    python -m scripts.mypy_baseline --update  # record the current counts

**Why a baseline and not zero.** `mypy app` reports 184 errors, and sampling
the highest-risk categories found one real defect and three kinds of false
positive - an invariant the type cannot express, a redis-py stub artifact, and
SQLAlchemy's own `UUID` column type failing to match `uuid.UUID`. Demanding
zero would mean silencing those with `# type: ignore`, which trades a number
nobody reads for comments nobody reads.

**Why per-file and not a total.** A single count lets a fixed error in one
module pay for a new one in another, and the trade is invisible in review. Per
file, a regression names the file it happened in.

This is the same bargain `tests/test_no_unreachable_routes.py` makes: a list of
known gaps, each attached to a place, that fails when it grows. The number is
allowed to be large. It is not allowed to grow quietly.

**Deliberately not a pytest test.** mypy needs the app's dependencies resolved
and takes a few seconds; running it inside the suite would slow every local
`pytest` for a check that belongs beside `ruff` in CI. `ci.yml` calls it there.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "mypy-baseline.json"

# `app/api/routes.py:123: error: ...  [arg-type]`
ERROR_LINE = re.compile(r"^(?P<path>[^:]+):\d+: error: ")


def current_counts() -> tuple[dict[str, int], str]:
    """Errors per file, and the raw output for when something needs reading."""
    done = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-pretty", "--no-error-summary"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    counts: collections.Counter[str] = collections.Counter()
    for line in done.stdout.splitlines():
        match = ERROR_LINE.match(line)
        if match:
            counts[match.group("path")] += 1
    return dict(counts), done.stdout


def load_baseline() -> dict[str, int]:
    if not BASELINE.exists():
        return {}
    return json.loads(BASELINE.read_text())["files"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="Record current counts as the new baseline. Use after fixing things, "
        "and expect to justify it if any number went up.",
    )
    args = parser.parse_args()

    counts, raw = current_counts()
    total = sum(counts.values())

    if args.update:
        BASELINE.write_text(
            json.dumps({"total": total, "files": dict(sorted(counts.items()))}, indent=2) + "\n"
        )
        print(f"Baseline recorded: {total} errors across {len(counts)} files.")
        return 0

    baseline = load_baseline()
    grew = {f: (baseline.get(f, 0), n) for f, n in counts.items() if n > baseline.get(f, 0)}
    # A file that dropped to zero disappears from `counts`, which is a win and
    # not something to report as drift. Only growth fails.
    shrank = {f: (b, counts.get(f, 0)) for f, b in baseline.items() if counts.get(f, 0) < b}

    if shrank:
        print("Improved since the baseline:")
        for f, (was, now) in sorted(shrank.items()):
            print(f"  {f}: {was} -> {now}")
        print("Run with --update to bank it.\n")

    if grew:
        print("mypy errors increased:")
        for f, (was, now) in sorted(grew.items()):
            print(f"  {f}: {was} -> {now}")
        print(
            "\nFix them, or explain the increase and re-baseline with --update.\n"
            "Full output:\n"
        )
        print(raw)
        return 1

    print(f"mypy holding at {total} errors across {len(counts)} files (baseline {sum(baseline.values())}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
