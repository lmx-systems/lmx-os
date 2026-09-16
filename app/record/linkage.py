"""Noticing that two pieces of work are about the same thing (REC-4).

Three detectors, each finding a different failure to join two facts the system
already holds separately. None of them needs a model, and that is the point:
the roadmap files `PRD-2` as *"a join and a rule, not a model"*, and these are
the same shape. The value is in asking the question at all.

**Every detector proposes and none decides.** Each flag has a legitimate
explanation available - a genuinely urgent second order, two branches that both
really needed the part - so a flag is a question put to a dispatcher, never an
action. That is the same rule IDN-2's merge queue follows, for the same reason:
the expensive mistake is the invisible one, and a system that silently
cancelled a "duplicate" would make exactly that mistake.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.account_signals import parse_account_ref
from app.models.linkage_flag import (
    KIND_DUPLICATE_BRANCH,
    KIND_OPEN_RETURN,
    KIND_REPEAT_VISIT,
    LinkageFlag,
)
from app.models.order import Order
from app.models.return_item import ReturnItem
from app.models.shop import Shop

# How far back a detector looks. A window rather than the whole table because
# these are operational prompts - a duplicate order from March is history, not
# something a dispatcher can act on this morning.
DEFAULT_WINDOW = timedelta(days=7)

# A return older than this is a different conversation. Below it, "we are going
# there anyway" is the useful observation; above it, the question is why nobody
# has collected it at all, which is a report rather than a flag.
STALE_RETURN_AFTER = timedelta(days=30)


def _fingerprint(*parts: object) -> str:
    """Stable across runs and independent of argument order.

    Sorted before hashing so the same pair of orders fingerprints the same
    whichever way round the query returned them - otherwise re-running would
    raise a second flag for a pair somebody already dismissed.
    """
    joined = "|".join(sorted(str(p) for p in parts if p is not None))
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


async def _raise_flag(
    session: AsyncSession,
    *,
    hub_id,
    kind: str,
    subjects: dict,
    detail: str,
    detected_at: datetime,
    fingerprint: str,
) -> bool:
    """Record a flag unless this exact observation already exists.

    ON CONFLICT DO NOTHING against the (kind, fingerprint) constraint, so a
    detector that runs every cycle does not rebuild the same queue each time.
    Returns whether a new flag was created, which is what the callers count.
    """
    statement = (
        pg_insert(LinkageFlag)
        .values(
            hub_id=hub_id,
            kind=kind,
            fingerprint=fingerprint,
            subjects=subjects,
            detail=detail[:500],
            detected_at=detected_at,
        )
        .on_conflict_do_nothing(constraint="uq_linkage_flags_kind_fingerprint")
        .returning(LinkageFlag.id)
    )
    return (await session.execute(statement)).first() is not None


async def flag_open_returns_on_visits(
    session: AsyncSession, *, hub_id, now: datetime | None = None
) -> int:
    """A van is going to a dock that already has a return waiting.

    The $82 core part, in general form. Nothing here is broken - the delivery
    is fine and the return is recorded - and that is exactly why it goes
    unnoticed: two correct records, and no query that puts them side by side
    before the van leaves.

    Only undelivered orders are considered. Flagging a trip that has already
    happened tells a dispatcher something they can no longer act on, which is
    how a queue becomes noise.
    """
    now = now or datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(Order, ReturnItem)
            .join(ReturnItem, ReturnItem.shop_id == Order.shop_id)
            .where(
                Order.hub_id == hub_id,
                Order.shop_id.is_not(None),
                Order.delivered_at.is_(None),
                ReturnItem.collected_at.is_(None),
                ReturnItem.returned_at.is_(None),
                ReturnItem.created_at >= now - STALE_RETURN_AFTER,
            )
        )
    ).all()

    raised = 0
    for order, pending_return in rows:
        waiting_days = max((now - pending_return.created_at).days, 0)
        if await _raise_flag(
            session,
            hub_id=hub_id,
            kind=KIND_OPEN_RETURN,
            subjects={
                "order_id": str(order.id),
                "return_item_id": str(pending_return.id),
                "shop_id": str(order.shop_id),
            },
            detail=(
                f"A return has been waiting at this shop for {waiting_days} day(s) "
                f"({pending_return.manifest[:120]}) and there is an undelivered order "
                f"going to the same place. One trip could do both."
            ),
            detected_at=now,
            fingerprint=_fingerprint(order.id, pending_return.id),
        ):
            raised += 1
    if raised:
        await session.flush()
    return raised


async def flag_duplicates_across_branches(
    session: AsyncSession, *, hub_id, now: datetime | None = None, window=DEFAULT_WINDOW
) -> int:
    """The same order reference, from two branches of one account.

    Branches share an account root - `ROOT/BRANCH` (see
    `app/identity/account_signals.py`) - so two orders carrying the same
    external reference under one root are either a genuine double-order or one
    delivery about to be made and billed twice.

    Deliberately keyed on the customer's own reference rather than on anything
    we derived. A distributor reusing a PO across branches is a fact about their
    process; a similarity score would be a guess about it.
    """
    now = now or datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(Order, Shop.external_ref)
            .join(Shop, Order.shop_id == Shop.id)
            .where(
                Order.hub_id == hub_id,
                Order.created_at >= now - window,
                Shop.external_ref.is_not(None),
            )
        )
    ).all()

    # reference -> account root -> the orders carrying it
    by_reference: dict[tuple[str, str], list] = {}
    for order, account_ref in rows:
        parsed = parse_account_ref(account_ref)
        if parsed is None or not order.external_order_ref:
            continue
        by_reference.setdefault((order.external_order_ref, parsed.root), []).append(
            (order, account_ref)
        )

    raised = 0
    for (reference, root), group in by_reference.items():
        branches = {ref for _, ref in group}
        # Two orders at the SAME branch with one reference is a re-send, which
        # the idempotency key already handles. This is about two branches.
        if len(branches) < 2:
            continue
        orders = [order for order, _ in group]
        if await _raise_flag(
            session,
            hub_id=hub_id,
            kind=KIND_DUPLICATE_BRANCH,
            subjects={
                "order_ids": sorted(str(o.id) for o in orders),
                "reference": reference,
                "account_root": root,
                "branches": sorted(branches),
            },
            detail=(
                f"Reference {reference} was ordered by {len(branches)} branches of "
                f"account {root} ({', '.join(sorted(branches))}). Either a genuine "
                f"double-order, or one delivery about to be billed twice."
            ),
            detected_at=now,
            fingerprint=_fingerprint(*(o.id for o in orders)),
        ):
            raised += 1
    if raised:
        await session.flush()
    return raised


async def flag_repeat_visits(
    session: AsyncSession, *, hub_id, now: datetime | None = None, window=DEFAULT_WINDOW
) -> int:
    """The same dock, more than once in a day, on separate orders.

    Often unavoidable - a second order genuinely arrived after the van left.
    Often not: the second order was in the system while the first was still
    being loaded, and nothing looked. Batching is the product, so a dock being
    visited twice in one day is the product not having worked, and worth a
    dispatcher's glance even when the answer is "that one was fine".

    Grouped by calendar day in UTC, which is a simplification worth naming: a
    hub whose shift crosses midnight will see one evening split across two
    days. Fixing that needs the hub's own timezone, which `hub_calendar` knows
    and this does not yet ask it for.
    """
    now = now or datetime.now(timezone.utc)
    orders = list(
        await session.scalars(
            select(Order).where(
                Order.hub_id == hub_id,
                Order.shop_id.is_not(None),
                Order.created_at >= now - window,
            )
        )
    )

    by_day: dict[tuple, list] = {}
    for order in orders:
        by_day.setdefault((order.shop_id, order.created_at.date()), []).append(order)

    raised = 0
    for (shop_id, day), group in by_day.items():
        if len(group) < 2:
            continue
        if await _raise_flag(
            session,
            hub_id=hub_id,
            kind=KIND_REPEAT_VISIT,
            subjects={
                "shop_id": str(shop_id),
                "day": day.isoformat(),
                "order_ids": sorted(str(o.id) for o in group),
            },
            detail=(
                f"{len(group)} separate orders to the same shop on {day.isoformat()}. "
                f"If they were not batched, that is two trips where one would do."
            ),
            detected_at=now,
            fingerprint=_fingerprint(shop_id, day, *(o.id for o in group)),
        ):
            raised += 1
    if raised:
        await session.flush()
    return raised


async def run_linkage_detectors(
    session: AsyncSession, *, hub_id, now: datetime | None = None
) -> dict:
    """All three, reporting how many new questions each raised."""
    now = now or datetime.now(timezone.utc)
    return {
        KIND_OPEN_RETURN: await flag_open_returns_on_visits(session, hub_id=hub_id, now=now),
        KIND_DUPLICATE_BRANCH: await flag_duplicates_across_branches(
            session, hub_id=hub_id, now=now
        ),
        KIND_REPEAT_VISIT: await flag_repeat_visits(session, hub_id=hub_id, now=now),
    }


async def open_flags(session: AsyncSession, *, hub_id) -> list[LinkageFlag]:
    """The queue a dispatcher works, oldest first."""
    return list(
        await session.scalars(
            select(LinkageFlag)
            .where(LinkageFlag.hub_id == hub_id, LinkageFlag.resolved_at.is_(None))
            .order_by(LinkageFlag.detected_at)
        )
    )


async def resolve_flag(
    session: AsyncSession, flag: LinkageFlag, *, note: str | None = None
) -> LinkageFlag:
    """Somebody looked. Recorded rather than deleted.

    A dismissed flag is evidence that a person considered the case, which is
    worth as much as the flag was - and deleting it would let the detector
    raise the same question again tomorrow.
    """
    flag.resolved_at = datetime.now(timezone.utc)
    flag.resolution_note = note
    await session.flush()
    return flag
