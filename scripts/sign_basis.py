#!/usr/bin/env python3
"""Record that a side agreed to how a statement was calculated (`STL-2`).

    python scripts/sign_basis.py --client <uuid> --month 2026-08 --side lmx --who "Sourabh"
    python scripts/sign_basis.py --client <uuid> --month 2026-08 --side customer --who "Ops Manager"
    python scripts/sign_basis.py --client <uuid> --month 2026-08 --status

*"Baseline changes need sign-off from both sides."* Code cannot make a customer
agree to anything. What it can do is refuse to call a basis agreed until both
sides are on it, and keep the record where the figures are rather than in an
email nobody can find in March.

Its own command rather than a flag on `settle_month.py`, because signing is a
different act at a different time by a different person - usually after a
conversation about the statement the settlement run produced.
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
from app.models.settlement_basis import SIDES  # noqa: E402
from app.settle.basis import live_basis, sign  # noqa: E402


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    return start, (start + timedelta(days=32)).replace(day=1)


async def _run(client: str, month: str, side: str | None, who: str | None) -> int:
    since, until = _month_bounds(month)
    async with AsyncSessionLocal() as session:
        basis = await live_basis(
            session, client_id=uuid.UUID(client), period_start=since, period_end=until
        )
        if basis is None:
            print(
                f"no basis has been issued for {month}. Run "
                "`scripts/settle_month.py --issue` first - a signature needs "
                "something to sign."
            )
            return 1
        if side and who:
            await sign(session, basis, side=side, who=who)
            await session.commit()

        print(f"basis v{basis.version} for {month}")
        print(f"  issued        {basis.issued_at:%Y-%m-%d %H:%M}")
        for label, by, at in (
            ("lmx", basis.lmx_signed_by, basis.lmx_signed_at),
            ("customer", basis.customer_signed_by, basis.customer_signed_at),
        ):
            print(
                f"  {label:13} {by} on {at:%Y-%m-%d}" if at else
                f"  {label:13} - not signed"
            )
        print(f"  agreed        {'yes' if basis.is_agreed else 'no - one signature is a draft'}")
    return 0 if basis.is_agreed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--side", choices=SIDES)
    parser.add_argument("--who", help="the name that goes on the record")
    parser.add_argument("--status", action="store_true", help="read without signing")
    args = parser.parse_args()
    if not args.status and not (args.side and args.who):
        raise SystemExit("--side and --who are both required unless --status")
    side = None if args.status else args.side
    who = None if args.status else args.who
    return asyncio.run(_run(args.client, args.month, side, who))


if __name__ == "__main__":
    raise SystemExit(main())
