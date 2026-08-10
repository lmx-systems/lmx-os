"""
Status sinks - the write-back half of the contract (docs/LMX_LINK_PLAN.md §1.4).

§1.4 is blunt about why this exists: *"A carrier that takes orders and goes quiet
is not a carrier - it is a favour. This is the half that gets cut under pressure
and must not be."*

The mirror of `app/ingestion/adapters/`. Sources normalize inbound into one order
object; sinks map one status machine outbound into whatever vocabulary each
consumer speaks. Neither side is allowed to leak into the core.

Two sinks exist: the logging one, which is always correct, and the outbound client
webhook (`F4`, `app/webhooks/sink.py`) - the first that reaches outside this
system. The client portal still reads status straight from Postgres, which remains
right while it is a surface of this application rather than a consumer of it.

Emission is deliberately best-effort and never fails the caller: a driver
completing a stop must not see an error because a downstream consumer is down.
That is the same reasoning behind the fire-and-forget shop SMS in
`app/messaging/shop_notifications.py`.

**WHY `emit` TAKES A SESSION.** `emit_status_change` is called from inside
`advance_orders`, *before* the caller commits. A sink that acted on the event
immediately - POSTing it, say - could therefore tell a consumer an order was
delivered on a transaction that then rolled back, and there is no way to un-send
that. Handing sinks the caller's session lets one that cares record its intent in
the same transaction as the fact it describes, so the notification cannot get ahead
of reality. Sinks that don't care (the logging one) ignore it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import structlog

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import OrderStatus
from app.orders.state_machine import public_label

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StatusEvent:
    """One order status transition, in the public vocabulary.

    Carries the §1.4 label rather than the internal enum value, because that is
    the contract a consumer is entitled to - `classified` and `queued` are our
    business, not theirs.
    """

    order_id: str
    client_id: str | None
    source_system: str
    source_order_ref: str | None
    previous_status: str
    status: str
    occurred_at: datetime


class BaseStatusSink(ABC):
    """One outbound consumer of order status."""

    sink_name: str

    @abstractmethod
    async def emit(self, event: StatusEvent, session: AsyncSession | None = None) -> None:
        """Deliver one transition, or durably record that it is owed.

        `session` is the caller's, mid-transaction and NOT yet committed - see the
        module docstring. A sink that writes through it commits atomically with the
        status change. A sink that acts on the event immediately must not, because
        the transition may still roll back.

        Must not raise. A sink that cannot deliver should log and return - the
        driver action that produced this event has already happened, and failing
        it retroactively because a consumer is unreachable would be worse than
        the missed notification.
        """
        raise NotImplementedError


class LoggingStatusSink(BaseStatusSink):
    """Writes transitions to the structured log.

    Not a placeholder for a real sink so much as the one that is always correct:
    an order's status history should be reconstructable from logs regardless of
    what else is configured, and this is what makes §1.4's under-30-second
    write-back target measurable before any external consumer exists.
    """

    sink_name = "logging"

    async def emit(self, event: StatusEvent, session: AsyncSession | None = None) -> None:
        logger.info(
            "order_status_changed",
            order_id=event.order_id,
            client_id=event.client_id,
            source_system=event.source_system,
            source_order_ref=event.source_order_ref,
            previous_status=event.previous_status,
            status=event.status,
            occurred_at=event.occurred_at.isoformat(),
        )


def _build_sinks() -> list[BaseStatusSink]:
    # Imported here rather than at module scope: app/webhooks/sink.py imports
    # StatusEvent from this module, so a top-level import would be circular.
    from app.webhooks.sink import WebhookStatusSink

    return [LoggingStatusSink(), WebhookStatusSink()]


_SINKS: list[BaseStatusSink] | None = None


def registered_sinks() -> list[BaseStatusSink]:
    global _SINKS
    if _SINKS is None:
        _SINKS = _build_sinks()
    return list(_SINKS)


async def emit_status_change(
    *,
    session: AsyncSession | None = None,
    order_id: str,
    client_id: str | None,
    source_system: str,
    source_order_ref: str | None,
    previous: OrderStatus,
    current: OrderStatus,
    occurred_at: datetime,
) -> None:
    """Fan one transition out to every registered sink.

    Each sink is isolated: one raising must not stop the others, and none of them
    can fail the caller. `emit` is documented as never raising, but a sink is
    ordinary code and this is the wrong place to find out otherwise.
    """
    event = StatusEvent(
        order_id=order_id,
        client_id=client_id,
        source_system=source_system,
        source_order_ref=source_order_ref,
        previous_status=public_label(previous),
        status=public_label(current),
        occurred_at=occurred_at,
    )
    for sink in registered_sinks():
        try:
            await sink.emit(event, session)
        except Exception:  # noqa: BLE001 - a sink must never break a driver action
            logger.warning("status_sink_failed", sink=sink.sink_name, order_id=order_id)
