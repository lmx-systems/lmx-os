from datetime import datetime
from typing import Literal

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
    # When we committed to collecting this order. `Order.hold_deadline`, which is what
    # `GET /client/orders` reports to the client as `collect_by` - so it is the promise a
    # counter person actually read off the confirmation screen, not an internal deadline.
    #
    # Optional because an order can reach the optimizer through a path that has no
    # commitment attached (a live-route insertion, a test fixture). Absent means no time
    # window is sent, which is honest: a window invented from nothing would make the
    # solver optimise against a promise nobody made.
    collect_by: datetime | None = None

    @property
    def has_delivery_location(self) -> bool:
        return self.delivery_lat is not None and self.delivery_lng is not None


class DriverCandidate(BaseModel):
    driver_id: str
    lat: float
    lng: float
    capacity_remaining_units: float


class RouteVisit(BaseModel):
    """One leg of one order, in the sequence the optimizer planned it.

    A shipment is two visits - collect, then deliver - and the solver plans both
    together, which is what makes its travel times and feasibility right. Before this
    existed, `RouteAssignment` carried a flat list of order ids, so the two visits per
    order were deduplicated down to one and the planned *drop* ordering was thrown away
    on the way out of the client. A route that the solver costed as "collect A, collect
    B, drop B, drop A" was then rebuilt as "collect both, drop both" - legal, longer, and
    not the plan whose arrival times we were quoting.
    """

    order_id: str
    kind: Literal["pickup", "delivery"]
    # The solver's planned arrival. Absolute, and therefore perishable: it assumes the
    # route starts when the plan was made, and an offer can sit unaccepted for up to
    # `job_offer_ttl_seconds`. Carried because the *intervals* between visits are real
    # road-network travel times and are the eventual replacement for
    # app/delivery/eta.py's straight-line estimate. Nothing reads it as an absolute
    # timestamp today.
    arrival: datetime | None = None


class RouteAssignment(BaseModel):
    driver_id: str
    # Every leg, in planned order. Both visits for an order appear, pickup first.
    visits: list[RouteVisit]

    @property
    def stop_ids(self) -> list[str]:
        """The order ids on this route, in first-appearance (collection) order.

        Kept as a derived view because that is what "which orders did this driver get"
        means to `app/optimizer/service.py`, and deriving it removes any chance of the
        two disagreeing.
        """
        seen: list[str] = []
        for visit in self.visits:
            if visit.order_id not in seen:
                seen.append(visit.order_id)
        return seen


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
