#!/usr/bin/env python3
"""Check that everything we ingested reached an invoice (`STL-3`).

    python scripts/reconcile_billing.py --client <uuid> --month 2026-08

`app/billing/` has generated invoices since C3 and nothing has ever checked
that what it billed matches what came in. The failure mode is the worst kind:
silent, compounding, and in whichever direction nobody is watching.

Windowed on **ingestion**, not delivery, because the fee unit is the ingest.
Billing windows on `delivered_at`, and that difference is the thing this exists
to measure: an order taken in on the last day of a month and delivered on the
first of the next is ingested in one period and billed in another, and only a
check anchored to the ingest can see it at all.

Exit code is 1 when the period does not reconcile, so this can sit in a month-end
job rather than being something somebody remembers to run.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.billing.reconciliation import (  # noqa: E402
    invoiced_total_cents,
    reconcile_period,
    render,
)
from app.db import AsyncSessionLocal  # noqa: E402


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    return start, (start + timedelta(days=32)).replace(day=1)


async def _run(client: str, month: str, verbose: bool) -> int:
    since, until = _month_bounds(month)
    async with AsyncSessionLocal() as session:
        report = await reconcile_period(
            session, client_id=uuid.UUID(client), since=since, until=until
        )
        charged = await invoiced_total_cents(
            session, client_id=uuid.UUID(client), since=since, until=until
        )
        print(render(report))
        print(f"\n  invoiced this window  ${charged / 100:,.2f}")
        if verbose:
            for name, ids in (
                ("delivered, priced, uninvoiced", report.delivered_not_invoiced),
                ("delivered with no rate", report.unpriced),
                ("never delivered", report.not_delivered),
                ("billed with no ingest", report.billed_without_ingest),
            ):
                if ids:
                    print(f"\n  {name}:")
                    for order_id in ids[:50]:
                        print(f"    {order_id}")
                    if len(ids) > 50:
                        print(f"    ... and {len(ids) - 50} more")
    return 0 if report.reconciles else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--verbose", action="store_true", help="list the order ids")
    args = parser.parse_args()
    return asyncio.run(_run(args.client, args.month, args.verbose))


if __name__ == "__main__":
    raise SystemExit(main())
