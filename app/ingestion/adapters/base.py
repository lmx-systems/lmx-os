"""
POS/DMS adapter interface (component 1: Order Ingestion Layer).

Every adapter's only job is to turn a vendor-specific payload into a
NormalizedOrder. Nothing else in the system should ever branch on
source_system - if a downstream component needs to special-case a POS
vendor, that's a sign the adapter isn't normalizing enough.

Integration priority per the technical design (Section on POS/DMS):
  1. Epicor      (~40% of target shop share, REST/webhook)   - Phase 1
  2. MAM Software (~20%)                                      - Phase 2
  3. ASA Automotive (~15%, SOAP/legacy)                       - Phase 3
  4. Custom/flat-file (~25%)                                  - fallback, available now
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.order import NormalizedOrder


class IngestionAdapterError(Exception):
    """Raised when a vendor payload can't be normalized (malformed/missing fields)."""


class BaseIngestionAdapter(ABC):
    source_system: str

    @abstractmethod
    def normalize(self, hub_id: str, client_id: str, payload: dict) -> NormalizedOrder:
        """Turn one vendor payload into a NormalizedOrder. Must not perform I/O."""
        raise NotImplementedError
