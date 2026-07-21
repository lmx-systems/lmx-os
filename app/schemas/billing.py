"""Read models for billing statements (roadmap item C3)."""
from __future__ import annotations

from pydantic import BaseModel


class StatementLineView(BaseModel):
    sla_tier: str
    rate_per_drop_cents: int
    order_count: int
    subtotal_cents: int


class StatementView(BaseModel):
    client_id: str
    client_name: str
    year: int
    month: int
    lines: list[StatementLineView]
    total_cents: int
    delivered_order_count: int
    unbilled_order_count: int
