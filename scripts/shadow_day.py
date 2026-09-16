#!/usr/bin/env python3
"""Run shadow cycles, and report the delta they produced (`DEC-0`).

    python scripts/shadow_day.py run    --hub <hub-uuid>
    python scripts/shadow_day.py report --hub <hub-uuid> --date 2026-07-06

DEC-0 is done when *"a full day produces a plan and a recorded delta with zero
operational change"*. Two halves, and they are two commands because they happen
at different times.

**`run` is a moment, not a day.** `plan_cycle` reads the hold queue and the
fleet as they are right now, so a day's cycles cannot be reconstructed after the
fact - there is no past state to plan against. A day of shadow evidence is made
by running this repeatedly through the day, which is what a cron entry or the
background scheduler below is for. Anything claiming to "backfill yesterday's
shadow plans" would be inventing them.

**`report` is safe to run whenever**, over any window, as many times as you
like. It only reads.

**Zero operational change is a property of `plan_cycle`, not of this script.**
It performs no writes - no hold-queue removals, no status updates, no offers, no
notifications - and `tests/test_shadow_recorder.py` asserts a shadow run leaves
the queue and every order status exactly as it found them.

**Still to decide, and not decided here:** whether this runs as a background
task in the app - the shape `app/learning_loop/scheduler.py` already uses, an
asyncio loop with a Redis lock started at startup - and at what cadence, and for
which hubs. Cadence is the load-bearing one: the shadow cycle's dispatch lead is
bounded below by how often it runs, so a cycle every thirty minutes cannot
demonstrate beating a dispatcher who inserts an order the moment it lands. That
is an operational choice with a cost, so it belongs to whoever runs the pilot.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import AsyncSessionLocal  # noqa: E402
from app.shadow.divergence import compute_divergence, render  # noqa: E402
from app.shadow.recorder import record_shadow_cycle  # noqa: E402


async def _run(hub_id: str) -> int:
    async with AsyncSessionLocal() as session:
        decision = await record_shadow_cycle(session, hub_id)
        await session.commit()
        print(
            f"shadow cycle recorded for hub {hub_id}\n"
            f"  planned_at   {decision.planned_at:%Y-%m-%d %H:%M:%S}\n"
            f"  engine       {decision.engine}\n"
            f"  duration     {decision.plan_duration_seconds:.2f}s\n"
            f"  held         {decision.held_order_count}\n"
            f"  released     {decision.released_order_count}\n"
            f"  assigned     {decision.assigned_order_count}\n"
            f"  unassigned   {decision.unassigned_order_count}\n"
            f"  drivers      {decision.driver_count}"
        )
        if decision.hub_closed:
            print(
                "  NOTE: the hub was closed. It decided nothing; it did not "
                "decide to dispatch nothing."
            )
    return 0


async def _report(hub_id: str, day: datetime | None, days: int) -> int:
    until = (day or datetime.now(timezone.utc)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    since = until - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        report = await compute_divergence(
            session, hub_id=uuid.UUID(hub_id), since=since, until=until
        )
        print(render(report))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="record one shadow cycle now, changing nothing")
    run.add_argument("--hub", required=True)

    report = sub.add_parser("report", help="the delta over a window, read-only")
    report.add_argument("--hub", required=True)
    report.add_argument(
        "--date", help="the last day to include, YYYY-MM-DD (default: today)"
    )
    report.add_argument("--days", type=int, default=1)

    args = parser.parse_args()
    if args.command == "run":
        return asyncio.run(_run(args.hub))
    day = (
        datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.date
        else None
    )
    return asyncio.run(_report(args.hub, day, args.days))


if __name__ == "__main__":
    raise SystemExit(main())
