import pytest

from app.optimizer.google_routes_client import StubRouteOptimizationClient
from app.schemas.optimizer import DriverCandidate, StopCandidate


@pytest.mark.asyncio
async def test_stub_assigns_nearest_driver_within_capacity():
    client = StubRouteOptimizationClient()
    drivers = [
        DriverCandidate(driver_id="d1", lat=34.05, lng=-118.25, capacity_remaining_units=5),
        DriverCandidate(driver_id="d2", lat=40.0, lng=-120.0, capacity_remaining_units=5),
    ]
    stops = [
        StopCandidate(stop_id="s1", order_ids=["o1"], lat=34.051, lng=-118.25, weight_units=1, sla_tier="T2"),
    ]
    assignments, unassigned = await client.optimize(drivers, stops)
    assert unassigned == []
    assert len(assignments) == 1
    assert assignments[0].driver_id == "d1"
    assert assignments[0].stop_ids == ["s1"]


@pytest.mark.asyncio
async def test_stub_prioritizes_t1_over_t2():
    client = StubRouteOptimizationClient()
    # Single driver with capacity for only one stop.
    drivers = [DriverCandidate(driver_id="d1", lat=0, lng=0, capacity_remaining_units=1)]
    stops = [
        StopCandidate(stop_id="s_t2", order_ids=["o1"], lat=0, lng=0, weight_units=1, sla_tier="T2"),
        StopCandidate(stop_id="s_t1", order_ids=["o2"], lat=0, lng=0, weight_units=1, sla_tier="T1"),
    ]
    assignments, unassigned = await client.optimize(drivers, stops)
    assert assignments[0].stop_ids == ["s_t1"]
    assert unassigned == ["s_t2"]


@pytest.mark.asyncio
async def test_stub_leaves_stop_unassigned_when_no_capacity():
    client = StubRouteOptimizationClient()
    drivers = [DriverCandidate(driver_id="d1", lat=0, lng=0, capacity_remaining_units=0)]
    stops = [StopCandidate(stop_id="s1", order_ids=["o1"], lat=0, lng=0, weight_units=1, sla_tier="T2")]
    assignments, unassigned = await client.optimize(drivers, stops)
    assert assignments == []
    assert unassigned == ["s1"]
