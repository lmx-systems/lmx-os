"""
Client interface to the routing math provider.

Design doc decision (Section on build-vs-buy): routing math itself is
bought, not built - Google Route Optimization API + Google Maps Platform
for geocoding/traffic. This module defines a small interface so the rest
of the optimizer never talks to Google directly, and provides:

  - GoogleRouteOptimizationClient: real HTTP client, used when
    GOOGLE_ROUTES_API_KEY is configured.
  - StubRouteOptimizationClient: deterministic nearest-neighbor fallback,
    used automatically when no API key is configured, so the rest of the
    stack (ingestion -> SLA -> hold queue -> optimizer -> API) is runnable
    and testable end-to-end without live Google credentials or network
    access. This is NOT a real optimizer - it exists so the pipeline can be
    developed, demoed, and unit-tested before Phase 1 procurement lands.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.schemas.optimizer import DriverCandidate, RouteAssignment, StopCandidate

logger = structlog.get_logger(__name__)

GOOGLE_ROUTE_OPTIMIZATION_ENDPOINT = (
    "https://routeoptimization.googleapis.com/v1/projects/{project}:optimizeTours"
)


class RouteOptimizationClient(ABC):
    engine_name: str

    @abstractmethod
    async def optimize(
        self, drivers: list[DriverCandidate], stops: list[StopCandidate]
    ) -> tuple[list[RouteAssignment], list[str]]:
        """Returns (assignments, unassigned_stop_ids)."""
        raise NotImplementedError


class GoogleRouteOptimizationClient(RouteOptimizationClient):
    engine_name = "google_route_optimization"

    def __init__(self, api_key: str, project_id: str) -> None:
        self._api_key = api_key
        self._project_id = project_id
        self._http = httpx.AsyncClient(timeout=4.0)  # leaves headroom inside the 5s cycle budget

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.25, max=1))
    async def optimize(
        self, drivers: list[DriverCandidate], stops: list[StopCandidate]
    ) -> tuple[list[RouteAssignment], list[str]]:
        # NOTE: request/response shape here is a placeholder pending real
        # integration work - Google's optimizeTours API has a specific
        # shipments/vehicles schema that should be mapped in explicitly
        # once GOOGLE_ROUTES_API_KEY is provisioned and we're integrating
        # for real. Left intentionally minimal so this is an obvious spot
        # to fill in, not something that silently does the wrong thing.
        raise NotImplementedError(
            "GoogleRouteOptimizationClient.optimize is a Phase 1 integration "
            "placeholder - implement the optimizeTours request/response mapping "
            "before enabling this client against a live Google account."
        )


class StubRouteOptimizationClient(RouteOptimizationClient):
    """Greedy nearest-neighbor assignment. Deterministic, no network calls."""

    engine_name = "stub_nearest_neighbor"

    async def optimize(
        self, drivers: list[DriverCandidate], stops: list[StopCandidate]
    ) -> tuple[list[RouteAssignment], list[str]]:
        remaining_capacity = {d.driver_id: d.capacity_remaining_units for d in drivers}
        driver_positions = {d.driver_id: (d.lat, d.lng) for d in drivers}
        assignments: dict[str, list[str]] = {d.driver_id: [] for d in drivers}
        unassigned: list[str] = []

        # Highest urgency first (T1 before T2 before T3), then nearest
        # available driver by naive Euclidean distance (fine for a stub;
        # a real optimizer uses road-network distance).
        tier_priority = {"T1": 0, "T2": 1, "T3": 2}
        sorted_stops = sorted(stops, key=lambda s: tier_priority.get(s.sla_tier, 1))

        for stop in sorted_stops:
            best_driver_id: str | None = None
            best_distance = float("inf")
            for driver_id, (lat, lng) in driver_positions.items():
                if remaining_capacity[driver_id] < stop.weight_units:
                    continue
                distance = ((lat - stop.lat) ** 2 + (lng - stop.lng) ** 2) ** 0.5
                if distance < best_distance:
                    best_distance = distance
                    best_driver_id = driver_id

            if best_driver_id is None:
                unassigned.append(stop.stop_id)
                continue

            assignments[best_driver_id].append(stop.stop_id)
            remaining_capacity[best_driver_id] -= stop.weight_units
            # Move the driver's reference point to the assigned stop so the
            # next nearest-neighbor check reflects the route in progress.
            driver_positions[best_driver_id] = (stop.lat, stop.lng)

        route_assignments = [
            RouteAssignment(driver_id=driver_id, stop_ids=stop_ids)
            for driver_id, stop_ids in assignments.items()
            if stop_ids
        ]
        return route_assignments, unassigned


def get_route_optimization_client() -> RouteOptimizationClient:
    if settings.google_routes_api_key:
        logger.info("optimizer_client_selected", engine="google_route_optimization")
        return GoogleRouteOptimizationClient(
            api_key=settings.google_routes_api_key, project_id="lmx-os"
        )
    logger.warning(
        "optimizer_client_selected",
        engine="stub_nearest_neighbor",
        reason="GOOGLE_ROUTES_API_KEY not configured - running in stub mode",
    )
    return StubRouteOptimizationClient()
