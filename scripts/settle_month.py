#!/usr/bin/env python3
"""Cost a period, check the arm, and produce the savings statement.

    python scripts/settle_month.py --hub <uuid> --client <uuid> --month 2026-08
    python scripts/settle_month.py --hub <uuid> --client <uuid> --month 2026-08 --pdf out.pdf

Three switches that did not exist, in the order the work actually happens.
`record_driver_day_cost` and `build_statement` both had no caller anywhere -
tested, finished-looking, and never run - so nothing in this system had ever
computed what a drop cost or produced a statement.

**Costing runs first, because the statement reads the ledger.** It is idempotent:
orders already costed are skipped rather than written twice, since an
append-only ledger with two live costs for one order leaves nothing downstream
able to choose. `--recompute` supersedes instead, which is the ledger's own
answer to a corrected figure - use it when an input changed, not to paper over a
double run.

**`EXP-3` runs inside `build_statement`, before anything is computed.** If the
arm is skewed or contaminated the statement prints no figure, and this prints
why. That is a real outcome, not an error: a statement that quietly reported a
number from a broken arm is the failure the gate exists to prevent.

**The PDF is opt-in.** A statement is worth reading in a terminal while the
numbers are still being argued about; it becomes a document when somebody has
decided to send it.
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
from app.experiment.integrity import render as render_integrity  # noqa: E402
from app.record.cost import record_costs_for_period  # noqa: E402
from app.settle.basis import issue, live_basis, reproduce  # noqa: E402
from app.settle.pdf import render_statement_pdf  # noqa: E402
from app.settle.statement import build_statement, render_statement  # noqa: E402


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    following = (start + timedelta(days=32)).replace(day=1)
    return start, following


async def _run(
    hub: str, client: str, month: str, pdf: Path | None, recompute: bool,
    issue_basis: bool,
) -> int:
    since, until = _month_bounds(month)
    async with AsyncSessionLocal() as session:
        print(f"Costing {since:%B %Y} for hub {hub}")
        summary = await record_costs_for_period(
            session, hub_id=uuid.UUID(hub), since=since, until=until,
            recompute=recompute,
        )
        await session.commit()
        for key, value in summary.items():
            print(f"  {key:26} {value}")
        if summary["placeholder_rate_days"]:
            print(
                f"  NOTE: {summary['placeholder_rate_days']} driver-day(s) were "
                "costed against payroll's placeholder wage because the driver has "
                "no hourly rate recorded. Those figures are arithmetic on an "
                "invented number - see scripts/set_driver_rate.py."
            )

        statement = await build_statement(
            session, hub_id=uuid.UUID(hub), client_id=uuid.UUID(client),
            period_start=since, period_end=until,
        )
        print("\n" + "=" * 78)
        print(render_statement(statement))
        print("=" * 78)

        if statement.integrity is not None and statement.integrity.blocks_a_statement:
            print("\nWhy no figure was printed:\n")
            print(render_integrity(statement.integrity))

        # STL-2. Checked before issuing, because the interesting case is a
        # period that already has a basis and no longer reproduces under it -
        # which is exactly what `--recompute` above can cause.
        existing = await live_basis(
            session, client_id=uuid.UUID(client),
            period_start=since, period_end=until,
        )
        if existing is not None:
            result = reproduce(existing, statement)
            print(f"\n{result.explain()}")
            if not result.reproduces and result.recosted_orders:
                for order in result.recosted_orders[:20]:
                    print(f"    {order}")
                if len(result.recosted_orders) > 20:
                    print(f"    ... and {len(result.recosted_orders) - 20} more")

        if issue_basis:
            basis = await issue(session, statement)
            await session.commit()
            state = "agreed" if basis.is_agreed else "not yet signed by either side"
            print(f"\nissued under basis v{basis.version} ({state})")
            print("  scripts/sign_basis.py records a signature")

        if pdf:
            pdf.write_bytes(render_statement_pdf(statement))
            print(f"\nwrote {pdf}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", required=True)
    parser.add_argument("--client", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--pdf", type=Path, default=None)
    parser.add_argument(
        "--recompute", action="store_true",
        help="supersede existing costs rather than skipping them",
    )
    parser.add_argument(
        "--issue", action="store_true",
        help="record what this statement was calculated under (STL-2)",
    )
    args = parser.parse_args()
    return asyncio.run(
        _run(
            args.hub, args.client, args.month, args.pdf, args.recompute,
            args.issue,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
