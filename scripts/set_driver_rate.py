#!/usr/bin/env python3
"""Record what a driver is paid, so a cost stops being an invented number.

    python scripts/set_driver_rate.py --driver <uuid> --hourly 24.50
    python scripts/set_driver_rate.py --hub <uuid> --list

`Driver.hourly_rate_cents` is nullable and nothing in this codebase ever set it,
so `app/payroll/hours.py` fell back to a placeholder it flags as "not tuned
against any real wage decision" - and every cost `REC-2` computes was arithmetic
on that placeholder. The figure looked like a measurement and was not one.

The rate is per hour in dollars because that is how it is agreed; it is stored
in cents because money in floats is how rounding errors reach an invoice.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import AsyncSessionLocal  # noqa: E402
from app.models.driver import Driver  # noqa: E402


async def _list(hub: str) -> int:
    async with AsyncSessionLocal() as session:
        drivers = list(
            await session.scalars(
                select(Driver).where(Driver.hub_id == uuid.UUID(hub)).order_by(Driver.name)
            )
        )
        if not drivers:
            print("no drivers at that hub")
            return 0
        missing = 0
        for driver in drivers:
            if driver.hourly_rate_cents:
                rate = f"${driver.hourly_rate_cents / 100:.2f}/hr"
            else:
                rate = "- (costed at payroll's placeholder)"
                missing += 1
            print(f"  {driver.id}  {driver.name:24} {rate}")
        if missing:
            print(
                f"\n{missing} driver(s) have no rate. Every drop they carry is "
                "costed against an invented wage, and the savings statement says so."
            )
    return 0


async def _set(driver_id: str, hourly: float) -> int:
    if hourly <= 0:
        raise SystemExit("an hourly rate must be positive")
    async with AsyncSessionLocal() as session:
        driver = await session.get(Driver, uuid.UUID(driver_id))
        if driver is None:
            raise SystemExit(f"no driver {driver_id}")
        driver.hourly_rate_cents = round(hourly * 100)
        await session.commit()
        print(f"{driver.name}: ${driver.hourly_rate_cents / 100:.2f}/hr")
        print(
            "Costs already recorded are not recalculated - the ledger is "
            "append-only. Re-run settle_month.py with --recompute to supersede "
            "them with figures against the real wage."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver")
    parser.add_argument("--hourly", type=float)
    parser.add_argument("--hub")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        if not args.hub:
            raise SystemExit("--list needs --hub")
        return asyncio.run(_list(args.hub))
    if not args.driver or args.hourly is None:
        raise SystemExit("--driver and --hourly are both required")
    return asyncio.run(_set(args.driver, args.hourly))


if __name__ == "__main__":
    raise SystemExit(main())
