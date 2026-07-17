"""
Generic / flat-file adapter - fallback for the ~25% of shops without a
supported POS integration and for MAM/ASA before those adapters ship.
Expects a payload already shaped close to NormalizedOrder (e.g. produced by
a nightly CSV/flat-file import job), so this adapter is mostly validation.
"""
from __future__ import annotations

from datetime import datetime

from app.ingestion.adapters.base import BaseIngestionAdapter, IngestionAdapterError
from app.schemas.order import NormalizedOrder

REQUIRED_FIELDS = ("order_ref", "shop_ref", "shop_lat", "shop_lng", "requested_at")


class GenericFlatFileAdapter(BaseIngestionAdapter):
    source_system = "flat_file"

    def normalize(self, hub_id: str, client_id: str, payload: dict) -> NormalizedOrder:
        missing = [f for f in REQUIRED_FIELDS if f not in payload]
        if missing:
            raise IngestionAdapterError(f"Flat-file payload missing required fields: {missing}")

        try:
            requested_at = datetime.fromisoformat(payload["requested_at"])
        except ValueError as exc:
            raise IngestionAdapterError(
                f"Unparseable requested_at: {payload['requested_at']!r}"
            ) from exc

        return NormalizedOrder(
            external_order_ref=str(payload["order_ref"]),
            source_system=self.source_system,
            hub_id=hub_id,
            client_id=client_id,
            shop_external_ref=str(payload["shop_ref"]),
            shop_lat=float(payload["shop_lat"]),
            shop_lng=float(payload["shop_lng"]),
            weight_units=float(payload.get("weight_units", 1.0)),
            requested_at=requested_at,
            raw_payload=payload,
        )
