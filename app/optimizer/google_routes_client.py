"""
Client interface to the routing math provider.

Design doc decision (Section on build-vs-buy): routing math itself is
bought, not built - Google Route Optimization API + Google Maps Platform
for geocoding/traffic. This module defines a small interface so the rest
of the optimizer never talks to Google directly, and provides:

  - GoogleRouteOptimizationClient: real HTTP client, used when
    GOOGLE_CLOUD_PROJECT_ID is configured.
  - StubRouteOptimizationClient: deterministic nearest-neighbor fallback,
    used automatically when no project is configured, so the rest of the
    stack (ingestion -> SLA -> hold queue -> optimizer -> API) is runnable
    and testable end-to-end without live Google credentials or network
    access. This is NOT a real optimizer - it exists so the pipeline can be
    developed, demoed, and unit-tested before Phase 1 procurement lands.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

import google.auth
import google.auth.transport.requests
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.schemas.optimizer import DriverCandidate, RouteAssignment, StopCandidate

logger = structlog.get_logger(__name__)

GOOGLE_ROUTE_OPTIMIZATION_ENDPOINT = "https://routeoptimization.googleapis.com/v1/{parent}:optimizeTours"
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# How far ahead the shipment model looks. This is a single dispatch cycle,
# not a full driver shift plan - wide enough that a stop released near the
# end of a cycle still has room to be scheduled, narrow enough that the
# solver isn't wasting time reasoning about assignments hours out that
# will be re-optimized next cycle anyway.
MODEL_HORIZON = timedelta(hours=8)

# Per Section 9's <5s cycle budget and the 4s httpx client timeout below,
# ask Google's solver to return well inside that window rather than let it
# consume its default solve budget.
SOLVE_TIMEOUT = "3s"

# Cost of leaving a stop unassigned this cycle, by SLA tier. Shipments are
# deliberately made *skippable* (via penaltyCost) rather than mandatory:
# a mandatory shipment the solver can't fit (e.g. no driver has capacity)
# makes the whole request infeasible and returns an error instead of a
# partial plan. Skippable shipments instead show up in `skippedShipments`
# and stay in the hold queue for next cycle (see service.py) - the same
# "leave it held, don't drop it" behavior the stub client has. Costs are
# ordered so the solver exhausts T2/T3 headroom before ever skipping a T1.
SLA_TIER_SKIP_PENALTY = {"T1": 100_000.0, "T2": 10_000.0, "T3": 1_000.0}
DEFAULT_SKIP_PENALTY = 10_000.0


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

    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._http = httpx.AsyncClient(timeout=4.0)  # leaves headroom inside the 5s cycle budget
        # Application Default Credentials: a service account JSON at
        # GOOGLE_APPLICATION_CREDENTIALS, workload identity, or gcloud
        # user creds in local dev. Route Optimization is a Cloud IAM API,
        # not an API-key product, so there's no API key to plumb through.
        self._credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        self._auth_request = google.auth.transport.requests.Request()

    async def _bearer_token(self) -> str:
        # google-auth's refresh() is a blocking network call (token
        # endpoint round-trip); keep it off the event loop. `.valid` is
        # false on the first call and once the cached token nears expiry,
        # so most calls skip the refresh entirely.
        if not self._credentials.valid:
            await asyncio.to_thread(self._credentials.refresh, self._auth_request)
        return self._credentials.token

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.25, max=1))
    async def optimize(
        self, drivers: list[DriverCandidate], stops: list[StopCandidate]
    ) -> tuple[list[RouteAssignment], list[str]]:
        token = await self._bearer_token()
        request_body = self._build_request(drivers, stops)

        response = await self._http.post(
            GOOGLE_ROUTE_OPTIMIZATION_ENDPOINT.format(parent=f"projects/{self._project_id}"),
            json=request_body,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return self._parse_response(response.json())

    @staticmethod
    def _build_request(drivers: list[DriverCandidate], stops: list[StopCandidate]) -> dict:
        now = datetime.now(timezone.utc)
        horizon_end = now + MODEL_HORIZON

        shipments = [
            {
                "label": stop.stop_id,
                "deliveries": [
                    {"arrivalLocation": {"latitude": stop.lat, "longitude": stop.lng}}
                ],
                "loadDemands": {
                    "weight": {"amount": str(max(round(stop.weight_units), 0))}
                },
                "penaltyCost": SLA_TIER_SKIP_PENALTY.get(stop.sla_tier, DEFAULT_SKIP_PENALTY),
            }
            for stop in stops
        ]

        vehicles = [
            {
                "label": driver.driver_id,
                "startLocation": {"latitude": driver.lat, "longitude": driver.lng},
                # No endLocation: field drivers don't return to a depot at
                # the end of a single re-optimization cycle - the route
                # just ends at the last delivery.
                "loadLimits": {
                    "weight": {"maxLoad": str(max(round(driver.capacity_remaining_units), 0))}
                },
            }
            for driver in drivers
        ]

        return {
            "model": {
                "globalStartTime": now.isoformat().replace("+00:00", "Z"),
                "globalEndTime": horizon_end.isoformat().replace("+00:00", "Z"),
                "shipments": shipments,
                "vehicles": vehicles,
            },
            "timeout": SOLVE_TIMEOUT,
            "considerRoadTraffic": True,
        }

    @staticmethod
    def _parse_response(payload: dict) -> tuple[list[RouteAssignment], list[str]]:
        assignments = [
            RouteAssignment(
                driver_id=route["vehicleLabel"],
                stop_ids=[visit["shipmentLabel"] for visit in route.get("visits", [])],
            )
            for route in payload.get("routes", [])
            if route.get("visits")
        ]
        unassigned = [skipped["label"] for skipped in payload.get("skippedShipments", [])]
        return assignments, unassigned


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
    if settings.google_cloud_project_id:
        logger.info("optimizer_client_selected", engine="google_route_optimization")
        return GoogleRouteOptimizationClient(project_id=settings.google_cloud_project_id)
    logger.warning(
        "optimizer_client_selected",
        engine="stub_nearest_neighbor",
        reason="GOOGLE_CLOUD_PROJECT_ID not configured - running in stub mode",
    )
    return StubRouteOptimizationClient()
