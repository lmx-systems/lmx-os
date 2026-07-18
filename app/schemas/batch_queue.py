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
    shop_name: str
    # Computed fresh at request time from the same clustering logic the
    # Dispatch Optimizer uses (app.batch_queue.clustering.cluster_members)
    # - not persisted anywhere, since it can change every time a sibling
    # order is added/removed/released. See app/api/routes.py.
    cluster_mate_ids: list[str]
