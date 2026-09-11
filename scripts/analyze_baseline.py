#!/usr/bin/env python3
"""
Reduce a driver-activity export and a customer stop-invoice export to the operational
baseline that the W9 shadow-mode scorecard is measured against (docs/ROADMAP.md).

    python scripts/analyze_baseline.py \
      --activity path/to/driver_activity.csv \
      --invoices path/to/stop_invoices.csv

The exports come from systems we do not control, so the column names are discovered at
runtime rather than assumed. Start here on first contact with a new file:

    python scripts/analyze_baseline.py --activity FILE --describe

`--describe` prints every header in the file and what it bound to, and never computes
anything. When auto-detection misses a column - which it will, because vendor headers
are arbitrary - bind it by hand and re-run:

    --map activity:driver_id='Emp #' --map invoices:amount='Extended Price'

Either file may be given alone. Metrics that need the other one are reported as not
computable rather than skipped.

Keep the raw CSVs out of the repository. They carry a real customer's name in the
filename and their operational detail in the rows; `CLAUDE.md` forbids that name in
committed artifacts, and this tool never writes it into its own output.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Runnable as `python scripts/analyze_baseline.py` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.baseline.columns import (  # noqa: E402
    ACTIVITY_FIELDS,
    INVOICE_FIELDS,
    ColumnMappingError,
    resolve,
)
from app.baseline.loaders import (  # noqa: E402
    BaselineLoadError,
    read_csv,
    load_activity,
    load_invoices,
)
from app.baseline.metrics import compute  # noqa: E402
from app.baseline.report import render_json, render_text  # noqa: E402


def _parse_map(pairs: list[str]) -> dict[str, dict[str, str]]:
    """`--map activity:driver_id='Emp #'` -> {"activity": {"driver_id": "Emp #"}}.

    An unprefixed `--map driver_id=X` applies to whichever file knows that field; if
    both do (`stop_ref`, `miles`), the prefix is required rather than guessed.
    """
    overrides: dict[str, dict[str, str]] = {"activity": {}, "invoices": {}}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--map expects canonical=Header, got {pair!r}")
        left, header = pair.split("=", 1)
        left = left.strip()
        header = header.strip()
        if ":" in left:
            which, canonical = left.split(":", 1)
            which, canonical = which.strip(), canonical.strip()
            if which not in overrides:
                raise SystemExit(f"--map prefix must be 'activity' or 'invoices', got {which!r}")
            overrides[which][canonical] = header
            continue
        in_activity = left in ACTIVITY_FIELDS
        in_invoices = left in INVOICE_FIELDS
        if in_activity and in_invoices:
            raise SystemExit(
                f"--map {left}=... is ambiguous: both exports have a {left} field. "
                f"Prefix it, e.g. --map activity:{left}={header!r}"
            )
        if in_activity:
            overrides["activity"][left] = header
        elif in_invoices:
            overrides["invoices"][left] = header
        else:
            raise SystemExit(
                f"--map {left}=... names no known field.\n"
                f"  activity: {', '.join(sorted(ACTIVITY_FIELDS))}\n"
                f"  invoices: {', '.join(sorted(INVOICE_FIELDS))}"
            )
    return overrides


def _describe(path: str, known: dict[str, str], overrides: dict[str, str], label: str) -> None:
    """Show headers and the mapping, without computing anything.

    Deliberately survives a mapping failure: the reason to run `--describe` is usually
    that the mapping just failed, and printing the headers is the answer to that.
    """
    rows, headers = read_csv(path)
    print(f"\n{label}: {path}")
    print(f"  {len(rows):,} data rows, {len(headers)} columns")
    print("  columns present:")
    for header in headers:
        print(f"    {header!r}")
    try:
        column_map = resolve(headers, known, (), overrides, label=label)
    except ColumnMappingError as exc:
        print(f"  mapping error: {exc}")
        return
    print("  bound:")
    for line in column_map.describe() or ["    (nothing bound)"]:
        print(line)
    unbound = sorted(set(known) - set(column_map.mapping))
    if unbound:
        print(f"  UNBOUND ({len(unbound)}): {', '.join(unbound)}")
        print("    Bind with --map, or accept that the metrics needing them are n/a.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="analyze_baseline.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--activity", help="driver activity CSV export")
    parser.add_argument("--invoices", help="customer stop invoice CSV export")
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="FIELD=HEADER",
        help="bind a canonical field to a column, e.g. activity:driver_id='Emp #'",
    )
    parser.add_argument(
        "--describe",
        action="store_true",
        help="print headers and the resolved mapping, then stop",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    parser.add_argument("--out", help="write the report to a file as well as stdout")
    args = parser.parse_args(argv)

    if not args.activity and not args.invoices:
        parser.error("give --activity, --invoices, or both")

    overrides = _parse_map(args.map)

    try:
        if args.describe:
            if args.activity:
                _describe(args.activity, ACTIVITY_FIELDS, overrides["activity"], "activity export")
            if args.invoices:
                _describe(args.invoices, INVOICE_FIELDS, overrides["invoices"], "invoice export")
            return 0

        activity = load_activity(args.activity, overrides["activity"]) if args.activity else None
        invoices = load_invoices(args.invoices, overrides["invoices"]) if args.invoices else None
    except (BaselineLoadError, ColumnMappingError) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        print("Run with --describe to see the columns this file actually has.", file=sys.stderr)
        return 2

    baseline = compute(activity, invoices)
    text = render_json(baseline) if args.json else render_text(baseline)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"\nWritten to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
