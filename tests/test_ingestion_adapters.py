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
