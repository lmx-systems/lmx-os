"""Deleting what we said we would delete.

The privacy policy in `content/privacy.md` states retention periods as facts. Before
this module nothing deleted anything, which would have made every one of those sentences
the same class of defect as a proof-of-delivery requirement nobody checks or a geocoder
that caches its own failures: a promise about a thing that never happens.

**The policy is the specification for this file, not the other way round.** Each sweep's
period comes from a setting, and each setting's value is a number printed in the policy.
Changing one without the other makes the document lie.

Three sweeps, and what is deliberately absent:

  - **Driver location trails**, ninety days. The only personal record that grows without
    bound - at a 30-second ping interval an on-duty driver writes about 120 rows an hour,
    forever, describing a person's movements.
  - **Messages and calls**, two years. What we texted, to which number, and that a call
    happened between two numbers. Never call content, which we do not record.
  - **Declined applications**, twelve months. A company that applied and was turned down
    has no ongoing relationship with us, so there is nothing to keep the record for past
    the point of recognising a second application.

Not here, on purpose. **Proof-of-delivery images and driver licence/insurance scans** are
in object storage, where the right mechanism is a bucket lifecycle rule rather than an
application loop that lists and deletes objects one at a time - `docs/LEGAL_BRIEF.md`
tracks that as outstanding, and notes the constraint that matters: proof retention must
outlast the claim window, or we delete the photograph before a client can still claim on
it. **Delivery and billing records** are kept for seven years as business records, and
nothing should be quietly deleting those. **Tracking links** already expire via
`settings.tracking_link_grace_hours`.

Every sweep is safe to over-call and safe to miss for a day: these are retention periods,
not deletion deadlines measured in hours.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.call import Call
from app.models.client import Client
from app.models.client_api_key import ClientApiKey
from app.models.client_rate import ClientRate
from app.models.client_sla_term import ClientSlaTerm
from app.models.client_user import ClientUser
from app.models.client_webhook import ClientWebhookEndpoint, WebhookDelivery
from app.models.driver_location_ping import DriverLocationPing
from app.models.invoice import Invoice
from app.models.message import Message
from app.models.order import Order
from app.models.shop import Shop

logger = structlog.get_logger(__name__)


def _cutoff(days: int, now: datetime | None = None) -> datetime:
    """The oldest record a period allows us to keep.

    UTC throughout. Building a cutoff from a local date against a timezone-aware column
    is a bug that only appears in the evening - it has already happened once in this
    codebase, in the billing tests, where five tests passed all afternoon and failed
    after 5pm Pacific.
    """
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)


async def _delete_older_than(session: AsyncSession, model, column, cutoff: datetime) -> int:
    """Count first, then delete, in one transaction.

    The count is what gets reported. "The sweep ran" and "the sweep deleted the rows it
    should have" are different claims, and a scheduler that only ever sees a 200 cannot
    tell them apart.
    """
    doomed = (
        await session.execute(select(func.count()).select_from(model).where(column < cutoff))
    ).scalar_one()
    if doomed:
        await session.execute(delete(model).where(column < cutoff))
    return int(doomed)


# ---------------------------------------------------------------------------
# 1. Driver location trails
# ---------------------------------------------------------------------------


def location_ping_cutoff(now: datetime | None = None) -> datetime:
    return _cutoff(settings.location_ping_retention_days, now)


async def prune_location_pings(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """Delete location pings past the retention period."""
    cutoff = location_ping_cutoff(now)
    deleted = await _delete_older_than(
        session, DriverLocationPing, DriverLocationPing.recorded_at, cutoff
    )
    if deleted:
        await session.commit()
    result = {
        "deleted": deleted,
        "retention_days": settings.location_ping_retention_days,
        "cutoff": cutoff.isoformat(),
    }
    logger.info("location_ping_prune_complete", **result)
    return result


# ---------------------------------------------------------------------------
# 2. Messages and calls
# ---------------------------------------------------------------------------


async def prune_communications(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """Delete SMS and call records past the retention period.

    Both in one sweep because the policy states one period for both and they are the same
    kind of record: that we contacted a number, not what was said. Call *content* is never
    recorded, so there is nothing else here to delete.

    Keyed on `created_at` rather than a send timestamp, because a message that failed to
    send has no send timestamp and would otherwise be kept forever - exactly the rows
    least worth keeping.
    """
    cutoff = _cutoff(settings.communication_retention_days, now)
    messages = await _delete_older_than(session, Message, Message.created_at, cutoff)
    calls = await _delete_older_than(session, Call, Call.created_at, cutoff)
    if messages or calls:
        await session.commit()
    result = {
        "messages_deleted": messages,
        "calls_deleted": calls,
        "retention_days": settings.communication_retention_days,
        "cutoff": cutoff.isoformat(),
    }
    logger.info("communication_prune_complete", **result)
    return result


# ---------------------------------------------------------------------------
# 3. Declined applications
# ---------------------------------------------------------------------------

# Everything owned by a client that a rejected applicant could legitimately have. A
# rejected applicant has exactly one of these - the inactive user created at signup - but
# the list is complete because a partial delete would fail on a foreign key and roll back
# the whole sweep.
#
# Rows are removed explicitly rather than by database cascade, and none of these foreign
# keys declares one. That is deliberate: without a cascade, Postgres refuses to delete a
# client that still has an order or an invoice, which is a last line of defence against
# this sweep destroying a business record.
_CLIENT_OWNED = (
    (ClientUser, ClientUser.client_id),
    (ClientRate, ClientRate.client_id),
    (ClientApiKey, ClientApiKey.client_id),
    (ClientWebhookEndpoint, ClientWebhookEndpoint.client_id),
    (ClientSlaTerm, ClientSlaTerm.client_id),
)

# If a rejected applicant has any of these, something is wrong upstream and the record is
# left alone. A rejected client cannot order - `POST /client/orders` is gated on
# `signup_status == 'active'` - so an order here means either a bug or a status that was
# changed by hand, and in both cases deleting is the wrong response to a surprise.
_MUST_BE_ABSENT = (
    ("orders", Order, Order.client_id),
    ("invoices", Invoice, Invoice.client_id),
    ("shops", Shop, Shop.client_id),
)


async def prune_declined_applications(
    session: AsyncSession, *, now: datetime | None = None
) -> dict:
    """Delete applications we declined more than the retention period ago.

    A row with no `rejected_at` is never deleted. Those are rejections recorded before
    migration 0041 added the column, and the sweep will not invent a date - it reports the
    count as `skipped_undated` instead, so a stuck row is visible rather than silent.
    """
    cutoff = _cutoff(settings.declined_application_retention_days, now)

    candidates = (
        (
            await session.execute(
                select(Client).where(
                    Client.signup_status == "rejected",
                    Client.rejected_at.is_not(None),
                    Client.rejected_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )

    undated = (
        await session.execute(
            select(func.count())
            .select_from(Client)
            .where(Client.signup_status == "rejected", Client.rejected_at.is_(None))
        )
    ).scalar_one()

    deleted = 0
    skipped_with_records: list[str] = []
    for client in candidates:
        blockers: list[str] = []
        for label, model, column in _MUST_BE_ABSENT:
            count = (
                await session.execute(
                    select(func.count()).select_from(model).where(column == client.id)
                )
            ).scalar_one()
            if count:
                blockers.append(f"{count} {label}")
        if blockers:
            # Loud, with the reason. A rejected applicant holding orders is a fact worth
            # somebody's attention, and it is not this sweep's job to resolve it.
            logger.warning(
                "declined_application_retained",
                client_id=str(client.id),
                reason="has records that must not be deleted",
                blockers=blockers,
            )
            skipped_with_records.append(str(client.id))
            continue

        # Webhook deliveries hang off the endpoint rather than the client, so they have
        # to go before it. In practice there are none - a delivery references an order,
        # and an applicant with orders was already skipped above.
        await session.execute(
            delete(WebhookDelivery).where(
                WebhookDelivery.endpoint_id.in_(
                    select(ClientWebhookEndpoint.id).where(
                        ClientWebhookEndpoint.client_id == client.id
                    )
                )
            )
        )
        for model, column in _CLIENT_OWNED:
            await session.execute(delete(model).where(column == client.id))
        await session.delete(client)
        deleted += 1

    if deleted:
        await session.commit()

    result = {
        "deleted": deleted,
        "skipped_undated": int(undated),
        "skipped_with_records": len(skipped_with_records),
        "retention_days": settings.declined_application_retention_days,
        "cutoff": cutoff.isoformat(),
    }
    logger.info("declined_application_prune_complete", **result)
    return result


# ---------------------------------------------------------------------------
# The scheduled entry point
# ---------------------------------------------------------------------------


async def prune_all(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """Every sweep, reported per category.

    One endpoint and one schedule rather than three, because they share a cadence and a
    reason to exist. Reported separately because "deleted 4000 rows" tells an operator
    nothing about whether the sweep that matters actually ran.

    Sequential rather than concurrent: they commit to the same session, and the whole
    thing takes one indexed query per category when there is nothing due.
    """
    return {
        "location_pings": await prune_location_pings(session, now=now),
        "communications": await prune_communications(session, now=now),
        "declined_applications": await prune_declined_applications(session, now=now),
    }
