"""The driver's off-platform record (`DE-7`): a gig job logged as a golden record.

Its own module rather than another block in `driver_routes.py`: the record is a
measurement beside the driver's work, not a step in an LMX delivery. The door's
survey and photos go through the stop endpoints every drop-off uses
(`/driver/stops/{record_id}/dock-survey`, `/handoff-photo`), because the record
*is* a drop-off stop. See `app/record/off_platform.py`.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.driver_auth.dependencies import AuthedDriver, get_current_driver
from app.gig_platform.service import InvalidGigJobTransition
from app.models.hub import Hub
from app.record.off_platform import (
    OffPlatformRecord,
    RecordConflict,
    RecordInvalid,
    RecordNotFound,
    find_off_platform_record,
    log_off_platform_record,
    record_grade,
)
from app.schemas.off_platform import OffPlatformRecordBody, OffPlatformRecordView

router = APIRouter(prefix="/driver", tags=["driver"])


async def _view(session: AsyncSession, record: OffPlatformRecord) -> OffPlatformRecordView:
    tier, missing = await record_grade(session, record.dropoff.id)
    return OffPlatformRecordView(
        record_id=str(record.dropoff.id),
        pickup_stop_id=str(record.pickup.id),
        order_id=str(record.order.id),
        gig_job_id=str(record.job.id),
        gig_job_status=record.job.status,
        picked_up_at=record.pickup.completed_at,
        delivered_at=record.dropoff.completed_at,
        tier=tier,
        elements_missing=missing,
        record_bonus_cents=record.job.record_bonus_cents,
        counter_resolved=record.pickup.handoff_point_id is not None,
    )


@router.put("/me/off-platform-records/{record_id}", response_model=OffPlatformRecordView)
async def log_off_platform_delivery(
    record_id: uuid.UUID,
    body: OffPlatformRecordBody,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> OffPlatformRecordView:
    """Log a delivery from a gig platform as a golden record, or add to it.

    A PUT because the phone names the record: `record_id` becomes the drop-off
    stop's id, so the door's survey and photos can wait behind this in the
    outbox before the server has ever seen it. Sent once at the counter and
    again at the door, each time with only what it carries, and a retry writes
    the same again.

    404 for a job that is not this driver's; 409 for a job declined or
    cancelled, or already logged as another record; 422 for a drop-off address
    that names no place, or a time ahead of our clock.
    """
    home = await session.get(Hub, uuid.UUID(driver.hub_id))
    if home is None:
        raise HTTPException(status_code=404, detail="Hub not found")
    try:
        record = await log_off_platform_record(
            session, driver_id=uuid.UUID(driver.driver_id), home=home, record_id=record_id, body=body
        )
    except RecordNotFound as missing:
        raise HTTPException(status_code=404, detail=str(missing)) from missing
    except (RecordConflict, InvalidGigJobTransition) as conflict:
        raise HTTPException(status_code=409, detail=str(conflict)) from conflict
    except RecordInvalid as invalid:
        raise HTTPException(status_code=422, detail=str(invalid)) from invalid
    await session.commit()
    return await _view(session, record)


@router.get("/me/off-platform-records/{record_id}", response_model=OffPlatformRecordView)
async def get_off_platform_record(
    record_id: uuid.UUID,
    driver: AuthedDriver = Depends(get_current_driver),
    session: AsyncSession = Depends(get_db),
) -> OffPlatformRecordView:
    """The record as the server has it - for the app to know, once the counter
    half has landed, whether there is a counter to capture. 404 until the first
    send arrives, and for anyone else's record."""
    try:
        record = await find_off_platform_record(session, driver_id=uuid.UUID(driver.driver_id), record_id=record_id)
    except RecordNotFound as missing:
        raise HTTPException(status_code=404, detail=str(missing)) from missing
    return await _view(session, record)
