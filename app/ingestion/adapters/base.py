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
from collections.abc import Mapping

from app.schemas.order import NormalizedOrder
from app.sla.engine import HOT_SHOT_FLAG_KEYS, RUSH_FLAG_KEYS, SCHEDULED_FLAG_KEYS

# Payload keys something downstream reads by name, whatever the source: the SLA
# engine's urgency flags, and intake's parcels and returns
# (app/ingestion/service.py). An adapter knows these without mapping them.
READ_DOWNSTREAM = frozenset(
    HOT_SHOT_FLAG_KEYS + RUSH_FLAG_KEYS + SCHEDULED_FLAG_KEYS
    + ("parcels", "parcel_count", "return_manifest", "core_return")
)

UNKNOWN_FIELDS = "unknown_fields"
DEFAULTED_FIELDS = "defaulted_fields"


def field_flags(payload: dict, *, known: tuple[str, ...], defaults: Mapping[str, object]) -> list[dict]:
    """What an adapter did not understand, and what it had to assume (D-UNK).

    An unknown field is accepted, never rejected: the order goes through with the
    field kept in `raw_payload`, where an ops urgency rule can still match it, and
    this says the field map has a gap. An optional field that was absent is named
    with the default it got - a `Weight` of 1.0 the source never sent reads like a
    weight somebody measured.
    """
    flags: list[dict] = []
    unknown = sorted(set(payload) - set(known) - set(defaults) - READ_DOWNSTREAM)
    if unknown:
        flags.append({"kind": UNKNOWN_FIELDS, "fields": unknown})
    defaulted = {name: value for name, value in defaults.items() if name not in payload}
    if defaulted:
        flags.append({"kind": DEFAULTED_FIELDS, "fields": defaulted})
    return flags


class IngestionAdapterError(Exception):
    """Raised when a vendor payload can't be normalized (malformed/missing fields)."""


class BaseIngestionAdapter(ABC):
    source_system: str

    @abstractmethod
    def normalize(self, hub_id: str, client_id: str, payload: dict) -> NormalizedOrder:
        """Turn one vendor payload into a NormalizedOrder. Must not perform I/O."""
        raise NotImplementedError
