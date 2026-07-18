from datetime import datetime

from pydantic import BaseModel


class HeldOrderView(BaseModel):
    """Read-only view of a held order, for dashboards - not used internally."""

    order_id: str
    shop_lat: float
    shop_lng: float
    sla_tier: str
    hold_deadline: datetime
    held_since: datetime
