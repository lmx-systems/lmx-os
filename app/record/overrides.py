"""The dispatcher override, and the reason that makes it one (`CON-2`, `CON-3`).

`CON-2`: *"no override completes without a reason."* `CON-3`: *"every override
lands in the decision log as a labelled example."*

There was no override. Nothing in `app/api/` let a dispatcher release a held
order or hold a released one, so the mandatory reason code had nothing to attach
to and the training data had no source. Both done-whens needed the thing itself
built first - the same shape `AGT-4` turned up one layer down, where the reasons
existed and were discarded.

## Why the reason is enforced in three places and not one

The Pydantic model rejects a request without a reason code. The database rejects
a row without one. This module rejects an `other` with no note.

That is not belt and braces, it is three different failures. The schema stops a
malformed request. The constraint stops a script, a fixture, or a future
endpoint that forgets - and CON-2 says *no override completes* without a reason,
not *no request*. The note rule stops the one an enum cannot see: `other` with
nothing written is an override with no reason wearing the costume of one, and it
is what a hurried dispatcher will reach for every time if it is allowed.

## The label, and when there isn't one

An override is training data only when we know what it overrode. `CON-3`'s
example is the pair - *the queue said hold because no cluster mate had arrived;
the dispatcher said release because the customer called*. When no cycle had
recorded a decision, the override is still performed and still recorded in full,
and `system_decision_known` is false. `labelled_overrides` skips it.

Inferring the queue's position from the order's status instead would produce a
label out of the queue's silence, and by the time anyone trained on it there
would be no way to tell it from a real one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dispatcher_override import (
    ACTION_HOLD,
    ACTION_RELEASE,
    ACTIONS,
    REASON_CODES,
    REASON_CODES_REQUIRING_NOTE,
    DispatcherOverride,
)
from app.models.order import Order, OrderStatus
from app.orders.state_machine import assert_transition
from app.record.explain import latest_system_decision

# What each action means as a status move, and what it may be applied to.
#
# Release takes an order out of the hold queue without waiting for the next
# cycle; hold puts a released one back. Hold is deliberately not permitted from
# `assigned`: a driver has an offer in front of them by then, and retracting it
# is a different operation with a driver-facing consequence, not a queue
# decision. Letting one button do both would make the quiet case - pulling an
# order back before anyone has seen it - indistinguishable from the loud one.
_APPLICABLE_FROM: dict[str, tuple[OrderStatus, ...]] = {
    ACTION_RELEASE: (OrderStatus.held, OrderStatus.classified),
    ACTION_HOLD: (OrderStatus.queued,),
}

_RESULTING_STATUS: dict[str, OrderStatus] = {
    ACTION_RELEASE: OrderStatus.queued,
    ACTION_HOLD: OrderStatus.held,
}


class OverrideRefused(Exception):
    """The override did not happen, and the message says why to a dispatcher.

    Every path out of here is something a person can act on: pick a reason, say
    what "other" means, or look at the order again because it has moved. None of
    them is a stack trace.
    """


@dataclass(frozen=True)
class OverrideOutcome:
    """What happened, and whether it was a disagreement."""

    override: DispatcherOverride
    previous_status: OrderStatus
    new_status: OrderStatus

    @property
    def contradicted_the_system(self) -> bool:
        return self.override.contradicts_the_system


async def apply_override(
    session: AsyncSession,
    *,
    order_id,
    action: str,
    reason_code: str,
    ops_user_id: str,
    ops_user_email: str,
    note: str | None = None,
    now: datetime | None = None,
) -> OverrideOutcome:
    """Overrule the queue on one order, and record the disagreement.

    Raises `OverrideRefused` rather than performing a partial override. The
    status change and the record are written in one transaction on purpose: an
    order that moved with no row saying who moved it is the state CON-2 exists
    to make impossible, and it is exactly what a two-step version produces the
    first time the second step fails.
    """
    now = now or datetime.now(timezone.utc)
    note = note.strip() if note else None

    if action not in ACTIONS:
        raise OverrideRefused(f"{action!r} is not an override. Choose release or hold.")
    if reason_code not in REASON_CODES:
        raise OverrideRefused(
            f"{reason_code!r} is not a reason code. Pick one from the list."
        )
    if reason_code in REASON_CODES_REQUIRING_NOTE and not note:
        raise OverrideRefused(
            "Say what the reason was. 'Other' with nothing written is an override "
            "with no reason."
        )

    order = await session.get(Order, order_id)
    if order is None:
        raise OverrideRefused("No such order.")

    previous = order.status
    applicable = _APPLICABLE_FROM[action]
    if previous not in applicable:
        raise OverrideRefused(
            f"This order is {previous.value}, and {action} applies to "
            f"{' or '.join(s.value for s in applicable)}. It may have moved since "
            "the screen was loaded - look at it again."
        )

    new_status = _RESULTING_STATUS[action]
    # Belt from the one machine that owns lifecycle legality. If this raises, the
    # table above and the state machine disagree, and the state machine wins.
    assert_transition(previous, new_status)

    system = await latest_system_decision(session, order_id=order_id)

    override = DispatcherOverride(
        hub_id=order.hub_id,
        order_id=order.id,
        overridden_at=now,
        ops_user_id=ops_user_id,
        ops_user_email=ops_user_email,
        action=action,
        reason_code=reason_code,
        note=note,
        system_action=system.action if system.known else None,
        system_reason=system.reason if system.known else None,
        system_decision_known=system.known,
        cited_snapshot_id=system.snapshot_id if system.known else None,
        order_status_before=previous.value,
    )
    session.add(override)

    order.status = new_status
    await session.flush()

    return OverrideOutcome(
        override=override, previous_status=previous, new_status=new_status
    )


async def overrides_for_order(session: AsyncSession, *, order_id) -> list[DispatcherOverride]:
    """Every override on one order, oldest first.

    A list rather than the latest: an order released, pulled back, and released
    again is three decisions by up to three people, and only the sequence says
    that. `AGT-4`'s explanation reads this so the console shows the human
    decisions beside the system's.
    """
    return list(
        await session.scalars(
            select(DispatcherOverride)
            .where(DispatcherOverride.order_id == order_id)
            .order_by(DispatcherOverride.overridden_at)
        )
    )


async def labelled_overrides(
    session: AsyncSession,
    *,
    hub_id=None,
    since: datetime | None = None,
    contradictions_only: bool = True,
) -> list[DispatcherOverride]:
    """The overrides that are usable as training data (`CON-3`).

    Two filters, both of which remove rows that would otherwise look like signal:

    `system_decision_known` - an override of a decision nobody recorded is not an
    example of the queue being wrong, because nothing says what the queue thought.

    `contradictions_only` - a dispatcher releasing an order the queue had already
    decided to release agrees with it and got there first. Counting that as a
    correction inflates the disagreement rate, which is the first number anyone
    judging the queue will look at. Defaults on; pass false to count both, which
    is what the denominator needs.
    """
    query = select(DispatcherOverride).where(DispatcherOverride.system_decision_known)
    if hub_id is not None:
        query = query.where(DispatcherOverride.hub_id == hub_id)
    if since is not None:
        query = query.where(DispatcherOverride.overridden_at >= since)

    rows = list(await session.scalars(query.order_by(DispatcherOverride.overridden_at)))
    if contradictions_only:
        rows = [row for row in rows if row.contradicts_the_system]
    return rows
