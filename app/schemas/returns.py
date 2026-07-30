"""Schemas for returns & core pickups (docs/ROADMAP.md W1)."""
from pydantic import BaseModel, Field


class ReturnFlagBody(BaseModel):
    # A shop declaring accumulated cores/returns ready for a standalone
    # pickup (docs/ROADMAP.md W1 slice 2).
    manifest: str = Field(min_length=1, max_length=500)


class CollectReturnBody(BaseModel):
    # Optional. Omit to simply confirm collection of a return that was
    # expected on this order; provide a manifest to record an *ad-hoc* core
    # the driver is bringing back that wasn't flagged at ingestion.
    manifest: str | None = Field(default=None, max_length=500)


class ReturnItemView(BaseModel):
    return_id: str
    # Empty for a standalone (shop-flagged) return with no originating order.
    origin_order_ref: str
    shop_name: str | None
    manifest: str
    status: str
    collected_at: str | None
    returned_at: str | None
