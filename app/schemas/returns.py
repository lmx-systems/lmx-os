"""Schemas for returns & core pickups (docs/ROADMAP.md W1)."""
from pydantic import BaseModel, Field


class CollectReturnBody(BaseModel):
    # Optional. Omit to simply confirm collection of a return that was
    # expected on this order; provide a manifest to record an *ad-hoc* core
    # the driver is bringing back that wasn't flagged at ingestion.
    manifest: str | None = Field(default=None, max_length=500)


class ReturnItemView(BaseModel):
    return_id: str
    origin_order_ref: str
    manifest: str
    status: str
    collected_at: str | None
    returned_at: str | None
