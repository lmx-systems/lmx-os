"""What we promised, in one place.

**Two promises existed and neither behaved like one.**

`app/billing/credits.py` computes a delivery commitment from contract data and charges
us a credit for missing it. Its own docstring is blunt about the other one: *"`app/sla/
engine.py` defines HOLD windows, which are when we must set off, not when the customer
gets their part. `hold_deadline` is ours and internal."*

Meanwhile `app/api/client_routes.py` reports that internal `hold_deadline` to the client
as `collect_by`, and the confirmation screen presents it as "we'll collect by 2:40 PM".
So the system showed a commitment it never measured, and measured a commitment it never
showed - and the customer owed the credit could not see the number the credit was
assessed against.

This module is the delivery commitment, defined once. `credits.py` and the client-facing
views both read it, which is the only way the figure on a statement and the figure on a
screen cannot drift apart.

**It reports its own source.** "We told them 3:25 out loud", "their contract says T2 is
180 minutes" and "nobody ever wrote down what we owe this client" are three different
answers, and the third is not a time. Returning `source` keeps the caller from having to
infer that from a null - `credits.py` needs it to report an order as unassessable rather
than clean, and a client view needs it to show nothing rather than a fabricated promise.

Deliberately not here: the *collection* commitment. `hold_deadline` is when an order
leaves the batch-hold queue, so the real collection is later by however long it takes a
driver to get there - which makes the number currently shown to clients optimistic by
construction. Fixing that means deciding what the collection promise actually is, per
tier, alongside `delivery_target_minutes` (docs/ROADMAP.md E11). Until then this module
covers the promise that carries money, and `collected_at` on the client views makes the
other one checkable instead of merely stated.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client_sla_term import ClientSlaTerm
from app.models.order import Order

CommitmentSource = Literal["explicit", "tier_term", "none"]


@dataclass(frozen=True)
class Commitment:
    """When this order was due, and on what authority."""

    promised_delivery_by: datetime | None
    source: CommitmentSource

    @property
    def exists(self) -> bool:
        return self.promised_delivery_by is not None


NO_COMMITMENT = Commitment(promised_delivery_by=None, source="none")


def delivery_commitment(order: Order, term: ClientSlaTerm | None) -> Commitment:
    """The delivery time this order is judged against.

    Two sources, in the order that authority runs:

      - **`promised_at` wins.** If we told this customer a specific time, that is the
        promise; a per-tier default cannot override something said out loud. Populated
        only when a source system hands us one - for an LMX-owned order, never - which
        is precisely why the tier term below had to exist before a credit was chargeable.
      - **The client's contract term for this tier**, measured from `requested_at`. Per
        client and per tier, recorded as contract data rather than as a constant chosen
        in whichever module happens to need it.

    No term and no explicit promise is `source="none"`, not a guess. Inventing a target
    would mean either charging ourselves a credit against a number nobody agreed, or
    telling a customer we owe them something we never promised.
    """
    if order.promised_at is not None:
        return Commitment(promised_delivery_by=order.promised_at, source="explicit")

    if term is not None and order.requested_at is not None:
        return Commitment(
            promised_delivery_by=order.requested_at
            + timedelta(minutes=term.delivery_target_minutes),
            source="tier_term",
        )

    return NO_COMMITMENT


async def terms_for_client(
    session: AsyncSession, client_id: uuid.UUID
) -> dict[str, ClientSlaTerm]:
    """This client's service-level terms, keyed by tier.

    Here rather than in either caller, so billing and the client-facing views read the
    same contract rows through the same query. One round trip per statement or per page,
    not per order.
    """
    result = await session.execute(
        select(ClientSlaTerm).where(ClientSlaTerm.client_id == client_id)
    )
    return {term.sla_tier: term for term in result.scalars().all()}
