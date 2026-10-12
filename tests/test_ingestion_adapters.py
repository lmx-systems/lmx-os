import pytest

from app.ingestion.adapters.base import IngestionAdapterError
from app.ingestion.adapters.epicor import EpicorAdapter
from app.ingestion.adapters.generic import GenericFlatFileAdapter
from app.ingestion.registry import get_adapter


def test_epicor_adapter_normalizes_valid_payload():
    adapter = EpicorAdapter()
    payload = {
        "OrderNum": "E-100",
        "ShipToNum": "SHOP-9",
        "ShipToLat": 34.05,
        "ShipToLng": -118.25,
        "OrderDate": "2026-07-17T12:00:00+00:00",
        "PriorityCode": "RUSH",
        "Weight": 3.5,
    }
    normalized = adapter.normalize("hub-1", "client-1", payload)
    assert normalized.external_order_ref == "E-100"
    assert normalized.source_system == "epicor"
    assert normalized.weight_units == 3.5
    assert normalized.raw_payload["rush"] is True


def test_epicor_adapter_raises_on_missing_fields():
    adapter = EpicorAdapter()
    with pytest.raises(IngestionAdapterError):
        adapter.normalize("hub-1", "client-1", {"OrderNum": "E-100"})


def test_epicor_adapter_raises_on_bad_date():
    adapter = EpicorAdapter()
    payload = {
        "OrderNum": "E-100",
        "ShipToNum": "SHOP-9",
        "ShipToLat": 34.05,
        "ShipToLng": -118.25,
        "OrderDate": "not-a-date",
    }
    with pytest.raises(IngestionAdapterError):
        adapter.normalize("hub-1", "client-1", payload)


def test_generic_adapter_normalizes_valid_payload():
    adapter = GenericFlatFileAdapter()
    payload = {
        "order_ref": "F-1",
        "shop_ref": "SHOP-1",
        "shop_lat": 34.0,
        "shop_lng": -118.0,
        "requested_at": "2026-07-17T12:00:00+00:00",
    }
    normalized = adapter.normalize("hub-1", "client-1", payload)
    assert normalized.external_order_ref == "F-1"
    assert normalized.source_system == "flat_file"


def test_registry_returns_correct_adapter():
    assert isinstance(get_adapter("epicor"), EpicorAdapter)
    assert isinstance(get_adapter("flat_file"), GenericFlatFileAdapter)


def test_registry_raises_for_unknown_source():
    with pytest.raises(ValueError):
        get_adapter("some_unsupported_pos")


# --- D-UNK: an unknown field is accepted and flagged, never rejected ---------

_EPICOR = {
    "OrderNum": "E-200",
    "ShipToNum": "SHOP-9",
    "ShipToLat": 34.05,
    "ShipToLng": -118.25,
    "OrderDate": "2026-07-17T12:00:00+00:00",
}


def test_an_unknown_epicor_field_is_accepted_and_flagged():
    normalized = EpicorAdapter().normalize(
        "hub-1", "client-1", {**_EPICOR, "Weight": 2.0, "PriorityCode": "STD", "ShipVia": "VAN", "CustPONum": "PO-7"}
    )

    assert normalized.external_order_ref == "E-200"
    assert normalized.intake_flags == [{"kind": "unknown_fields", "fields": ["CustPONum"]}]
    # Kept where an ops urgency rule can still match it.
    assert normalized.raw_payload["CustPONum"] == "PO-7"


def test_an_absent_optional_field_is_named_with_the_default_it_got():
    normalized = EpicorAdapter().normalize("hub-1", "client-1", _EPICOR)

    assert normalized.weight_units == 1.0
    assert normalized.intake_flags == [
        {"kind": "defaulted_fields", "fields": {"Weight": 1.0, "PriorityCode": "not rush", "ShipVia": "not will-call"}}
    ]


def test_keys_read_downstream_are_not_unknown():
    # The SLA engine reads urgency flags straight from the payload, and intake
    # reads parcels and returns; a source sending them is understood.
    payload = {
        "order_ref": "F-1", "shop_ref": "S-1", "shop_lat": 1.0, "shop_lng": 2.0,
        "requested_at": "2026-07-17T12:00:00+00:00", "weight_units": 4.0,
        "rush": "Y", "will_call": "N", "parcels": ["B1"], "core_return": True,
    }

    assert GenericFlatFileAdapter().normalize("hub-1", "client-1", payload).intake_flags == []
