"""HTTP surface for the Order Ingestion Layer."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.store import HoldQueueStore
from app.db import get_db
from app.ingestion.adapters.base import IngestionAdapterError
from app.ingestion.service import ShopNotFoundError, ingest_order
from app.optimizer.event_trigger import dispatch_event_bus

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


def get_hold_queue_store() -> HoldQueueStore:
    return HoldQueueStore()


@router.post("/{hub_id}/{client_id}/{source_system}", status_code=status.HTTP_201_CREATED)
async def ingest_order_endpoint(
    hub_id: str,
    client_id: str,
    source_system: str,
    payload: dict,
    session: AsyncSession = Depends(get_db),
    hold_queue: HoldQueueStore = Depends(get_hold_queue_store),
) -> dict:
    """
    Vendor-agnostic ingestion endpoint. `source_system` selects the adapter
    (epicor | flat_file today; mam | asa land in later phases). This is the
    webhook target you'd register with a client's POS, or the endpoint a
    flat-file import job posts normalized rows to.
    """
    try:
        order = await ingest_order(
            session,
            hold_queue,
            hub_id=hub_id,
            client_id=client_id,
            source_system=source_system,
            payload=payload,
        )
    except IngestionAdapterError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ShopNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # A newly-held order is a "meaningful event" per the design doc - it
    # may have a cluster-mate already waiting, or be releasable immediately
    # if there's nothing to commingle with. See app/optimizer/event_trigger.py.
    await dispatch_event_bus.publish(hub_id, "order_held")

    return {
        "order_id": str(order.id),
        "sla_tier": order.sla_tier,
        "hold_deadline": order.hold_deadline.isoformat() if order.hold_deadline else None,
        "status": order.status,
    }
