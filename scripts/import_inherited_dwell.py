#!/usr/bin/env python3
"""Give docks we have never visited a dwell measured by the previous operator (`IDN-4`).

    python scripts/import_inherited_dwell.py --file lmx-dwell/detail.csv \
        --source "design partner, incumbent platform" [--dry-run]

`M1`'s problem is cold start: a dock we have not delivered to has no dwell, and
`refresh_dwell_statistics` can only compute from stops we made. The design
partner's second-precision export carries real observations for hundreds of the
same physical docks.

**Only the Detail export.** The whole-book Customer Timing file is
minute-resolution, so 65.5% of its stops compute to zero dwell - the finding that
justifies `DRV-1` rather than a parsing bug. Importing it would fill the table
with zeros that look like measurements. `ml/real/export.py::usable_dwell` is what
separates the two, and this calls it rather than re-deciding.

**Nothing is merged with our own observations.** The figures land in
`inherited_dwell_*`, never in `dwell_p50_seconds`, so the nightly refresh cannot
erase them and nobody reading a dock can mistake whose measurement they hold.

**Identity has to be seeded first.** The join is `Shop.external_ref`, which
`scripts/load_identity_from_export.py` writes. A receiver id matching no shop is
counted and reported rather than guessed at - run that script first, and if the
unmatched count is high afterwards, that is the finding.

`--dry-run` reads and reports without writing, which is the right first move on
a file nobody has imported before: the unmatched count tells you whether the
identity seed and this export are talking about the same accounts.
"""
import argparse
import asyncio
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import session_scope  # noqa: E402
from app.identity.inherited_dwell import import_inherited_dwell  # noqa: E402
from ml.real.export import load_detail, usable_dwell  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="The Detail export")
    parser.add_argument(
        "--source",
        required=True,
        help="Who measured it, e.g. 'design partner, incumbent platform'. Recorded "
        "on every dock: an inherited figure with no attribution is "
        "indistinguishable from an invented one.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Read and report, write nothing.")
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    if not args.file.exists():
        print(f"No such file: {args.file}", file=sys.stderr)
        return 1

    stops, coverage = load_detail(args.file)
    usable = usable_dwell(stops)
    print(f"rows read         {len(stops)}")
    print(f"usable dwell      {len(usable)}  ({len(stops) - len(usable)} rejected by the gates)")
    print(f"coverage          {coverage}")

    if not usable:
        print("Nothing to import - no row in this file carries a usable dwell.")
        return 1

    by_receiver: dict[str, list[float]] = defaultdict(list)
    for stop in usable:
        by_receiver[stop.receiver_id].append(stop.dwell_sec)

    arrivals = [s.arrived for s in usable if s.arrived is not None]
    observed_from, observed_to = (min(arrivals), max(arrivals)) if arrivals else (None, None)
    print(f"receivers         {len(by_receiver)}")
    print(f"period            {observed_from} to {observed_to}")
    print(
        "median of medians "
        f"{statistics.median(statistics.median(v) for v in by_receiver.values()):.0f}s"
    )

    async with session_scope() as session:
        if args.dry_run:
            # The number worth knowing before writing anything: how many of these
            # accounts identity has ever heard of.
            from sqlalchemy import select

            from app.models.shop import Shop

            known = set(
                (
                    await session.scalars(
                        select(Shop.external_ref).where(
                            Shop.external_ref.in_(by_receiver),
                            Shop.location_id.is_not(None),
                        )
                    )
                ).all()
            )
            print(f"would match       {len(known)} of {len(by_receiver)} receivers")
            if len(known) < len(by_receiver):
                print(
                    "  Unmatched receivers have no shop with that external_ref, or a "
                    "shop with no dock. Run scripts/load_identity_from_export.py first."
                )
            return 0

        report = await import_inherited_dwell(
            session,
            dict(by_receiver),
            source=args.source,
            observed_from=observed_from,
            observed_to=observed_to,
        )

    print(report.summary())
    return 0 if report.docks_updated else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
