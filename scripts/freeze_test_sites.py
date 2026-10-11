#!/usr/bin/env python3
"""Draw a region's frozen test sites before collecting there, and hand them to `ml/`.

    python scripts/freeze_test_sites.py draw   --region TX --share 0.2 --by sourabh
    python scripts/freeze_test_sites.py status --region TX
    python scripts/freeze_test_sites.py export --out test_sites.json

`DE-8`, decision D-TEST. Run `draw` before the visits that collect a region's
records, and again whenever new sites arrive: a site is drawn once, ever, so a
second run only draws the sites the first did not reach. **The share has no
default.** How many sites a region holds back is a trade between how tightly a
test can be read and how much is left to train on, and it is set by whoever runs
the first draw; every later draw in the region must use the same one.

`export` writes the test sites in the form `ml/modality/splits.py` reads, so a
training set can refuse them. New Jersey cannot be drawn (D-NJ).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import AsyncSessionLocal  # noqa: E402
from app.models.site_draw import SiteDraw  # noqa: E402
from app.record.site_draws import DrawRefused, draw_test_sites, frozen_sites_document  # noqa: E402


async def _draw(region: str, share: float, by: str) -> int:
    async with AsyncSessionLocal() as session:
        try:
            report = await draw_test_sites(session, region=region, share=share, drawn_by=by)
        except DrawRefused as problem:
            raise SystemExit(str(problem)) from problem
        await session.commit()
    print(f"{region}: drew {report.drawn} sites, {report.test} into the test pool (share {share})")
    for group, n in sorted(report.by_group.items()):
        print(f"  {group[0]} / {group[1]:<10} {n:>4} drawn, {report.test_by_group[group]:>3} test")
    print(f"already drawn before this run    {report.already_drawn}")
    print(f"waiting for kind or setting      {report.waiting_for_group}  (drawn once both are known)")
    return 0


async def _status(region: str) -> int:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(SiteDraw.kind_of_site, SiteDraw.setting, SiteDraw.is_test, func.count())
                .where(SiteDraw.region == region)
                .group_by(SiteDraw.kind_of_site, SiteDraw.setting, SiteDraw.is_test)
                .order_by(SiteDraw.kind_of_site, SiteDraw.setting)
            )
        ).all()
    if not rows:
        print(f"{region}: nothing drawn yet")
        return 0
    for kind, setting, is_test, n in rows:
        print(f"  {kind} / {setting:<10} {'test' if is_test else 'train':<5} {n:>4}")
    return 0


async def _export(out: str) -> int:
    async with AsyncSessionLocal() as session:
        document = await frozen_sites_document(session)
    Path(out).write_text(json.dumps(document, indent=2) + "\n")
    print(f"wrote {len(document['test_sites'])} test sites to {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    draw = sub.add_parser("draw")
    draw.add_argument("--region", required=True)
    draw.add_argument("--share", type=float, required=True, help="0 to 0.5; no default, see above")
    draw.add_argument("--by", required=True, help="who is running the draw")
    status = sub.add_parser("status")
    status.add_argument("--region", required=True)
    export = sub.add_parser("export")
    export.add_argument("--out", required=True)

    args = parser.parse_args()
    if args.command == "draw":
        return asyncio.run(_draw(args.region, args.share, args.by))
    if args.command == "status":
        return asyncio.run(_status(args.region))
    return asyncio.run(_export(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
