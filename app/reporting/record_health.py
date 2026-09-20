"""What the record actually holds, read back (`REC-1`..`REC-4`).

The audit's last gap. `REC-1` writes a decision every cycle, `REC-3` writes an
outcome at every delivery, `REC-2` writes consequences and silences, `REC-4`
raises flags — and nothing read any of it back, so the only way to know whether
a writer was working was to query the database by hand.

That matters more than it sounds. Every one of those writers was wired in the
last three changes, and **a writer that silently stops looks exactly like a
quiet week.** A panel that shows the counts is the thing that would notice.

## What this is not

It is not the savings statement and it is not a KPI. It answers "is the record
being written, and how far is the label set from being usable", both of which
are questions about *us* rather than about the business. `STL-1`'s statement is
the customer-facing number and has its own rules about what may be claimed.

## Why the on-time rate is here at all

Because it is the cheapest possible check that `REC-3` is doing its job, and
because it is computed from the ledger rather than from `orders`. An on-time
rate recomputed from `Order.delivered_at` against today's SLA terms would answer
a different question - it would rejudge old deliveries under new promises - and
the two disagreeing is the signal that the ledger has stopped being written.

Reported as a `Rate` with the arithmetic visible, and with a Wilson interval
rather than a bare percentage. `DATA_NEED_BRIEF.md` §4.3: the normal
approximation "is anti-conservative in exactly the small-proportion regime and
it fails on the side that matters", and an on-time rate early in a pilot is
exactly that regime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.experiment.integrity import wilson_interval
from app.identity.inherited_dwell import dwell_estimate
from app.models.decision_snapshot import DecisionSnapshot
from app.models.client import Client
from app.models.linkage_flag import LinkageFlag
from app.models.outcome_entry import KIND_DELIVERED, OutcomeEntry
from app.models.receiver_profile import ReceiverProfile
from app.models.shop import Shop
from app.record.consequences import label_counts
from app.reporting.measurement import Rate

DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class WriterHealth:
    """Whether one of the record's writers is producing anything.

    `last_written_at` rather than a boolean. "Nothing this week" and "nothing
    since March" are both zero rows and they mean completely different things -
    the first is a quiet week, the second is a writer that stopped.
    """

    name: str
    rows_in_window: int
    last_written_at: datetime | None
    note: str


@dataclass(frozen=True)
class DwellCoverage:
    """How many of this hub's docks we can say a dwell for, and whose it is.

    The read-back for the inherited import. Three counts rather than a
    percentage, because "80% covered" hides which 80% and whose measurement it
    is - and an inherited figure is a different kind of answer from one of our
    own (`app/identity/inherited_dwell.py`).
    """

    docks: int
    from_our_own: int
    inherited: int
    # Of the figures we have, how many rest on too few observations to lean
    # on. Reported because the real import produced a median of four stops
    # per dock - coverage without this reads as far stronger than it is.
    thin: int

    @property
    def unknown(self) -> int:
        return self.docks - self.from_our_own - self.inherited


@dataclass
class RecordHealth:
    """The record layer, reported on itself."""

    window_days: int
    on_time: Rate
    on_time_interval: tuple[float, float] | None
    decisions_recorded: int
    outcomes_recorded: int
    outcomes_linked_to_a_decision: int
    writers: list[WriterHealth] = field(default_factory=list)
    labels: dict = field(default_factory=dict)
    open_flags: int = 0
    dwell: DwellCoverage = field(
        default_factory=lambda: DwellCoverage(
            docks=0, from_our_own=0, inherited=0, thin=0
        )
    )

    @property
    def decision_link_rate(self) -> Rate:
        """How many delivered outcomes cite the cycle that assigned them.

        `REC-1` and `REC-3` were joinable by nothing until `snapshot_that_assigned`,
        and this is what says whether the join is actually being made. It is
        expected to be below 100%: an order placed on a route by a live insertion
        or an offer rather than by a recorded cycle has no snapshot to cite, and
        null is the honest answer there. A *fall* is the signal, not the level.
        """
        return Rate(
            name="Delivered outcomes citing a decision",
            target="no target - a fall is the signal, not the level",
            numerator=self.outcomes_linked_to_a_decision,
            denominator=self.outcomes_recorded,
            not_measured=(
                "no outcomes recorded in this window" if not self.outcomes_recorded else None
            ),
        )


async def _writer(
    session: AsyncSession, *, name: str, note: str, model, when, where
) -> WriterHealth:
    rows = await session.scalar(select(func.count()).select_from(model).where(*where))
    last = await session.scalar(select(func.max(when)).where(*where))
    return WriterHealth(
        name=name, rows_in_window=int(rows or 0), last_written_at=last, note=note
    )


async def build_record_health(
    session: AsyncSession, *, hub_id, window_days: int = DEFAULT_WINDOW_DAYS, now=None
) -> RecordHealth:
    """Read the record back for one hub.

    Everything here is a count or a proportion over the window. Nothing is
    recomputed from `orders`, deliberately: the point is to report what the
    ledger says, and a reader that fell back to recomputing would keep showing a
    healthy number after the ledger stopped being written, which is the one
    failure it exists to catch.
    """
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)

    delivered = list(
        await session.scalars(
            select(OutcomeEntry).where(
                OutcomeEntry.hub_id == hub_id,
                OutcomeEntry.kind == KIND_DELIVERED,
                OutcomeEntry.occurred_at >= since,
            )
        )
    )
    # `on_time is None` means no promise was ever made - a client with no
    # contract term. Excluded from both halves rather than counted as a success,
    # which would flatter the rate by exactly the orders nobody promised
    # anything about.
    judged = [e for e in delivered if e.values.get("on_time") is not None]
    on_time_count = sum(1 for e in judged if e.values.get("on_time") is True)

    rate = Rate(
        name="On time",
        target="from REC-3's ledger, judged against the promise that applied then",
        numerator=on_time_count,
        denominator=len(judged),
        not_measured=(
            "no delivery in this window carried a promise to be judged against"
            if not judged
            else None
        ),
    )
    interval = (
        tuple(round(100 * bound, 1) for bound in wilson_interval(on_time_count, len(judged)))
        if judged
        else None
    )

    decisions = await session.scalar(
        select(func.count())
        .select_from(DecisionSnapshot)
        .where(DecisionSnapshot.hub_id == hub_id, DecisionSnapshot.decided_at >= since)
    )

    writers = [
        await _writer(
            session,
            name="Decisions (REC-1)",
            note="one per dispatch cycle",
            model=DecisionSnapshot,
            when=DecisionSnapshot.decided_at,
            where=(DecisionSnapshot.hub_id == hub_id, DecisionSnapshot.decided_at >= since),
        ),
        await _writer(
            session,
            name="Delivery outcomes (REC-3)",
            note="one per completed dropoff",
            model=OutcomeEntry,
            when=OutcomeEntry.occurred_at,
            where=(
                OutcomeEntry.hub_id == hub_id,
                OutcomeEntry.kind == KIND_DELIVERED,
                OutcomeEntry.occurred_at >= since,
            ),
        ),
        await _writer(
            session,
            name="Consequences and silences (REC-2)",
            note="a dispatcher's judgement, plus the nightly close",
            model=OutcomeEntry,
            when=OutcomeEntry.occurred_at,
            where=(
                OutcomeEntry.hub_id == hub_id,
                OutcomeEntry.kind == "disputed",
                OutcomeEntry.occurred_at >= since,
            ),
        ),
        await _writer(
            session,
            name="Linkage flags (REC-4)",
            note="three detectors, nightly",
            model=LinkageFlag,
            when=LinkageFlag.detected_at,
            where=(LinkageFlag.hub_id == hub_id, LinkageFlag.detected_at >= since),
        ),
    ]

    open_flags = await session.scalar(
        select(func.count())
        .select_from(LinkageFlag)
        .where(LinkageFlag.hub_id == hub_id, LinkageFlag.resolved_at.is_(None))
    )

    # Every dock this hub's customers collect from, whether or not we have been
    # there. Docks we have never visited are the point: they are the ones an
    # inherited figure exists for, and a coverage count that only looked at
    # visited docks would report 100% while the new ones had nothing.
    dock_rows = (
        await session.execute(
            select(Shop.location_id, ReceiverProfile)
            .join(Client, Shop.client_id == Client.id)
            .outerjoin(ReceiverProfile, ReceiverProfile.location_id == Shop.location_id)
            .where(Client.hub_id == hub_id, Shop.location_id.is_not(None))
            .distinct()
        )
    ).all()
    estimates = [dwell_estimate(profile) for _, profile in dock_rows]
    coverage = DwellCoverage(
        docks=len(dock_rows),
        from_our_own=sum(1 for e in estimates if e.is_ours),
        inherited=sum(
            1 for e in estimates if e.source is not None and not e.is_ours
        ),
        thin=sum(1 for e in estimates if e.is_thin),
    )

    return RecordHealth(
        dwell=coverage,
        window_days=window_days,
        on_time=rate,
        on_time_interval=interval,
        decisions_recorded=int(decisions or 0),
        outcomes_recorded=len(delivered),
        outcomes_linked_to_a_decision=sum(
            1 for e in delivered if e.decision_snapshot_id is not None
        ),
        writers=writers,
        labels=await label_counts(session, hub_id=hub_id),
        open_flags=int(open_flags or 0),
    )
