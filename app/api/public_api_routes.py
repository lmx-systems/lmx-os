"""
The public order API (docs/ORDER_API.md, docs/ROADMAP.md LMX Link T5).

**The half of T5 that was missing.** Its exit criterion is *"an external system POSTs
an order and receives status callbacks without LMX assistance"*. F4 shipped the
callbacks; this is the POST. Until now the only way for a client's system to submit
an order was `/ingestion/{hub}/{client}/{source}`, which sits behind the ops-user
middleware - so wiring a POS to it meant handing that POS an LMX ops login.

**Why this is a new prefix rather than opening up the existing endpoint.** That one
takes `hub_id` and `client_id` in the path. Exempting it from ops auth would have
made those attacker-supplied, so a client's key could submit orders billed to and
delivered for someone else. Here there is deliberately no way to name a client at
all: it comes from the API key, and the hub comes from the client
(`app/client_api/dependencies.py`).

`/api/v1` is versioned because this is the first contract in this system that
someone outside it writes code against, and the one thing we cannot do later is
change it quietly.

**Native-payload adapters stay ops-only, on purpose.** An Epicor or MAM connector is
something LMX configures against a specific tenant's field names, with the adapter
chosen by us - it is not self-serve, and `source_system` selecting an adapter is a
lever an external caller has no reason to hold. This endpoint takes the documented
LMX Order Object instead, which is the shape we are prepared to support
indefinitely.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.store import HoldQueueStore
from app.client_api.dependencies import AuthedApiClient, get_api_client
from app.db import get_db
from app.geocoding import get_geocoder
from app.ingestion.service import (
    OriginUnresolvableError,
    ShopNotFoundError,
    ingest_lmx_order,
)
from app.models.order import Order
from app.optimizer.event_trigger import dispatch_event_bus
from app.schemas.lmx_order import LMXOrder
from app.schemas.public_api import ApiOrderBody, ApiOrderResult

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["public-api"])


@router.post("/orders", response_model=ApiOrderResult, status_code=201)
async def submit_order(
    body: ApiOrderBody,
    api_client: AuthedApiClient = Depends(get_api_client),
    session: AsyncSession = Depends(get_db),
) -> ApiOrderResult:
    """Submit one delivery.

    **Idempotent on `your_order_ref`, and that is not a nicety.** An external caller
    whose POST times out has no way to know whether we got it, so it will retry - and
    a duplicate here is a second van to a real address, billed twice. Resubmitting the
    same reference returns the order we already have, with `duplicate: true`, instead
    of creating another. Without this the caller's only safe options are to never
    retry (and silently lose orders) or to reconcile by hand.

    The uniqueness is enforced by `uq_orders_source_ref` in Postgres, so it holds even
    for two retries racing each other - the check below is the fast path, the
    constraint is the guarantee.

    422 when the pickup address can't be resolved, and that is deliberately a refusal
    rather than a best guess: `app/geocoding/base.py` explains why a wrong coordinate
    is worse than none - it sends a real van to the wrong place.
    """
    existing = await _existing_order(session, api_client, body.your_order_ref)
    if existing is not None:
        logger.info(
            "api_order_duplicate_ignored",
            client_id=api_client.client_id,
            your_order_ref=body.your_order_ref,
            order_id=str(existing.id),
        )
        return _result(existing, duplicate=True)

    lmx = LMXOrder(
        # Not taken from the request. See the module docstring.
        hub_id=api_client.hub_id,
        client_id=api_client.client_id,
        source_system=_SOURCE_SYSTEM,
        source_order_ref=body.your_order_ref,
        pickup_address=body.pickup_address,
        pickup_contact_name=body.pickup_contact_name,
        pickup_contact_phone=body.pickup_contact_phone,
        drop_address_raw=body.delivery_address,
        drop_contact_name=body.delivery_contact_name,
        drop_contact_phone=body.delivery_contact_phone,
        access_notes=body.delivery_notes,
        ready_at=body.ready_at,
        delivery_window_end=body.deliver_by,
    )

    try:
        order = await ingest_lmx_order(
            session, HoldQueueStore(), lmx, geocoder=get_geocoder()
        )
    except OriginUnresolvableError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"We couldn't find that pickup address: {exc}. "
                "Check it and resubmit - we don't guess at coordinates."
            ),
        ) from exc
    except ShopNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Same trigger the portal's order submission uses, so an API order is dispatched
    # on exactly the same path rather than waiting for the next scheduled cycle.
    await dispatch_event_bus.publish(api_client.hub_id, "order_ingested")

    logger.info(
        "api_order_accepted",
        client_id=api_client.client_id,
        api_key_id=api_client.api_key_id,
        order_id=str(order.id),
        your_order_ref=body.your_order_ref,
    )
    return _result(order, duplicate=False)


@router.get("/orders/{your_order_ref}", response_model=ApiOrderResult)
async def get_order(
    your_order_ref: str,
    api_client: AuthedApiClient = Depends(get_api_client),
    session: AsyncSession = Depends(get_db),
) -> ApiOrderResult:
    """Look an order up by the caller's OWN reference.

    By their reference rather than ours, because that is the id their system holds -
    an API that can only be queried by an identifier we invented forces them to store
    a mapping they shouldn't need. It is also the reconciliation path for the window
    when a webhook endpoint was paused, since nothing is enqueued for an inactive
    endpoint (docs/WEBHOOKS.md).
    """
    order = await _existing_order(session, api_client, your_order_ref)
    if order is None:
        raise HTTPException(status_code=404, detail="No order with that reference")
    return _result(order, duplicate=False)


# Every order through this endpoint is tagged with one source system, so API traffic
# is distinguishable from portal and Epicor traffic in `orders.source_system` - which
# is what keeps I1/I4's analytics from treating three different intake paths as one.
_SOURCE_SYSTEM = "client_api"


async def _existing_order(
    session: AsyncSession, api_client: AuthedApiClient, your_order_ref: str
) -> Order | None:
    """This client's order with that reference, if we already have it.

    Scoped by client_id as well as by reference. Two clients can legitimately use the
    same internal numbering, and without the client scope one would be able to read -
    and idempotently "recover" - the other's order by guessing a reference.
    """
    result = await session.execute(
        select(Order).where(
            Order.client_id == uuid.UUID(api_client.client_id),
            Order.source_system == _SOURCE_SYSTEM,
            Order.source_order_ref == your_order_ref,
        )
    )
    return result.scalar_one_or_none()


def _result(order: Order, *, duplicate: bool) -> ApiOrderResult:
    return ApiOrderResult(
        order_id=str(order.id),
        your_order_ref=order.source_order_ref or "",
        status=order.status.value,
        sla_tier=order.sla_tier,
        collect_by=order.hold_deadline,
        promised_at=order.promised_at,
        duplicate=duplicate,
    )
