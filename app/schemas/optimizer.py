from datetime import datetime

from pydantic import BaseModel


class StopCandidate(BaseModel):
    """A released order (or cluster of commingled orders) waiting for a route assignment."""

    stop_id: str  # order_id, or a synthetic id for a commingled cluster
    order_ids: list[str]
    lat: float
    lng: float
    weight_units: float
    sla_tier: str


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
