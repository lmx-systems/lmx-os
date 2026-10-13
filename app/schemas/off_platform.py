"""Logging an off-platform delivery as a golden record (`DE-7`).

One request shape for the whole short form, sent as often as the driver adds to
it - the pickup half at the counter, the drop-off half at the door - and each
send overwrites only what it carries. See `app/record/off_platform.py`.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.driver_app import ItemFactsBody
from app.schemas.gig import GigJobIntake


class Pin(BaseModel):
    """Where the phone was. Taken by the app, never typed."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class OffPlatformEnd(BaseModel):
    """One end of the job as the driver saw it: where the vehicle stopped, the
    counter or door itself, and when - the phone's own clock, as a geofence
    crossing is, because the outbox may hold it through a dead zone."""

    stop_pin: Pin | None = None
    door_pin: Pin | None = None
    at: datetime | None = None


class OffPlatformRecordBody(BaseModel):
    # The pickup stop's id, chosen by the phone like the record's own (the path),
    # so the counter's survey and photos can be queued before any signal.
    pickup_stop_id: uuid.UUID
    # The job: one already in the store, or the job as typed on the form, found
    # or recorded by its platform reference. One of the two, on the first send.
    gig_job_id: uuid.UUID | None = None
    job: GigJobIntake | None = None
    item: ItemFactsBody | None = None
    pickup: OffPlatformEnd | None = None
    dropoff: OffPlatformEnd | None = None

    @model_validator(mode="after")
    def _names_a_job(self) -> "OffPlatformRecordBody":
        if self.gig_job_id is None and self.job is None:
            raise ValueError("name the job: gig_job_id, or the job itself")
        return self


class OffPlatformRecordView(BaseModel):
    record_id: str
    pickup_stop_id: str
    order_id: str
    gig_job_id: str
    gig_job_status: str
    picked_up_at: datetime | None
    delivered_at: datetime | None
    # The record's grade now (`record_tiers`), and how many of its five
    # elements are still missing.
    tier: str | None
    elements_missing: int | None
    # Owed once the delivery is logged, and not paid until payouts exist.
    record_bonus_cents: int | None
    # Whether the pickup address named a place, so the counter has a handoff
    # point to capture. False: there is nothing to survey there, and the app
    # does not offer it.
    counter_resolved: bool
