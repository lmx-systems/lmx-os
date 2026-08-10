"""
Repeat COD disputes per account (docs/ROADMAP.md W2's "repeat-dispute count per account
feeding a monthly owner report").

**A single dispute is a bad afternoon; the same account disputing every month is a
commercial problem**, and it is invisible unless someone counts. That is the whole reason
this is a report and not just a table: a distributor whose customers refuse to pay
regularly is either mispricing, misquoting, or sending us to somebody they should have cut
off - and none of that is visible from one flagged stop.

Grouped by SHOP rather than by client. A distributor can have forty shops, and "your
account has a dispute problem" is not actionable where "the Riverside branch has one" is.
Client totals come along too, since that is the level the monthly conversation happens at.

Includes the collected count beside the disputed one on purpose. Three disputes out of
four deliveries and three out of three hundred are different facts, and a report that only
counts failures makes them look the same.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.cod_notifications import sms_is_configured
from app.models.cod_collection import OUTCOME_COLLECTED, OUTCOME_DISPUTED, CodCollection
from app.models.client import Client
from app.models.shop import Shop

logger = structlog.get_logger(__name__)

# The monthly owner report's default window. A month rather than a rolling 30 days because
# the conversation it feeds is monthly, and a window that doesn't match the meeting makes
# the numbers unarguable-with in the wrong way.
DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class ShopDisputeRow:
    shop_id: str | None
    shop_name: str
    client_id: str | None
    client_name: str
    disputed_count: int
    collected_count: int
    disputed_amount_cents: int

    @property
    def dispute_rate(self) -> float:
        total = self.disputed_count + self.collected_count
        return self.disputed_count / total if total else 0.0


@dataclass(frozen=True)
class CodDisputeReport:
    window_start: datetime
    window_end: datetime
    disputed_count: int
    collected_count: int
    disputed_amount_cents: int
    # Worst first, because the point of the report is which conversation to have.
    shops: list[ShopDisputeRow] = field(default_factory=list)
    # Disputes nobody was told about. Named separately because it breaks the promise the
    # feature makes ("one tap escalates"), and a count buried in a total would hide it.
    unescalated_count: int = 0
    # **Why that count is what it is.** With no SMS provider on this deployment (B5) every
    # dispute is un-escalated, and reporting that as N per-account failures would be a
    # metric that cries wolf permanently - it is one deployment-wide fact, said once.
    sms_configured: bool = True


async def build_cod_dispute_report(
    session: AsyncSession, *, hub_id: str, window_days: int = DEFAULT_WINDOW_DAYS
) -> CodDisputeReport:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)

    # Hub-scoped through the order, since a collection belongs to a hub only by way of the
    # order it settles.
    from app.models.order import Order

    base = (
        select(CodCollection)
        .join(Order, Order.id == CodCollection.order_id)
        .where(Order.hub_id == uuid.UUID(hub_id), CodCollection.occurred_at >= since)
        .subquery()
    )

    totals = (
        await session.execute(
            select(
                func.count().filter(base.c.outcome == OUTCOME_DISPUTED),
                func.count().filter(base.c.outcome == OUTCOME_COLLECTED),
                func.coalesce(
                    func.sum(base.c.amount_due_cents).filter(
                        base.c.outcome == OUTCOME_DISPUTED
                    ),
                    0,
                ),
                func.count().filter(
                    base.c.outcome == OUTCOME_DISPUTED, base.c.escalated_at.is_(None)
                ),
            ).select_from(base)
        )
    ).one()

    per_shop = (
        await session.execute(
            select(
                base.c.shop_id,
                base.c.client_id,
                func.count().filter(base.c.outcome == OUTCOME_DISPUTED),
                func.count().filter(base.c.outcome == OUTCOME_COLLECTED),
                func.coalesce(
                    func.sum(base.c.amount_due_cents).filter(
                        base.c.outcome == OUTCOME_DISPUTED
                    ),
                    0,
                ),
            )
            .select_from(base)
            .group_by(base.c.shop_id, base.c.client_id)
        )
    ).all()

    shop_names = await _names(session, Shop, {row[0] for row in per_shop if row[0]})
    client_names = await _names(session, Client, {row[1] for row in per_shop if row[1]})

    rows = [
        ShopDisputeRow(
            shop_id=str(shop_id) if shop_id else None,
            shop_name=shop_names.get(shop_id, "(unknown shop)"),
            client_id=str(client_id) if client_id else None,
            client_name=client_names.get(client_id, "(unknown client)"),
            disputed_count=int(disputed),
            collected_count=int(collected),
            disputed_amount_cents=int(amount),
        )
        for shop_id, client_id, disputed, collected, amount in per_shop
        # Shops with only clean collections are not what this report is for; including
        # them would bury the handful that matter in a list of every account we serve.
        if disputed
    ]
    rows.sort(key=lambda r: (r.disputed_count, r.disputed_amount_cents), reverse=True)

    return CodDisputeReport(
        window_start=since,
        window_end=now,
        disputed_count=int(totals[0]),
        collected_count=int(totals[1]),
        disputed_amount_cents=int(totals[2]),
        shops=rows,
        unescalated_count=int(totals[3]),
        sms_configured=sms_is_configured(),
    )


async def _names(session: AsyncSession, model, ids: set) -> dict:
    if not ids:
        return {}
    result = await session.execute(select(model.id, model.name).where(model.id.in_(ids)))
    return {row[0]: row[1] for row in result.all()}
