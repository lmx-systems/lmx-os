"""Keeping a dock out of the arm, and counting what that costs (`EXP-2`).

Half of EXP-2's done-when: *"fragile accounts excludable."*

**This is what makes the clause signable.** `EXP-1` ships off until a customer's
contract records that they agreed to a control arm, and the first thing anyone
asks before agreeing is whether their most important dock can be left out of it.
Without an answer the conversation ends there, so the mechanism has to exist
before the clause does.

**And it is not free.** A customer who excludes their most delivery-sensitive
docks leaves a control arm measured on their calmer traffic. The saving
estimated there does not generalise back to the docks that were removed - and
the removed docks are, by construction, the ones where being slower costs the
most. That is a reasonable trade for a customer to make and an unreasonable one
to make silently, which is why `exclusion_impact` exists: `STL-1` should be able
to state what share of volume the measurement never covered, in the statement
itself, without anybody having to remember to look.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment_assignment import EXPERIMENT_CONTROL_ARM
from app.models.experiment_exclusion import ExperimentExclusion

REQUESTED_BY_CUSTOMER = "customer"
REQUESTED_BY_LMX = "lmx"
REQUESTED_BY = (REQUESTED_BY_CUSTOMER, REQUESTED_BY_LMX)


class ReceiverExcluded(RuntimeError):
    """Raised when an arm is requested for a dock somebody asked us to skip.

    Its own type rather than reusing `ArmNotContractedError`, because they mean
    different things to whoever is reading the logs: one is a customer who never
    agreed to an experiment, the other is a customer who did and named this dock
    as out of scope. Conflating them would hide an exclusion list growing until
    the arm covered nothing.
    """


async def exclude_receiver(
    session: AsyncSession,
    *,
    client_id,
    receiver_key: str,
    reason: str,
    requested_by: str = REQUESTED_BY_CUSTOMER,
    experiment: str = EXPERIMENT_CONTROL_ARM,
    now: datetime | None = None,
) -> ExperimentExclusion:
    """Keep this dock out of the arm. Idempotent on a live exclusion."""
    if not reason or not reason.strip():
        raise ValueError(
            "an exclusion needs a reason - somebody will ask later why the "
            "measurement skipped this dock, and 'no reason recorded' is not an "
            "answer a savings statement can carry"
        )
    if requested_by not in REQUESTED_BY:
        raise ValueError(f"requested_by must be one of {REQUESTED_BY}")

    existing = await live_exclusion(
        session, client_id=client_id, receiver_key=receiver_key, experiment=experiment
    )
    if existing is not None:
        return existing

    exclusion = ExperimentExclusion(
        client_id=client_id,
        receiver_key=receiver_key,
        experiment=experiment,
        reason=reason.strip(),
        requested_by=requested_by,
        excluded_at=now or datetime.now(timezone.utc),
    )
    session.add(exclusion)
    await session.flush()
    return exclusion


async def revoke_exclusion(
    session: AsyncSession,
    *,
    client_id,
    receiver_key: str,
    experiment: str = EXPERIMENT_CONTROL_ARM,
    now: datetime | None = None,
) -> ExperimentExclusion | None:
    """Let a dock back into the arm, keeping the record that it was out.

    The row stays. "This dock was excluded from March to June at the customer's
    request" is part of what a later statement has to be able to explain, and a
    deleted row explains nothing.
    """
    exclusion = await live_exclusion(
        session, client_id=client_id, receiver_key=receiver_key, experiment=experiment
    )
    if exclusion is None:
        return None
    exclusion.revoked_at = now or datetime.now(timezone.utc)
    await session.flush()
    return exclusion


async def live_exclusion(
    session: AsyncSession, *, client_id, receiver_key: str, experiment: str
) -> ExperimentExclusion | None:
    return await session.scalar(
        select(ExperimentExclusion).where(
            ExperimentExclusion.client_id == client_id,
            ExperimentExclusion.receiver_key == receiver_key,
            ExperimentExclusion.experiment == experiment,
            ExperimentExclusion.revoked_at.is_(None),
        )
    )


async def is_excluded(
    session: AsyncSession,
    *,
    client_id,
    receiver_key: str,
    experiment: str = EXPERIMENT_CONTROL_ARM,
) -> bool:
    return (
        await live_exclusion(
            session,
            client_id=client_id,
            receiver_key=receiver_key,
            experiment=experiment,
        )
        is not None
    )


@dataclass(frozen=True)
class ExclusionImpact:
    """What the measurement does not cover, in the terms a statement needs."""

    client_id: object
    live_exclusions: int
    revoked_exclusions: int
    excluded_receivers: tuple[str, ...]
    orders_excluded: int
    orders_total: int

    @property
    def share_of_orders(self) -> float:
        return self.orders_excluded / self.orders_total if self.orders_total else 0.0

    def disclosure(self) -> str:
        """One sentence, written to be pasted into a savings statement.

        Phrased so it survives being read by somebody who did not ask for it:
        the number first, then what it means for the claim.
        """
        if not self.live_exclusions:
            return "No docks were excluded from the measurement."
        return (
            f"{self.live_exclusions} dock(s), carrying {self.share_of_orders:.1%} of "
            f"orders in this window, were excluded from the control arm at request. "
            "The comparison therefore does not cover them, and because docks are "
            "usually excluded for being delivery-sensitive, the excluded traffic is "
            "likely to be where lateness costs the most."
        )


async def exclusion_impact(
    session: AsyncSession,
    *,
    client_id,
    order_counts_by_receiver: dict[str, int] | None = None,
    experiment: str = EXPERIMENT_CONTROL_ARM,
) -> ExclusionImpact:
    """How much of the book the exclusions remove.

    `order_counts_by_receiver` is passed in rather than queried, because this
    package must not learn how an order maps to a dock - that mapping lives in
    `app/identity/`, and importing it here would put an edge dependency on the
    path the dispatch engine reads arm labels through. The caller owns the join.
    """
    rows = list(
        await session.scalars(
            select(ExperimentExclusion).where(
                ExperimentExclusion.client_id == client_id,
                ExperimentExclusion.experiment == experiment,
            )
        )
    )
    live = [r for r in rows if r.is_live]
    counts = order_counts_by_receiver or {}
    excluded_keys = tuple(sorted(r.receiver_key for r in live))
    return ExclusionImpact(
        client_id=client_id,
        live_exclusions=len(live),
        revoked_exclusions=len(rows) - len(live),
        excluded_receivers=excluded_keys,
        orders_excluded=sum(counts.get(key, 0) for key in excluded_keys),
        orders_total=sum(counts.values()),
    )


async def excluded_receiver_count(
    session: AsyncSession, *, client_id, experiment: str = EXPERIMENT_CONTROL_ARM
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(ExperimentExclusion)
            .where(
                ExperimentExclusion.client_id == client_id,
                ExperimentExclusion.experiment == experiment,
                ExperimentExclusion.revoked_at.is_(None),
            )
        )
        or 0
    )
