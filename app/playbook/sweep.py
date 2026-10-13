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
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.clustering import miles_between
from app.batch_queue.store import HoldQueueStore
from app.compliance.driver_documents import evaluate_driver_documents
from app.config import settings
from app.delivery.resolution import OrderNotFailedError, ShopMissingError, resolve_failed_order
from app.fleet_state.manager import FleetStateManager
from app.hub_calendar import is_hub_closed_at
from app.models.driver import Driver
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
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.optimizer.service import _position_is_stale
from app.orders.unplaced import unplaced_clause
from app.playbook.rules import (
    ESCALATE,
    OFFER,
    OFFER_WITH_BONUS,
    RULE_FAILED_DELIVERY,
    RULE_UNPLACED,
    Candidate,
    OfferHistory,
    decide_failed_delivery,
    decide_unplaced,
)

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
    fleet: FleetStateManager | None = None,
    now: datetime | None = None,
) -> PlaybookSweep:
    now = now or datetime.now(timezone.utc)
    hub = uuid.UUID(str(hub_id))
    sweep = PlaybookSweep()
    await _follow_current_mode(session, hub, now)
    sweep.superseded += await _supersede_moved_on(session, hub, now)
    await session.commit()
    await _call_failed_deliveries(session, hub, now, sweep)
    await session.commit()
    await _act_on_due(session, hub, now, hold_queue or HoldQueueStore(), sweep)
    # Orders nobody has (`DEC-10`): recommends only, so nothing here acts.
    sweep.superseded += await _close_unplaced_moved_on(session, hub, now)
    await session.commit()
    await _call_unplaced(session, hub, now, sweep, fleet or FleetStateManager())
    await session.commit()
    return sweep


_MODE_RANK = {MODE_RECOMMEND: 0, MODE_VETO: 1, MODE_ACT: 2}


async def _follow_current_mode(session: AsyncSession, hub_id: uuid.UUID, now: datetime) -> None:
    """Calls made in a higher mode than the rule has now come down to it.

    The mode setting is the off switch. A rule turned back from `veto` to
    `recommend` because it was misbehaving must not go on to carry out the calls
    it scheduled before - so they become recommendations, with no time to act,
    and the exceptions queue stops saying it will act. Brought down to `veto`,
    a call from `act` waits the veto window from now. Their evidence then counts
    in the mode they ended in, which is the one a person judged them in.
    """
    current = rule_mode(RULE_FAILED_DELIVERY)
    higher = [mode for mode, rank in _MODE_RANK.items() if rank > _MODE_RANK[current]]
    if not higher:
        return
    due_at = now + timedelta(minutes=settings.playbook_veto_minutes) if current == MODE_VETO else None
    await session.execute(
        update(PlaybookDecision)
        .where(
            PlaybookDecision.hub_id == hub_id,
            PlaybookDecision.rule == RULE_FAILED_DELIVERY,
            PlaybookDecision.outcome == OUTCOME_OPEN,
            PlaybookDecision.mode.in_(higher),
        )
        .values(mode=current, due_at=due_at)
    )


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
    if rule_mode(RULE_FAILED_DELIVERY) == MODE_RECOMMEND:
        # Belt and braces with _follow_current_mode: a recommending rule never acts.
        return
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
            # Commits the claim with it. No wake-up: the cycle that run-all starts
            # next for this hub places the redelivery, and a wake-up at a deadline
            # already past would start a second one within a second.
            await resolve_failed_order(session, hold_queue, order, action, schedule_wakeup=False)
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


async def _close_unplaced_moved_on(session: AsyncSession, hub_id: uuid.UUID, now: datetime) -> int:
    """An unplaced order's call is current until the order stops being unplaced -
    the cycle placed it, a client cancelled it - or a person acts on it. Then an
    open call is superseded, and an escalation simply ends."""
    still_unplaced = select(Order.id).where(Order.hub_id == hub_id, unplaced_clause(now))
    current = (
        PlaybookDecision.hub_id == hub_id,
        PlaybookDecision.rule == RULE_UNPLACED,
        PlaybookDecision.closed_at.is_(None),
        PlaybookDecision.order_id.notin_(still_unplaced),
    )
    superseded = await session.execute(
        update(PlaybookDecision)
        .where(*current, PlaybookDecision.outcome == OUTCOME_OPEN)
        .values(outcome=OUTCOME_SUPERSEDED, closed_at=now)
        .returning(PlaybookDecision.id)
    )
    await session.execute(
        update(PlaybookDecision)
        .where(*current, PlaybookDecision.outcome == OUTCOME_ESCALATED)
        .values(closed_at=now)
    )
    return len(superseded.all())


async def _call_unplaced(
    session: AsyncSession, hub_id: uuid.UUID, now: datetime, sweep: PlaybookSweep, fleet: FleetStateManager
) -> None:
    """One call per spell of being unplaced: an order with no current call gets one.

    A call stays current (`closed_at` null) until the order moves or a person
    acts, escalations included - so an order nobody can take is escalated once,
    not every sweep. The next spell, after a declined offer say, is a new call
    with the next `attempt`.
    """
    called = PlaybookDecision.__table__.alias("called")
    uncalled = list(
        await session.execute(
            select(Order, Shop)
            .join(Shop, Shop.id == Order.shop_id)
            .outerjoin(
                called,
                and_(
                    called.c.order_id == Order.id,
                    called.c.rule == RULE_UNPLACED,
                    called.c.closed_at.is_(None),
                ),
            )
            .where(Order.hub_id == hub_id, unplaced_clause(now), called.c.id.is_(None))
            .order_by(Order.requested_at, Order.id)
        )
    )
    if not uncalled:
        return
    hub_closed = await is_hub_closed_at(session, str(hub_id), now)
    candidates = [] if hub_closed else await _free_drivers(session, hub_id, now, fleet)
    mode = rule_mode(RULE_UNPLACED)
    for order, shop in uncalled:
        history, turned_down_by = await _offer_history(session, order.id)
        call = decide_unplaced(
            [
                Candidate(
                    driver_id=c.driver_id,
                    name=c.name,
                    miles=(
                        round(miles_between(position[0], position[1], float(shop.lat), float(shop.lng)), 1)
                        if position is not None
                        else None
                    ),
                    position_stale=c.position_stale,
                    turned_it_down=c.driver_id in turned_down_by,
                )
                for c, position in candidates
            ],
            hub_closed=hub_closed,
            history=history,
            bonus_cents=settings.offer_bonus_cents,
        )
        escalate = call.action == ESCALATE
        next_attempt = (
            select(func.coalesce(func.max(PlaybookDecision.attempt), 0) + 1)
            .where(PlaybookDecision.rule == RULE_UNPLACED, PlaybookDecision.order_id == order.id)
            .scalar_subquery()
        )
        inserted = await session.execute(
            insert(PlaybookDecision)
            .values(
                rule=RULE_UNPLACED,
                hub_id=order.hub_id,
                order_id=order.id,
                attempt=next_attempt,
                action=call.action,
                reason=call.reason,
                target=call.target,
                mode=mode,
                due_at=None,
                outcome=OUTCOME_ESCALATED if escalate else OUTCOME_OPEN,
            )
            .on_conflict_do_nothing(constraint="uq_playbook_decisions_rule_order_attempt")
            .returning(PlaybookDecision.id)
        )
        if inserted.first() is None:
            continue
        sweep.called += 1
        if escalate:
            sweep.escalated += 1


async def _offer_history(session: AsyncSession, order_id: uuid.UUID) -> tuple[OfferHistory, set[str]]:
    """This order's offers that were declined or left to lapse, and by whom."""
    offers = list(
        await session.scalars(
            select(RouteOffer).where(
                RouteOffer.stop_payload.contains([{"order_id": str(order_id)}]),
                RouteOffer.status.in_(("declined", "expired")),
            )
        )
    )
    history = OfferHistory(
        turned_down=len(offers), bonus_tried=any(offer.bonus_cents for offer in offers)
    )
    return history, {str(offer.driver_id) for offer in offers}


async def _free_drivers(
    session: AsyncSession, hub_id: uuid.UUID, now: datetime, fleet: FleetStateManager
) -> list[tuple[Candidate, tuple[float, float] | None]]:
    """Drivers a person's assign would accept (app/orders/assignment.py): idle
    with no route, room on the van, switched on, papers in order. With their last
    position, which the caller turns into a distance per shop."""
    states = [
        state
        for state in await fleet.get_fleet_overview(str(hub_id))
        if state.status == "available"
        and not state.current_route_id
        and state.capacity_units - state.load_units >= 1
    ]
    if not states:
        return []
    drivers = {
        str(driver.id): driver
        for driver in await session.scalars(
            select(Driver).where(
                Driver.id.in_([uuid.UUID(state.driver_id) for state in states]),
                Driver.hub_id == hub_id,
                Driver.is_active.is_(True),
            )
        )
    }
    free: list[tuple[Candidate, tuple[float, float] | None]] = []
    for state in states:
        driver = drivers.get(state.driver_id)
        if driver is None or not (await evaluate_driver_documents(session, state.driver_id)).can_go_on_shift:
            continue
        location = await fleet.get_driver_location(str(hub_id), state.driver_id)
        stale = location is None or _position_is_stale(location.recorded_at, now)
        position = (location.lat, location.lng) if location is not None else None
        free.append(
            (
                Candidate(driver_id=state.driver_id, name=driver.name, miles=None, position_stale=stale),
                position,
            )
        )
    return free


async def record_person_action(
    session: AsyncSession,
    order_id: uuid.UUID,
    action: str,
    ops_user_id: str,
    *,
    target: str | None = None,
) -> None:
    """A person is acting on an order nobody had: close the rule's current call.

    From the assign (`offer`, with the driver), the hold override (`hold` or
    `release`) and dispatch's cancel, before they lock the order and in their
    transaction - the call before the order, as everywhere. An open call is
    `agreed` only when the person offered it to the driver the call named, with
    a bonus or without as the call said; anything else overrules it. An escalation keeps its outcome and gains the
    answer it did not have.
    """
    decision = await session.scalar(
        select(PlaybookDecision)
        .where(
            PlaybookDecision.rule == RULE_UNPLACED,
            PlaybookDecision.order_id == order_id,
            PlaybookDecision.closed_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if decision is None:
        return
    if decision.outcome == OUTCOME_OPEN:
        same = action == decision.action and action in (OFFER, OFFER_WITH_BONUS) and target == decision.target
        decision.outcome = OUTCOME_AGREED if same else OUTCOME_OVERRULED
    decision.closed_at = datetime.now(timezone.utc)
    decision.human_action = action
    decision.human_target = target
    decision.ops_user_id = ops_user_id


async def current_calls(session: AsyncSession, orders: list[Order]) -> dict:
    """Each rule's current call on these orders, for the exceptions queue: open
    (waiting, or recommended) or escalated (a person's to make).

    A failed delivery's call is the one for its current attempt; an unplaced
    order's is the one still current for this spell of being unplaced.
    """
    calls: dict = {}
    failed = [order for order in orders if order.status == OrderStatus.delivery_failed]
    if failed:
        attempts = {order.id: order.delivery_attempts for order in failed}
        decisions = await session.scalars(
            select(PlaybookDecision).where(
                PlaybookDecision.rule == RULE_FAILED_DELIVERY,
                PlaybookDecision.order_id.in_(list(attempts)),
                PlaybookDecision.outcome.in_((OUTCOME_OPEN, OUTCOME_ESCALATED)),
            )
        )
        calls.update({d.order_id: d for d in decisions if attempts.get(d.order_id) == d.attempt})
    waiting = [order.id for order in orders if order.status in (OrderStatus.held, OrderStatus.queued)]
    if waiting:
        decisions = await session.scalars(
            select(PlaybookDecision).where(
                PlaybookDecision.rule == RULE_UNPLACED,
                PlaybookDecision.order_id.in_(waiting),
                PlaybookDecision.closed_at.is_(None),
            )
        )
        calls.update({d.order_id: d for d in decisions})
    return calls
