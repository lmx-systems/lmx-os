"""What the console's playbook panel reads (`DEC-7`, `DEC-8`).

Two questions. **How often does a person still have to act?** - touches per 100
orders over a window, by what they did. **Is each rule ready for more
authority?** - its calls against what people did with the same orders.

## How a rule shows it is ready to move up

From the calls it made in its current mode, all time rather than the window, so
a quiet week does not reset the evidence. In `recommend` mode a person still
acts on every order, so each call they also decided is a yes (`agreed`) or a no
(`overruled`). In `veto` mode the rule acts unless a person stops it, so a call
it carried out with nobody objecting counts as a yes too. It is ready when at
least `playbook_promotion_min_decisions` calls were decided that way and at
least `playbook_promotion_min_agreement` of them were yeses. Moving it is still
a setting a person changes: the panel says the evidence is there, it does not
act on it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ops_touch import CATEGORIES, CATEGORY_DAY, CATEGORY_RECORD, OpsTouch
from app.models.order import INTAKE_LIVE, Order
from app.models.playbook_decision import (
    MODE_ACT,
    MODE_RECOMMEND,
    MODE_VETO,
    OUTCOME_ACTED,
    OUTCOME_AGREED,
    OUTCOME_ESCALATED,
    OUTCOME_OPEN,
    OUTCOME_OVERRULED,
    OUTCOME_SUPERSEDED,
    PlaybookDecision,
)
from app.playbook.rules import RULES
from app.playbook.sweep import rule_mode

_NEXT_MODE = {MODE_RECOMMEND: MODE_VETO, MODE_VETO: MODE_ACT}
_OUTCOMES = (
    OUTCOME_OPEN, OUTCOME_ACTED, OUTCOME_AGREED, OUTCOME_OVERRULED, OUTCOME_ESCALATED, OUTCOME_SUPERSEDED
)


@dataclass(frozen=True)
class TouchCount:
    action: str
    category: str
    count: int


@dataclass(frozen=True)
class Overruled:
    order_id: uuid.UUID
    external_ref: str | None
    rule: str
    rule_action: str
    human_action: str | None
    reason: str
    closed_at: datetime | None


@dataclass
class RuleRecord:
    rule: str
    mode: str
    outcomes: dict[str, int]
    # The evidence for moving up from `mode`, all time.
    yes: int
    no: int
    agreement: float | None
    ready_to_move_up: bool
    next_mode: str | None
    # The bar, so the panel can say how far off it is.
    min_decisions: int
    min_agreement: float


@dataclass
class PlaybookReport:
    since: datetime
    until: datetime
    hub_id: uuid.UUID | None
    orders: int
    touches: int
    touches_per_100_orders: float | None
    by_category: dict[str, int]
    by_action: list[TouchCount]
    # With a hub chosen: touches on no hub at all - a site, a merge - which the
    # hub's rate cannot hold and the all-hubs one does. Zero otherwise.
    unscoped_touches: int = 0
    rules: list[RuleRecord] = field(default_factory=list)
    overruled: list[Overruled] = field(default_factory=list)


async def build_playbook_report(
    session: AsyncSession, *, hub_id: uuid.UUID | None = None, days: int = 7, now: datetime | None = None
) -> PlaybookReport:
    until = now or datetime.now(timezone.utc)
    since = until - timedelta(days=days)

    orders_q = select(func.count()).select_from(Order).where(
        Order.intake_mode == INTAKE_LIVE, Order.requested_at >= since, Order.requested_at < until
    )
    touches_q = (
        select(OpsTouch.action, OpsTouch.category, func.count())
        .where(OpsTouch.touched_at >= since, OpsTouch.touched_at < until)
        .group_by(OpsTouch.action, OpsTouch.category)
    )
    if hub_id is not None:
        orders_q = orders_q.where(Order.hub_id == hub_id)
        # A touch on no particular hub - a merge, a client's rates - is left out
        # of one hub's figure and counted in the all-hubs one.
        touches_q = touches_q.where(OpsTouch.hub_id == hub_id)
    orders = int(await session.scalar(orders_q) or 0)
    by_action = sorted(
        (TouchCount(action=a, category=c, count=int(n)) for a, c, n in (await session.execute(touches_q)).all()),
        key=lambda t: (-t.count, t.action),
    )
    by_category = {category: 0 for category in CATEGORIES}
    for touch in by_action:
        by_category[touch.category] = by_category.get(touch.category, 0) + touch.count
    touches = by_category[CATEGORY_DAY] + by_category[CATEGORY_RECORD]
    unscoped = 0
    if hub_id is not None:
        unscoped = int(
            await session.scalar(
                select(func.count())
                .select_from(OpsTouch)
                .where(
                    OpsTouch.touched_at >= since,
                    OpsTouch.touched_at < until,
                    OpsTouch.hub_id.is_(None),
                    OpsTouch.category.in_((CATEGORY_DAY, CATEGORY_RECORD)),
                )
            )
            or 0
        )

    report = PlaybookReport(
        since=since,
        until=until,
        hub_id=hub_id,
        orders=orders,
        touches=touches,
        touches_per_100_orders=round(100.0 * touches / orders, 1) if orders else None,
        by_category=by_category,
        by_action=by_action,
        unscoped_touches=unscoped,
    )
    for rule in RULES:
        report.rules.append(await _rule_record(session, rule, hub_id, since, until))
    report.overruled = await _recent_overruled(session, hub_id)
    return report


async def _rule_record(
    session: AsyncSession, rule: str, hub_id: uuid.UUID | None, since: datetime, until: datetime
) -> RuleRecord:
    mode = rule_mode(rule)
    window = select(PlaybookDecision.outcome, func.count()).where(
        PlaybookDecision.rule == rule,
        PlaybookDecision.created_at >= since,
        PlaybookDecision.created_at < until,
    )
    evidence = select(PlaybookDecision.outcome, func.count()).where(
        PlaybookDecision.rule == rule, PlaybookDecision.mode == mode
    )
    if hub_id is not None:
        window = window.where(PlaybookDecision.hub_id == hub_id)
        evidence = evidence.where(PlaybookDecision.hub_id == hub_id)
    outcomes = {outcome: 0 for outcome in _OUTCOMES}
    for outcome, n in (await session.execute(window.group_by(PlaybookDecision.outcome))).all():
        outcomes[outcome] = int(n)
    seen = dict((await session.execute(evidence.group_by(PlaybookDecision.outcome))).all())
    yes = int(seen.get(OUTCOME_AGREED, 0)) + (int(seen.get(OUTCOME_ACTED, 0)) if mode == MODE_VETO else 0)
    no = int(seen.get(OUTCOME_OVERRULED, 0))
    agreement = yes / (yes + no) if yes + no else None
    ready = (
        mode != MODE_ACT
        and yes + no >= settings.playbook_promotion_min_decisions
        and agreement is not None
        and agreement >= settings.playbook_promotion_min_agreement
    )
    return RuleRecord(
        rule=rule,
        mode=mode,
        outcomes=outcomes,
        yes=yes,
        no=no,
        agreement=round(agreement, 3) if agreement is not None else None,
        ready_to_move_up=ready,
        next_mode=_NEXT_MODE.get(mode),
        min_decisions=settings.playbook_promotion_min_decisions,
        min_agreement=settings.playbook_promotion_min_agreement,
    )


async def _recent_overruled(session: AsyncSession, hub_id: uuid.UUID | None) -> list[Overruled]:
    """The latest calls a person disagreed with: the rule's labelled mistakes."""
    query = (
        select(PlaybookDecision, Order.external_order_ref)
        .outerjoin(Order, Order.id == PlaybookDecision.order_id)
        .where(PlaybookDecision.outcome == OUTCOME_OVERRULED)
        .order_by(PlaybookDecision.closed_at.desc().nulls_last(), PlaybookDecision.id)
        .limit(10)
    )
    if hub_id is not None:
        query = query.where(PlaybookDecision.hub_id == hub_id)
    return [
        Overruled(
            order_id=decision.order_id,
            external_ref=ref,
            rule=decision.rule,
            rule_action=decision.action,
            human_action=decision.human_action,
            reason=decision.reason,
            closed_at=decision.closed_at,
        )
        for decision, ref in (await session.execute(query)).all()
    ]
