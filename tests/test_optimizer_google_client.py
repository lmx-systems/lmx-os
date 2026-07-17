from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.optimizer.google_routes_client import (
    GoogleRouteOptimizationClient,
    get_route_optimization_client,
)
from app.schemas.optimizer import DriverCandidate, StopCandidate


def _fake_credentials(valid: bool = True) -> MagicMock:
    creds = MagicMock()
    creds.valid = valid
    creds.token = "fake-token"
    return creds


@pytest.fixture
def client() -> GoogleRouteOptimizationClient:
    with patch("google.auth.default", return_value=(_fake_credentials(), "lmx-os")):
        return GoogleRouteOptimizationClient(project_id="lmx-os")


def test_build_request_maps_stops_and_drivers(client):
    drivers = [DriverCandidate(driver_id="d1", lat=34.05, lng=-118.25, capacity_remaining_units=5)]
    stops = [
        StopCandidate(stop_id="s1", order_ids=["o1"], lat=34.06, lng=-118.24, weight_units=2, sla_tier="T1"),
    ]
    body = client._build_request(drivers, stops)

    shipment = body["model"]["shipments"][0]
    assert shipment["label"] == "s1"
    assert shipment["deliveries"] == [{"arrivalLocation": {"latitude": 34.06, "longitude": -118.24}}]
    assert shipment["loadDemands"] == {"weight": {"amount": "2"}}
    assert shipment["penaltyCost"] > 0

    vehicle = body["model"]["vehicles"][0]
    assert vehicle["label"] == "d1"
    assert vehicle["startLocation"] == {"latitude": 34.05, "longitude": -118.25}
    assert "endLocation" not in vehicle
    assert vehicle["loadLimits"] == {"weight": {"maxLoad": "5"}}

    assert body["timeout"] == "3s"
    assert "globalStartTime" in body["model"]
    assert "globalEndTime" in body["model"]


def test_build_request_penalizes_t1_skips_more_than_t3(client):
    stops = [
        StopCandidate(stop_id="s_t1", order_ids=["o1"], lat=0, lng=0, weight_units=1, sla_tier="T1"),
        StopCandidate(stop_id="s_t3", order_ids=["o2"], lat=0, lng=0, weight_units=1, sla_tier="T3"),
    ]
    body = client._build_request([], stops)
    penalties = {s["label"]: s["penaltyCost"] for s in body["model"]["shipments"]}
    assert penalties["s_t1"] > penalties["s_t3"]


def test_parse_response_maps_routes_and_skipped_shipments(client):
    payload = {
        "routes": [
            {
                "vehicleLabel": "d1",
                "visits": [
                    {"shipmentLabel": "s1", "shipmentIndex": 0},
                    {"shipmentLabel": "s2", "shipmentIndex": 1},
                ],
            },
            {"vehicleLabel": "d2", "visits": []},
        ],
        "skippedShipments": [{"index": 2, "label": "s3"}],
    }
    assignments, unassigned = client._parse_response(payload)

    assert len(assignments) == 1
    assert assignments[0].driver_id == "d1"
    assert assignments[0].stop_ids == ["s1", "s2"]
    assert unassigned == ["s3"]


@pytest.mark.asyncio
async def test_optimize_sends_bearer_token_and_parses_response(client):
    response_payload = {
        "routes": [{"vehicleLabel": "d1", "visits": [{"shipmentLabel": "s1"}]}],
        "skippedShipments": [],
    }

    async def fake_post(url, json, headers):
        assert headers["Authorization"] == "Bearer fake-token"
        assert "optimizeTours" in url
        return httpx.Response(200, json=response_payload, request=httpx.Request("POST", url))

    client._http.post = fake_post
    drivers = [DriverCandidate(driver_id="d1", lat=0, lng=0, capacity_remaining_units=5)]
    stops = [StopCandidate(stop_id="s1", order_ids=["o1"], lat=0, lng=0, weight_units=1, sla_tier="T2")]

    assignments, unassigned = await client.optimize(drivers, stops)

    assert unassigned == []
    assert assignments[0].driver_id == "d1"
    assert assignments[0].stop_ids == ["s1"]


@pytest.mark.asyncio
async def test_optimize_refreshes_expired_credentials(client):
    client._credentials.valid = False
    response_payload = {"routes": [], "skippedShipments": []}

    async def fake_post(url, json, headers):
        return httpx.Response(200, json=response_payload, request=httpx.Request("POST", url))

    client._http.post = fake_post
    await client.optimize([], [])

    client._credentials.refresh.assert_called_once_with(client._auth_request)


def test_client_selection_falls_back_to_stub_without_project_id():
    with patch("app.optimizer.google_routes_client.settings") as mock_settings:
        mock_settings.google_cloud_project_id = None
        result = get_route_optimization_client()
    assert result.engine_name == "stub_nearest_neighbor"


def test_client_selection_uses_google_client_when_project_id_set():
    with patch("app.optimizer.google_routes_client.settings") as mock_settings, patch(
        "google.auth.default", return_value=(_fake_credentials(), "lmx-os")
    ):
        mock_settings.google_cloud_project_id = "lmx-os"
        result = get_route_optimization_client()
    assert result.engine_name == "google_route_optimization"
