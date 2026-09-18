"""Re-ingesting history (`ING-3`).

*"History re-ingests without double-counting or mutating decisions."*

Both halves of that are about restraint rather than machinery. Intake does six
things - creates the record, prices it, assigns a control arm, may record an
abstention, puts it in the live hold queue, counts it - and for an order that
already happened, five are wrong. Three of those are wrong in ways nothing can
undo:

**Pricing** puts a historical delivery back in `generate_invoice`'s selection,
and the customer is billed a second time for work already invoiced.

**The arm** writes to `experiment_assignments`, which is append-only and
immutable by trigger. An arm on an order delivered three weeks ago is experiment
data about an experiment that order was never in, and it stays there. `EXP-1`'s
measurement - the thing that turns the central claim from modelled to observed -
would be contaminated by an import.

**The hold queue** sends a driver to collect a delivery that already happened.
The only one of the three anybody would notice within the hour, and the only one
that is not permanent.

## Why this is a mode and not a script that is careful

A backfill written as "call ingest, then delete the assignment" cannot work: the
trigger refuses the delete. Written as "call ingest with the arm turned off by a
flag somewhere" it works until the next person adds a seventh side effect to
intake and does not know this file exists. `Order.intake_mode` is on the row, so
what an order *is* travels with it and the queries that must exclude history can
say so.

## What a backfill deliberately does not do

It does not set terminal statuses or delivery timestamps from the import. Those
are the importer's job through the ordinary contract fields, because a backfill
that invented a delivery time would be manufacturing exactly the observations
`M1` is meant to learn from.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.store import HoldQueueStore
from app.geocoding import BaseGeocoder
from app.ingestion.service import (
    DestinationUnresolvableError,
    OriginUnresolvableError,
    ShopNotFoundError,
    find_existing_order,
    ingest_lmx_order,
)
from app.models.order import INTAKE_BACKFILL
from app.schemas.lmx_order import LMXOrder


@dataclass
class BackfillReport:
    """What an import did, in terms somebody can check against their file.

    `replayed` is reported separately from `created` rather than folded into a
    success count. Re-running an import is the normal way to finish one that
    failed halfway, and a run that reports "1,400 imported" when 1,380 of them
    were already there is the report that hides a double-count rather than
    ruling one out.
    """

    created: int = 0
    replayed: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def considered(self) -> int:
        return self.created + self.replayed + self.failed

    def summary(self) -> str:
        line = (
            f"{self.considered} order(s) considered: {self.created} imported, "
            f"{self.replayed} already present, {self.failed} failed."
        )
        if self.replayed and not self.created:
            line += " Nothing new - this import had already been run."
        return line


async def backfill_orders(
    session: AsyncSession,
    orders: list[LMXOrder],
    *,
    geocoder: BaseGeocoder,
    stop_on_error: bool = False,
) -> BackfillReport:
    """Import historical orders, skipping the ones already present.

    Each order is committed by `ingest_lmx_order` individually rather than the
    whole file in one transaction. A 20,000-row import that rolls back entirely
    because row 19,000 has an address nobody can geocode is an import that never
    completes, and the replay path is what makes per-order commits safe: a rerun
    picks up where the last one stopped instead of duplicating what it did.

    Failures are collected rather than raised, with the source reference, so the
    report names the rows to look at. `stop_on_error` is for the first run of a
    new file, where a failure usually means the mapping is wrong and continuing
    produces twenty thousand of the same mistake.
    """
    report = BackfillReport()
    # A backfill never dispatches, so nothing here should be able to reach Redis.
    # Passing a real store would work and would mean the one line that protects
    # against it is a branch in another module.
    queue = _RefusingHoldQueue()

    for lmx in orders:
        ref = lmx.source_order_ref or "<no source ref>"
        try:
            if await find_existing_order(session, lmx) is not None:
                report.replayed += 1
                continue
            await ingest_lmx_order(
                session, queue, lmx, geocoder=geocoder, mode=INTAKE_BACKFILL
            )
            report.created += 1
        except (
            ShopNotFoundError,
            OriginUnresolvableError,
            DestinationUnresolvableError,
        ) as exc:
            await session.rollback()
            report.failed += 1
            report.failures.append((ref, str(exc)))
            if stop_on_error:
                break

    return report


class _RefusingHoldQueue(HoldQueueStore):
    """A hold queue that refuses to be used.

    Belt for the suppression in `ingest_lmx_order`. If a future change moves the
    queue write above the backfill return, this raises in a test rather than
    putting three weeks of history in front of a driver in production.
    """

    async def add(self, *args, **kwargs):  # pragma: no cover - the point is it never runs
        raise AssertionError(
            "a backfill reached the live hold queue - history must never be dispatched"
        )
