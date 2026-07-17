"""
Pydantic schemas for orders as they move through the pipeline:
raw POS payload -> NormalizedOrder (ingestion output) -> ClassifiedOrder
(SLA engine output) -> BatchDecision (batch-hold queue output).
"""
from datetime import datetime

from pydantic import BaseModel, Field


class NormalizedOrder(BaseModel):
    """
    Common shape every POS/DMS adapter (Epicor, MAM, ASA, flat-file) must
    produce. This is the contract between app/ingestion/adapters/* and the
    rest of the system - adapters absorb all vendor-specific weirdness so
    nothing downstream needs to know which POS a client uses.
    """

    external_order_ref: str
    source_system: str  # epicor | mam | asa | flat_file
    hub_id: str
    client_id: str
    shop_external_ref: str
    shop_lat: float
    shop_lng: float
    weight_units: float = 1.0
    requested_at: datetime
    raw_payload: dict = Field(default_factory=dict)


class ClassifiedOrder(BaseModel):
    """Output of the Dynamic SLA Engine for a single order."""

    order_id: str
    sla_tier: str  # T1 | T2 | T3
    hold_deadline: datetime
    reason: str
