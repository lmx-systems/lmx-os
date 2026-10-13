"""Running the playbook's rules, and acting on them (`DEC-8`).

`run_playbook` is called per hub from the scheduled sweep
(`/internal/dispatch/run-all`), under the hub lock and before that hub's cycle,
so an order the playbook redelivers is placed by the cycle in the same pass. It
does three things, each committed on its own:

1. **Closes calls the world has moved past.** An order that is no longer failed,
   or is on a later attempt, was dealt with some other way - the client
   cancelled it, say - and its open call is `superseded`.
2. **Makes a call on every failed live order that has none** for its current
   attempt, in the rule's current mode (`settings.playbook_failed_delivery_mode`).
   An escalation is closed as it is made: the rule has handed it to a person.
3. **Acts on calls that have fallen due** - in `act` mode at once, in `veto`
   mode `playbook_veto_minutes` later - through the same resolution a person's
   click uses (`app/delivery/resolution.py`).

## A person and the rule at the same moment

Both go through the call's row first. The rule claims it with a conditional
update (`outcome = 'open'`), and a person's resolution locks it
(`record_person_resolution`, from the resolve endpoint) before either touches
the order - one lock order, the call before the order. Whoever commits first
wins. The rule finding its call already closed does nothing; a person finding it
`acted` gets the 409 the order's own compare-and-set gives, as two people
resolving one failure always have.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.delivery.resolution import OrderNotFailedError, ShopMissingError, resolve_failed_order
from app.models.order import INTAKE_LIVE, Order, OrderStatus
from app.models.playbook_decision import (
    MODE_ACT,
    MODE_RECOMMEND,
    MODE_VETO,
    MODES,
    OUTCOME_ACTED,
    OUTCOME_AGREED,
    OUTCOME_ESCALATED,
    OUTCOME_OPEN,
    OUTCOME_OVERRULED,
    OUTCOME_SUPERSEDED,
    PlaybookDecision,
)
from app.models.stop import Stop, StopOrder
from app.playbook.rules import ESCALATE, RULE_FAILED_DELIVERY, decide_failed_delivery

logger = structlog.get_logger(__name__)


@dataclass
class PlaybookSweep:
    called: int = 0
    acted: int = 0
    escalated: int = 0
    superseded: int = 0
    errors: list[str] = field(default_factory=list)


def rule_mode(rule: str) -> str:
    """The mode a rule runs in now. Anything unrecognised is `recommend`: a typo
    in a setting must never be what gives a rule authority."""
    mode = settings.playbook_failed_delivery_mode if rule == RULE_FAILED_DELIVERY else MODE_RECOMMEND
    if mode not in MODES:
        logger.warning("playbook_mode_unrecognised", rule=rule, mode=mode)
        return MODE_RECOMMEND
    return mode


async def run_playbook(
    session: AsyncSession,
    hub_id: str | uuid.UUID,
    *,
    hold_queue: HoldQueueStore | None = None,
    now: datetime | None = None,
) -> PlaybookSweep:
    now = now or datetime.now(timezone.utc)
    hub = uuid.UUID(str(hub_id))
    sweep = PlaybookSweep()
    sweep.superseded += await _supersede_moved_on(session, hub, now)
    await session.commit()
    await _call_failed_deliveries(session, hub, now, sweep)
    await session.commit()
    await _act_on_due(session, hub, now, hold_queue or HoldQueueStore(), sweep)
    return sweep


async def _supersede_moved_on(session: AsyncSession, hub_id: uuid.UUID, now: datetime) -> int:
    result = await session.execute(
        update(PlaybookDecision)
        .where(
            PlaybookDecision.hub_id == hub_id,
            PlaybookDecision.rule == RULE_FAILED_DELIVERY,
            PlaybookDecision.outcome == OUTCOME_OPEN,
            PlaybookDecision.order_id == Order.id,
            or_(
                Order.status != OrderStatus.delivery_failed,
                Order.delivery_attempts != PlaybookDecision.attempt,
            ),
        )
        .values(outcome=OUTCOME_SUPERSEDED, closed_at=now)
        .returning(PlaybookDecision.id)
    )
    return len(result.all())


async def _call_failed_deliveries(
    session: AsyncSession, hub_id: uuid.UUID, now: datetime, sweep: PlaybookSweep
) -> None:
    called = PlaybookDecision.__table__.alias("called")
    uncalled = list(
        await session.scalars(
            select(Order)
            .outerjoin(
                called,
                and_(
                    called.c.order_id == Order.id,
                    called.c.rule == RULE_FAILED_DELIVERY,
                    called.c.attempt == Order.delivery_attempts,
                ),
            )
            .where(
                Order.hub_id == hub_id,
                Order.status == OrderStatus.delivery_failed,
                Order.intake_mode == INTAKE_LIVE,
                called.c.id.is_(None),
            )
            .order_by(Order.requested_at, Order.id)
        )
    )
    if not uncalled:
        return
    mode = rule_mode(RULE_FAILED_DELIVERY)
    failed_at = await _failed_stop_types(session, [order.id for order in uncalled])
    for order in uncalled:
        call = decide_failed_delivery(
            order.failure_reason,
            attempt=order.delivery_attempts,
            failed_at=failed_at.get(order.id),
            max_attempts=settings.playbook_max_delivery_attempts,
        )
        escalate = call.action == ESCALATE
        due_at = None
        if not escalate and mode == MODE_ACT:
            due_at = now
        elif not escalate and mode == MODE_VETO:
            due_at = now + timedelta(minutes=settings.playbook_veto_minutes)
        # ON CONFLICT DO NOTHING: a call made a moment ago by another sweep stands.
        inserted = await session.execute(
            insert(PlaybookDecision)
            .values(
                rule=RULE_FAILED_DELIVERY,
                hub_id=order.hub_id,
                order_id=order.id,
                attempt=order.delivery_attempts,
                action=call.action,
                reason=call.reason,
                mode=mode,
                due_at=due_at,
                outcome=OUTCOME_ESCALATED if escalate else OUTCOME_OPEN,
                closed_at=now if escalate else None,
            )
            .on_conflict_do_nothing(constraint="uq_playbook_decisions_rule_order_attempt")
            .returning(PlaybookDecision.id)
        )
        if inserted.first() is None:
            continue
        sweep.called += 1
        if escalate:
            sweep.escalated += 1


async def _failed_stop_types(session: AsyncSession, order_ids: list) -> dict:
    """Each order's most recently failed stop type, `pickup` or `dropoff`."""
    rows = (
        await session.execute(
            select(StopOrder.order_id, Stop.stop_type)
            .join(Stop, Stop.id == StopOrder.stop_id)
            .where(StopOrder.order_id.in_(order_ids), Stop.status == "failed")
            .order_by(StopOrder.order_id, Stop.flagged_at.desc().nulls_last(), Stop.id)
        )
    ).all()
    latest: dict = {}
    for order_id, stop_type in rows:
        latest.setdefault(order_id, stop_type)
    return latest


async def _act_on_due(
    session: AsyncSession,
    hub_id: uuid.UUID,
    now: datetime,
    hold_queue: HoldQueueStore,
    sweep: PlaybookSweep,
) -> None:
    due_ids = list(
        await session.scalars(
            select(PlaybookDecision.id)
            .where(
                PlaybookDecision.hub_id == hub_id,
                PlaybookDecision.rule == RULE_FAILED_DELIVERY,
                PlaybookDecision.outcome == OUTCOME_OPEN,
                PlaybookDecision.due_at.is_not(None),
                PlaybookDecision.due_at <= now,
            )
            .order_by(PlaybookDecision.due_at, PlaybookDecision.id)
        )
    )
    for decision_id in due_ids:
        # The claim. A person who resolved the order first has closed the call, and
        # one resolving it now holds the row until they commit; either way this
        # finds nothing open and leaves the order alone.
        claimed = (
            await session.execute(
                update(PlaybookDecision)
                .where(PlaybookDecision.id == decision_id, PlaybookDecision.outcome == OUTCOME_OPEN)
                .values(outcome=OUTCOME_ACTED, closed_at=now)
                .returning(PlaybookDecision.order_id, PlaybookDecision.action, PlaybookDecision.attempt)
            )
        ).first()
        if claimed is None:
            await session.rollback()
            continue
        order_id, action, attempt = claimed
        order = await session.get(Order, order_id, populate_existing=True)
        try:
            if (
                order is None
                or order.status != OrderStatus.delivery_failed
                or order.delivery_attempts != attempt
            ):
                raise OrderNotFailedError(f"Order {order_id} is no longer failed on attempt {attempt}")
            await resolve_failed_order(session, hold_queue, order, action)  # commits the claim with it
            sweep.acted += 1
        except OrderNotFailedError:
            await session.rollback()
            await _close(session, decision_id, OUTCOME_SUPERSEDED, now)
            sweep.superseded += 1
        except ShopMissingError as exc:
            # A broken row, not a judgement call: the rule cannot carry it out, so a
            # person gets it, with the reason.
            await session.rollback()
            await _close(session, decision_id, OUTCOME_ESCALATED, now)
            sweep.escalated += 1
            sweep.errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - one order must not stop the rest
            await session.rollback()
            logger.exception("playbook_action_failed", decision_id=str(decision_id))
            sweep.errors.append(f"{decision_id}: {type(exc).__name__}")


async def _close(session: AsyncSession, decision_id, outcome: str, now: datetime) -> None:
    await session.execute(
        update(PlaybookDecision)
        .where(PlaybookDecision.id == decision_id, PlaybookDecision.outcome == OUTCOME_OPEN)
        .values(outcome=outcome, closed_at=now)
    )
    await session.commit()


async def record_person_resolution(
    session: AsyncSession, order: Order, action: str, ops_user_id: str
) -> None:
    """A person is resolving a failed delivery: close the rule's call on it.

    Called by the resolve endpoint before the resolution, in its transaction, so
    the call closes only if the resolution lands. Locks the call first - the same
    order the sweep takes, call before order. An open call becomes `agreed` or
    `overruled`; an escalated one keeps its outcome and gains what the person did,
    which is the answer the rule did not have. A call the rule has already acted
    on is left alone: the resolution behind this will find the order moved and
    refuse.
    """
    decision = await session.scalar(
        select(PlaybookDecision)
        .where(
            PlaybookDecision.rule == RULE_FAILED_DELIVERY,
            PlaybookDecision.order_id == order.id,
            PlaybookDecision.attempt == order.delivery_attempts,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if decision is None:
        return
    if decision.outcome == OUTCOME_OPEN:
        decision.outcome = OUTCOME_AGREED if action == decision.action else OUTCOME_OVERRULED
        decision.closed_at = datetime.now(timezone.utc)
    if decision.outcome in (OUTCOME_AGREED, OUTCOME_OVERRULED, OUTCOME_ESCALATED) and decision.human_action is None:
        decision.human_action = action
        decision.ops_user_id = ops_user_id


async def current_calls(session: AsyncSession, orders: list[Order]) -> dict:
    """The rule's call on each failed order's current attempt, for the exceptions
    queue: open (waiting, or recommended) or escalated (a person's to make)."""
    failed = [order for order in orders if order.status == OrderStatus.delivery_failed]
    if not failed:
        return {}
    attempts = {order.id: order.delivery_attempts for order in failed}
    decisions = await session.scalars(
        select(PlaybookDecision).where(
            PlaybookDecision.rule == RULE_FAILED_DELIVERY,
            PlaybookDecision.order_id.in_(list(attempts)),
            PlaybookDecision.outcome.in_((OUTCOME_OPEN, OUTCOME_ESCALATED)),
        )
    )
    return {d.order_id: d for d in decisions if attempts.get(d.order_id) == d.attempt}
