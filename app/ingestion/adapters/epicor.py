"""
Epicor adapter - Phase 1 priority integration (~40% of target shop share).

ASSUMPTION FLAG: Epicor's REST/webhook payload shape varies by client
configuration (the peer review calls this out as "the most common cause of
Phase 1 slippage"). The field names below are a reasonable placeholder
shape for a parts-order webhook, not verified against a specific Epicor
tenant. Before onboarding a real Epicor client, confirm the actual payload
against that client's Epicor config and adjust REQUIRED_FIELDS /
OPTIONAL_DEFAULTS and `normalize` accordingly. Until then a field this
class does not know is accepted and flagged on the order (D-UNK), so the gap
shows up on the first real orders rather than as silently missing data - the goal of isolating this in one small class is
so that reconciliation is a one-file change.
"""
from __future__ import annotations

from datetime import datetime

from app.ingestion.adapters.base import BaseIngestionAdapter, IngestionAdapterError, field_flags
from app.schemas.order import NormalizedOrder

REQUIRED_FIELDS = ("OrderNum", "ShipToNum", "ShipToLat", "ShipToLng", "OrderDate")
# Optional fields this adapter reads, and what an order gets without them.
OPTIONAL_DEFAULTS = {"Weight": 1.0, "PriorityCode": "not rush", "ShipVia": "not will-call"}


class EpicorAdapter(BaseIngestionAdapter):
    source_system = "epicor"

    def normalize(self, hub_id: str, client_id: str, payload: dict) -> NormalizedOrder:
        missing = [f for f in REQUIRED_FIELDS if f not in payload]
        if missing:
            raise IngestionAdapterError(
                f"Epicor payload missing required fields: {missing}"
            )

        try:
            requested_at = datetime.fromisoformat(payload["OrderDate"])
        except ValueError as exc:
            raise IngestionAdapterError(f"Unparseable OrderDate: {payload['OrderDate']!r}") from exc

        raw_flags = {
            "rush": payload.get("PriorityCode") in {"RUSH", "HOT", "1"},
            "will_call": payload.get("ShipVia") == "WILLCALL",
        }

        return NormalizedOrder(
            external_order_ref=str(payload["OrderNum"]),
            source_system=self.source_system,
            hub_id=hub_id,
            client_id=client_id,
            shop_external_ref=str(payload["ShipToNum"]),
            shop_lat=float(payload["ShipToLat"]),
            shop_lng=float(payload["ShipToLng"]),
            weight_units=float(payload.get("Weight", 1.0)),
            requested_at=requested_at,
            raw_payload={**payload, **raw_flags},
            intake_flags=field_flags(payload, known=REQUIRED_FIELDS, defaults=OPTIONAL_DEFAULTS),
        )
