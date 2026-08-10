"""
The status sink that turns a transition into owed webhooks
(docs/ROADMAP.md F4, docs/LMX_LINK_PLAN.md §1.4).

The first sink that reaches outside this system - `app/orders/sinks.py` has only
ever had the logging one, and its docstring called this out as the case it was
designed for.

**What this sink does NOT do is send anything.** It writes a `WebhookDelivery` row
per active endpoint, in the caller's session, and returns. Two reasons, and both
are the point rather than caution:

1. **It commits with the fact it describes.** `emit_status_change` runs inside
   `advance_orders`, *before* the caller commits - so a sink that POSTed inline
   could tell a client an order was delivered on a transaction that then rolled
   back. There is no way to un-send that. A row in the caller's session cannot get
   ahead of reality.

2. **A driver's stop completion must not wait on a client's server.** Sinks are
   called on the hot path a driver is standing at a door waiting for. Inline
   delivery would put a stranger's HTTP stack, and their timeouts, inside that
   request.

Delivery happens in `app/webhooks/delivery.py`, immediately after commit as a
best-effort task and via the scheduler sweep as the guarantee.
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client_webhook import ClientWebhookEndpoint, WebhookDelivery
from app.orders.sinks import BaseStatusSink, StatusEvent

logger = structlog.get_logger(__name__)


def build_payload(event: StatusEvent, *, event_id: str) -> dict:
    """The body a consumer receives.

    Deliberately the public vocabulary and nothing more. `StatusEvent` already
    carries the §1.4 label rather than our enum - `classified` and `queued` are our
    business - and this adds no internal ids beyond the order's own, which the
    client already has.

    `source_order_ref` is here because it is the only identifier a client's own
    system recognises. A webhook that identifies an order solely by our uuid makes
    the consumer do a lookup they may not be able to do.
    """
    return {
        "event_id": event_id,
        "type": "order.status_changed",
        "order_id": event.order_id,
        "source_order_ref": event.source_order_ref,
        "source_system": event.source_system,
        "previous_status": event.previous_status,
        "status": event.status,
        "occurred_at": event.occurred_at.isoformat(),
    }


class WebhookStatusSink(BaseStatusSink):
    sink_name = "client_webhook"

    async def emit(self, event: StatusEvent, session: AsyncSession | None = None) -> None:
        """Enqueue this transition for every active endpoint of this client.

        No session means no enqueue, and that is correct rather than a fallback: the
        whole value of this sink is that the notification commits with the status
        change, so writing it through a session of its own would reintroduce the
        problem it exists to avoid. Logged, because a caller emitting without a
        session is a bug in the caller.
        """
        if event.client_id is None:
            # An order with no client - nobody to notify. Not an error: the
            # returns/resolution paths can produce these.
            return
        if session is None:
            logger.warning(
                "webhook_sink_called_without_a_session",
                order_id=event.order_id,
                detail="cannot enqueue transactionally - event not delivered",
            )
            return

        result = await session.execute(
            select(ClientWebhookEndpoint).where(
                ClientWebhookEndpoint.client_id == uuid.UUID(event.client_id),
                ClientWebhookEndpoint.is_active.is_(True),
            )
        )
        endpoints = list(result.scalars().all())
        if not endpoints:
            return

        # One id per TRANSITION, shared across every endpoint of this client, so a
        # client running two integrations sees the same event_id in both and can
        # correlate them. Uniqueness in the table is (endpoint, event), which is
        # what makes a replayed driver action safe.
        event_id = str(uuid.uuid4())
        payload = build_payload(event, event_id=event_id)

        for endpoint in endpoints:
            delivery = WebhookDelivery(
                endpoint_id=endpoint.id,
                order_id=uuid.UUID(event.order_id),
                event_id=event_id,
                payload=payload,
                # Due immediately. The post-commit task will almost always get
                # there first; this is what makes the sweep pick it up if not.
                next_attempt_at=event.occurred_at,
            )
            session.add(delivery)
            try:
                await session.flush()
            except IntegrityError:
                # The (endpoint, event_id) uniqueness fired - this exact
                # notification is already owed. A replayed transition, not a
                # problem.
                await session.rollback()
                logger.info(
                    "webhook_delivery_already_enqueued",
                    order_id=event.order_id,
                    endpoint_id=str(endpoint.id),
                )
                return
            _pending_delivery_ids(session).append(str(delivery.id))


# Ids enqueued during the current session, so the request that owns the
# transaction can kick off an immediate attempt once it has committed. Held on the
# session rather than in a module global because two requests share this process
# and a global would let one request's commit trigger another's deliveries.
_PENDING_ATTR = "_lmx_pending_webhook_delivery_ids"


def _pending_delivery_ids(session: AsyncSession) -> list[str]:
    ids = session.info.get(_PENDING_ATTR)
    if ids is None:
        ids = []
        session.info[_PENDING_ATTR] = ids
    return ids


def take_pending_delivery_ids(session: AsyncSession) -> list[str]:
    """Ids enqueued in this session, cleared as they're handed over.

    Called after commit. Cleared on read so a second call in the same session
    cannot schedule the same delivery twice - which would be harmless (the attempt
    is idempotent on a delivered row) but would double the traffic.
    """
    ids = _pending_delivery_ids(session)
    taken = list(ids)
    ids.clear()
    return taken
