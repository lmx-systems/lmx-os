"""
Delivering owed webhooks, and deciding what a failure means
(docs/ROADMAP.md F4).

**Why delivery is separate from the event.** `app/orders/sinks.py` enqueues a
`WebhookDelivery` row inside the transaction that changes the order's status, so
the notification and the fact it describes commit together. This module is the
other half: it takes owed rows and tries to hand them over, with the retry
discipline that makes "we will tell you" true rather than aspirational.

**Two paths, same code, for the same reason dispatch has two.** An immediate
attempt right after commit keeps §1.4's under-30-second write-back target
reachable; a scheduler sweep (`POST /internal/webhooks/deliver-pending`) is the
safety net, because a serverless platform can suspend the process between requests
and an in-flight task is not a guarantee. Neither is allowed to be the only one:
without the immediate attempt the target is capped by the scheduler interval, and
without the sweep an instance dying loses everything it was holding.

**What is retried and what is not** is the substance here, and it is the same
distinction the geocoder and the routing client needed:

  timeout / connection error / 5xx / 429 / 408   ->  retry. Ours or theirs to fix,
                                                    and a second attempt can work.
  any other 4xx                                 ->  REJECTED, not retried. The
                                                    endpoint said it does not want
                                                    this. Retrying a 400 sixteen
                                                    times spends our budget to be
                                                    told the same thing.

Counting a rejection as an outage would also be a misattribution that matters:
`consecutive_failures` disables an endpoint, and a client whose handler returns 422
for an event type they haven't implemented should not lose the ones they have.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models.client_webhook import (
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_REJECTED,
    MAX_CONSECUTIVE_FAILURES,
    ClientWebhookEndpoint,
    WebhookDelivery,
)
from app.webhooks.signing import sign

logger = structlog.get_logger(__name__)

# Short. A consumer that needs longer than this to acknowledge a notification is
# doing work inline that belongs on their own queue, and waiting on them means
# holding a connection while every other owed delivery waits behind it.
REQUEST_TIMEOUT_SECONDS = 10.0

# Roughly: 1m, 5m, 25m, 2h, 10h, 2d - about three days of trying in six attempts.
# Long enough to ride out a weekend deploy of a client's system, short enough that
# a notification which finally lands is still worth having.
_BACKOFF_MINUTES = (1, 5, 25, 120, 600, 2880)
MAX_ATTEMPTS = len(_BACKOFF_MINUTES)

# HTTP statuses worth another go. Everything else in the 4xx range is the consumer
# telling us something a retry won't change.
_RETRYABLE_STATUSES = frozenset({408, 425, 429})


def _is_retryable_status(status_code: int) -> bool:
    return status_code >= 500 or status_code in _RETRYABLE_STATUSES


def _next_attempt_at(attempts: int, now: datetime) -> datetime | None:
    """When to try again after `attempts` failures, or None once exhausted."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return now + timedelta(minutes=_BACKOFF_MINUTES[attempts])


def _new_client() -> httpx.AsyncClient:
    """The HTTP client used for one delivery attempt.

    A named seam rather than calling `httpx.AsyncClient` inline, so tests can point
    deliveries at a fake consumer without patching the httpx module itself - which
    makes any internal `httpx.AsyncClient(...)` call recurse into the patch.

    Redirects are NOT followed, and that is a security control rather than a
    default: a 302 is a way for a vetted public URL to forward us to a private one,
    walking straight around app/webhooks/url_safety.py.
    """
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False)


async def _post(endpoint: ClientWebhookEndpoint, delivery: WebhookDelivery) -> httpx.Response:
    """POST one delivery. `delivery.attempts` is already incremented by the caller,
    so it IS this attempt's number - the header must not add one to it again."""
    # Bytes frozen at enqueue time and signed exactly as sent - re-serialising here
    # would risk a body whose signature doesn't verify because a dict ordered
    # differently.
    body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode()
    timestamp = int(datetime.now(timezone.utc).timestamp())

    async with _new_client() as client:
        return await client.post(
            endpoint.url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-LMX-Signature": sign(endpoint.secret, body, timestamp),
                "X-LMX-Event-Id": delivery.event_id,
                "X-LMX-Delivery-Attempt": str(delivery.attempts),
                "User-Agent": "LMX-OS-Webhooks/1",
            },
        )


async def attempt_delivery(session: AsyncSession, delivery: WebhookDelivery) -> str:
    """Try once, record the outcome, and return the resulting status.

    Never raises. This runs from a background task and from a scheduler sweep, and
    in both an exception escaping would take the rest of the batch with it.
    """
    endpoint = await session.get(ClientWebhookEndpoint, delivery.endpoint_id)
    now = datetime.now(timezone.utc)

    if endpoint is None or not endpoint.is_active:
        # Deactivated between enqueue and delivery - either by the client or by the
        # consecutive-failure cutoff below. Not a failure of this delivery, and
        # retrying it would resurrect traffic to an endpoint we switched off.
        delivery.status = DELIVERY_REJECTED
        delivery.next_attempt_at = None
        delivery.last_error = "endpoint is inactive"
        return delivery.status

    delivery.attempts += 1
    try:
        response = await _post(endpoint, delivery)
        status_code = response.status_code
        delivery.last_status_code = status_code
    except Exception as exc:  # noqa: BLE001 - timeouts, DNS, TLS, resets
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:500]
        return _record_failure(delivery, endpoint, now, retryable=True)

    if 200 <= status_code < 300:
        delivery.status = DELIVERY_DELIVERED
        delivery.delivered_at = now
        delivery.next_attempt_at = None
        delivery.last_error = None
        endpoint.consecutive_failures = 0
        endpoint.last_success_at = now
        logger.info(
            "webhook_delivered",
            delivery_id=str(delivery.id),
            endpoint_id=str(endpoint.id),
            attempts=delivery.attempts,
            status_code=status_code,
        )
        return delivery.status

    delivery.last_error = f"HTTP {status_code}"
    if not _is_retryable_status(status_code):
        # The consumer rejected it. Deliberately does NOT count toward disabling
        # the endpoint - a handler that 422s an event type they haven't implemented
        # should not cost them the ones they have.
        delivery.status = DELIVERY_REJECTED
        delivery.next_attempt_at = None
        logger.warning(
            "webhook_rejected_by_consumer",
            delivery_id=str(delivery.id),
            endpoint_id=str(endpoint.id),
            status_code=status_code,
        )
        return delivery.status

    return _record_failure(delivery, endpoint, now, retryable=True)


def _record_failure(
    delivery: WebhookDelivery,
    endpoint: ClientWebhookEndpoint,
    now: datetime,
    *,
    retryable: bool,
) -> str:
    delivery.next_attempt_at = _next_attempt_at(delivery.attempts, now) if retryable else None
    if delivery.next_attempt_at is None:
        delivery.status = DELIVERY_FAILED
    else:
        delivery.status = DELIVERY_PENDING

    endpoint.consecutive_failures += 1
    if endpoint.consecutive_failures >= MAX_CONSECUTIVE_FAILURES and endpoint.is_active:
        # Switched off, not deleted: the client needs to see in their portal that
        # this happened and why, and re-enabling should be their decision.
        endpoint.is_active = False
        endpoint.disabled_at = now
        logger.warning(
            "webhook_endpoint_disabled",
            endpoint_id=str(endpoint.id),
            client_id=str(endpoint.client_id),
            consecutive_failures=endpoint.consecutive_failures,
            detail="too many consecutive failures - the client must re-enable it",
        )

    logger.info(
        "webhook_delivery_failed",
        delivery_id=str(delivery.id),
        endpoint_id=str(endpoint.id),
        attempts=delivery.attempts,
        status=delivery.status,
        error=delivery.last_error,
        next_attempt_at=(
            delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else None
        ),
    )
    return delivery.status


async def deliver_now(delivery_ids: list[str]) -> None:
    """Best-effort immediate attempt, in its own session.

    Its own session because the caller's has already committed by the time this
    runs - this is scheduled AFTER commit, so that an attempt can never describe a
    transaction that later rolled back.

    Swallows everything. It is a latency optimisation over the sweep, not the
    guarantee; if it fails the row is still `pending` and still due.
    """
    if not delivery_ids:
        return
    try:
        async with session_scope() as session:
            result = await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.id.in_(delivery_ids))
            )
            for delivery in result.scalars().all():
                await attempt_delivery(session, delivery)
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("webhook_immediate_delivery_failed", delivery_ids=delivery_ids)


async def deliver_pending(session: AsyncSession, *, limit: int = 100) -> dict[str, int]:
    """The sweep: every owed delivery that is due. Returns a per-status count.

    Ordered by `sequence` so that, when a consumer has been down and several events
    are owed, they arrive in the order they happened. Retries make arrival order
    unreliable in general - which is why the payload carries `sequence` too - but
    there is no reason to make it worse than it has to be.
    """
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status == DELIVERY_PENDING,
            WebhookDelivery.next_attempt_at.is_not(None),
            WebhookDelivery.next_attempt_at <= now,
        )
        .order_by(WebhookDelivery.sequence)
        .limit(limit)
    )
    deliveries = list(result.scalars().all())

    counts: dict[str, int] = {}
    for delivery in deliveries:
        outcome = await attempt_delivery(session, delivery)
        counts[outcome] = counts.get(outcome, 0) + 1
    await session.commit()

    if len(deliveries) == limit:
        # Said out loud rather than left as a silent truncation: a sweep that
        # always fills its batch means the backlog is growing faster than it
        # drains, and that is worth seeing.
        logger.warning(
            "webhook_sweep_hit_its_limit",
            limit=limit,
            detail="more deliveries were due than this run took - backlog may be growing",
        )
    return counts
