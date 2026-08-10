from datetime import datetime

from pydantic import BaseModel


class StopCandidate(BaseModel):
    """A released order (or cluster of commingled orders) waiting for a route assignment."""

    stop_id: str  # order_id, or a synthetic id for a commingled cluster
    order_ids: list[str]
    # The PICKUP location - the shop. Named lat/lng for historical reasons: for a
    # long time it was the only location the optimizer knew about.
    lat: float
    lng: float
    # Where the parts are actually going. Optional because `Order.delivery_lat` is
    # nullable and no source-system adapter populates it yet, so a real order can
    # legitimately reach here with a pickup and no known drop.
    #
    # **Sending these to the solver is the difference between planning the real
    # journey and planning half of it.** Without them the request modeled a single
    # visit AT THE SHOP, so the solver was told the job ended on collection: the
    # delivery leg's travel time was never costed, sequencing ignored where the
    # van actually had to go next, and `considerRoadTraffic` bought accurate
    # traffic for legs that weren't in the model. See
    # app/optimizer/google_routes_client.py::_build_request.
    delivery_lat: float | None = None
    delivery_lng: float | None = None
    weight_units: float
    sla_tier: str

    @property
    def has_delivery_location(self) -> bool:
        return self.delivery_lat is not None and self.delivery_lng is not None


class DriverCandidate(BaseModel):
    driver_id: str
    lat: float
    lng: float
    capacity_remaining_units: float


class RouteAssignment(BaseModel):
    driver_id: str
    stop_ids: list[str]  # in assigned sequence order


class OptimizationResult(BaseModel):
    hub_id: str
    assignments: list[RouteAssignment]
    unassigned_stop_ids: list[str]
    engine: str  # "google_route_optimization" | "stub_nearest_neighbor"
    duration_seconds: float
    over_budget: bool


class LastCycleSnapshot(BaseModel):
    """
    Redis snapshot of the most recently completed Dispatch Optimizer cycle
    for a hub - written by every run_cycle() call, whether triggered
    manually (POST /optimizer/{hub_id}/run-cycle) or automatically off an
    event (app/optimizer/event_trigger.py). Lets a dashboard show "last
    cycle" info for cycles nobody in the browser actually clicked a button
    for - see app/optimizer/last_cycle_store.py.
    """

    hub_id: str
    at: datetime
    engine: str
    duration_seconds: float
    assigned_count: int
    unassigned_count: int
    over_budget: bool
