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
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.schemas.optimizer import (
    DriverCandidate,
    RouteAssignment,
    RouteVisit,
    StopCandidate,
)

logger = structlog.get_logger(__name__)


class RouteOptimizationError(Exception):
    """The solver could not answer, and asking again won't help.

    A malformed request, a disabled API, a service account without
    `roles/cloudoptimization.user`. Carries Google's own error message, because
    for this API that message is usually the actual fix.
    """


class _RetryableRouteOptimizationError(RouteOptimizationError):
    """Same, except a second attempt might work - a timeout, a 429, a 5xx."""

GOOGLE_ROUTE_OPTIMIZATION_ENDPOINT = "https://routeoptimization.googleapis.com/v1/{parent}:optimizeTours"
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# How far ahead the shipment model looks. This is a single dispatch cycle,
# not a full driver shift plan - wide enough that a stop released near the
# end of a cycle still has room to be scheduled, narrow enough that the
# solver isn't wasting time reasoning about assignments hours out that
# will be re-optimized next cycle anyway.
MODEL_HORIZON = timedelta(hours=8)


def _rfc3339(value: datetime) -> str:
    """What this API wants: RFC 3339 with a literal Z rather than +00:00."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

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
# ordered so the solver exhausts T2/T3 headroom before ever skipping a T1,
# and HOT_SHOT (Phase 8's premium, never-commingled tier) before ever
# skipping a T1.
SLA_TIER_SKIP_PENALTY = {"HOT_SHOT": 1_000_000.0, "T1": 100_000.0, "T2": 10_000.0, "T3": 1_000.0}
DEFAULT_SKIP_PENALTY = 10_000.0

# Cost per hour of collecting an order LATER than we committed to, by tier.
#
# The skip penalty above answers "which orders get served at all". This answers "in what
# order", which the solver previously had no way to know: it was told a HOT_SHOT must not
# be dropped and never told when it was due, so it had no reason to schedule one early.
# The blunt HOT_SHOT hoist in `accept_offer` was the only thing prioritising the premium
# tier, and it overrides the plan - which is what made the solver's own arrival times
# unusable as ETAs.
#
# **Soft, not hard, for exactly the reason shipments are skippable rather than
# mandatory.** A hard `endTime` the solver cannot meet makes the shipment infeasible, so
# it gets skipped - turning a late collection into no collection, which is a far worse
# outcome for the customer than a late one. A soft window costs lateness instead: the
# solver hits it when it can and plans the trip anyway when it cannot.
#
# PLACEHOLDERS, and deliberately much smaller than the skip penalties: being an hour late
# must never cost more than abandoning the order entirely, or the solver would rather drop
# a T3 than deliver it late. Ratios between tiers are what matter here rather than the
# absolute figures, and both want tuning against real routes (docs/ROADMAP.md E10).
SLA_TIER_LATENESS_COST_PER_HOUR = {
    "HOT_SHOT": 5_000.0,
    "T1": 1_000.0,
    "T2": 200.0,
    "T3": 20.0,
}
DEFAULT_LATENESS_COST_PER_HOUR = 200.0

# What driving actually costs us, which is the solver's entire objective function.
#
# **Without these the request has no objective at all.** Vehicle costs default to
# zero in the API, so a model whose only costs are the skip penalties above tells
# the solver "serve everything you can, and I don't care how." Every feasible
# assignment then scores identically and the returned sequence is arbitrary - we
# would be paying for a routing solver and asking it to optimize nothing, while
# `considerRoadTraffic` bought accurate traffic data for a route nobody minimised.
# That is invisible in a unit test, because the response parses perfectly.
#
# Denominated in dollars so the numbers stay interpretable next to the penalties:
# roughly a loaded hourly driver cost, and fuel plus wear per kilometre.
# costPerHour rather than costPerTraveledHour on purpose - our drivers are paid
# for waiting time too, so idling at a shop should cost the plan something.
COST_PER_HOUR = 30.0
COST_PER_KILOMETER = 0.35

# The penalties above have to stay far larger than any achievable route cost, or
# the solver starts preferring to skip an order over driving to it. A realistic
# single-cycle route is a couple of hours and a few tens of kilometres - call it
# $100 - so even T3's 1,000 leaves a 10x margin. Worth rechecking if these
# coefficients are ever raised into the same order of magnitude as a penalty.


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

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.25, max=1),
        # Only retry what a retry can fix. Without a predicate this retried ANY
        # exception, including a 400 INVALID_ARGUMENT from a malformed request -
        # doubling latency inside a 5s cycle budget for a failure that is
        # guaranteed to happen again.
        retry=retry_if_exception_type(_RetryableRouteOptimizationError),
        # Surface the real exception rather than tenacity's RetryError. Without
        # this the caller gets "RetryError[...]" and Google's actual complaint is
        # buried in __cause__ - which, combined with the error body being dropped
        # below, made a failed call almost undiagnosable.
        reraise=True,
    )
    async def optimize(
        self, drivers: list[DriverCandidate], stops: list[StopCandidate]
    ) -> tuple[list[RouteAssignment], list[str]]:
        token = await self._bearer_token()
        request_body = self._build_request(drivers, stops)

        try:
            response = await self._http.post(
                GOOGLE_ROUTE_OPTIMIZATION_ENDPOINT.format(parent=f"projects/{self._project_id}"),
                json=request_body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            # Transport-level: timeout, connection reset, DNS. Worth one retry.
            raise _RetryableRouteOptimizationError(f"route optimization request failed: {exc}") from exc

        if response.status_code != 200:
            self._raise_for_error_response(response)

        return self._parse_response(response.json())

    @staticmethod
    def _raise_for_error_response(response: httpx.Response) -> None:
        """Turn a non-200 into an exception that still carries Google's reason.

        **`raise_for_status()` threw the response body away, and for this API the
        body is the whole diagnosis.** A 403 here is almost always one of two
        completely different problems - "Cloud Optimization API has not been used
        in project X before or it is disabled" versus a service account missing
        `roles/cloudoptimization.user` - and the status code alone cannot tell
        them apart. A 400 names the exact field it rejected. Dropping that turns a
        two-minute fix into an afternoon, which is precisely the cost this client
        has been sitting on unverified (docs/ROADMAP.md E1).
        """
        detail = response.text[:1000]
        try:
            payload = response.json()
            detail = payload.get("error", {}).get("message") or detail
        except ValueError:
            pass

        logger.error(
            "route_optimization_error",
            status_code=response.status_code,
            detail=detail,
        )

        message = f"route optimization returned HTTP {response.status_code}: {detail}"
        # 429 and 5xx can succeed on a second attempt; 400/401/403/404 cannot, and
        # retrying them just spends the cycle budget twice to fail identically.
        if response.status_code == 429 or response.status_code >= 500:
            raise _RetryableRouteOptimizationError(message)
        raise RouteOptimizationError(message)

    @staticmethod
    def _collection_window(
        stop: StopCandidate, *, horizon_start: datetime, horizon_end: datetime
    ) -> dict | None:
        """A soft deadline on the collection leg, or None when we promised nothing.

        Clamped into the request's global window. That is not defensiveness - it is the
        normal case. The batch-hold queue releases an order **when its deadline passes**,
        so most orders reach the solver already overdue, and a `softEndTime` in the past
        is outside `globalStartTime` and would be rejected. Clamping to the start of the
        horizon says "as early as possible" and lets the per-tier hourly cost decide
        which overdue order goes first, which is the question that actually matters.
        """
        if stop.collect_by is None:
            return None
        deadline = min(max(stop.collect_by, horizon_start), horizon_end)
        return {
            "softEndTime": _rfc3339(deadline),
            "costPerHourAfterSoftEndTime": SLA_TIER_LATENESS_COST_PER_HOUR.get(
                stop.sla_tier, DEFAULT_LATENESS_COST_PER_HOUR
            ),
        }

    @staticmethod
    def _build_shipment(
        stop: StopCandidate, *, horizon_start: datetime, horizon_end: datetime
    ) -> dict:
        """One order as a shipment: collect at the shop, drop at the customer.

        **This is the mapping defect E1 was most likely to expose.** The request
        previously carried a single `deliveries` entry at `stop.lat/lng` - which is
        the SHOP, the pickup (see app/optimizer/service.py's StopCandidate
        construction). So the solver was told every job both began and ended on
        collection: the delivery leg's drive was never costed, sequencing never
        considered where the van had to go next, and `considerRoadTraffic: True`
        bought accurate traffic for legs that weren't in the model at all. It
        parses perfectly and returns a plausible plan, which is why no unit test
        caught it.

        Route Optimization treats a shipment atomically - either every visit
        request is performed or the whole shipment is skipped - so pairing the two
        legs also means a driver can never be assigned a collection whose delivery
        doesn't fit in the plan.

        Falls back to the old single-visit shape when the drop isn't geocoded,
        because `Order.delivery_lat` is nullable and refusing to dispatch would
        strand orders that are collectable today. Logged, not silent: an
        unmodelled delivery leg is worth knowing about.
        """
        shipment: dict = {
            "label": stop.stop_id,
            "loadDemands": {"weight": {"amount": str(max(round(stop.weight_units), 0))}},
            "penaltyCost": SLA_TIER_SKIP_PENALTY.get(stop.sla_tier, DEFAULT_SKIP_PENALTY),
        }

        window = GoogleRouteOptimizationClient._collection_window(
            stop, horizon_start=horizon_start, horizon_end=horizon_end
        )

        if stop.has_delivery_location:
            collection: dict = {
                "arrivalLocation": {"latitude": stop.lat, "longitude": stop.lng}
            }
            if window:
                collection["timeWindows"] = [window]
            shipment["pickups"] = [collection]
            shipment["deliveries"] = [
                {
                    "arrivalLocation": {
                        "latitude": stop.delivery_lat,
                        "longitude": stop.delivery_lng,
                    }
                }
            ]
            return shipment

        logger.warning(
            "route_optimization_stop_without_delivery_location",
            stop_id=stop.stop_id,
            detail=(
                "planning a visit to the shop only - the delivery leg is not "
                "costed, so travel time and sequencing for this stop are optimistic"
            ),
        )
        # One visit, at the shop. It is the collection, so the window belongs on it -
        # `deliveries` here is the API's slot for "the only visit", not a real drop.
        lone_visit: dict = {"arrivalLocation": {"latitude": stop.lat, "longitude": stop.lng}}
        if window:
            lone_visit["timeWindows"] = [window]
        shipment["deliveries"] = [lone_visit]
        return shipment

    @staticmethod
    def _build_request(drivers: list[DriverCandidate], stops: list[StopCandidate]) -> dict:
        now = datetime.now(timezone.utc)
        horizon_end = now + MODEL_HORIZON

        shipments = [
            GoogleRouteOptimizationClient._build_shipment(
                stop, horizon_start=now, horizon_end=horizon_end
            )
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
                # The objective function. Omitting these leaves every feasible
                # plan equally optimal - see COST_PER_HOUR above.
                "costPerHour": COST_PER_HOUR,
                "costPerKilometer": COST_PER_KILOMETER,
            }
            for driver in drivers
        ]

        return {
            "model": {
                "globalStartTime": _rfc3339(now),
                "globalEndTime": _rfc3339(horizon_end),
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
                visits=GoogleRouteOptimizationClient._visit_sequence(route),
            )
            for route in payload.get("routes", [])
            if route.get("visits")
        ]
        unassigned = [skipped["label"] for skipped in payload.get("skippedShipments", [])]
        return assignments, unassigned

    @staticmethod
    def _visit_sequence(route: dict) -> list[RouteVisit]:
        """Every leg on this route, in the order the solver planned to drive it.

        This used to deduplicate by `shipmentLabel` and return a flat list of order
        ids, which was the only shape `RouteAssignment` could hold. That threw away
        the thing worth having: **a plan can interleave legs** - collect A, collect B,
        drop B, drop A - and a list of order ids cannot express it. The route was then
        rebuilt as "every pickup, then every dropoff", so we drove a longer route than
        the one the solver costed and quoted arrival times from a sequence we were not
        following.

        `isPickup` is what distinguishes the legs. A shipment with no modelled delivery
        (`Order.delivery_lat` is nullable) has one visit, which `_build_shipment` files
        under `deliveries` at the shop's own location - so it arrives here as a
        delivery, and the pickup below is synthesised. Without that, such an order
        would produce a drop with no collection and `complete_stop`'s guard would
        refuse it forever.

        `startTime` is carried through as the planned arrival. It is absolute and
        therefore perishable - see `RouteVisit.arrival`.
        """
        visits: list[RouteVisit] = []
        for raw in route.get("visits", []):
            label = raw.get("shipmentLabel")
            if not label:
                continue
            visits.append(
                RouteVisit(
                    order_id=label,
                    kind="pickup" if raw.get("isPickup") else "delivery",
                    arrival=GoogleRouteOptimizationClient._parse_visit_time(
                        raw.get("startTime")
                    ),
                )
            )

        # An order whose delivery leg was never modelled arrives with a delivery visit
        # and no pickup. The collection is real work regardless, so it is inserted
        # immediately before the drop rather than left out of the route.
        without_pickup = {
            v.order_id for v in visits if v.kind == "delivery"
        } - {v.order_id for v in visits if v.kind == "pickup"}
        if without_pickup:
            repaired: list[RouteVisit] = []
            for visit in visits:
                if visit.kind == "delivery" and visit.order_id in without_pickup:
                    repaired.append(
                        RouteVisit(
                            order_id=visit.order_id, kind="pickup", arrival=visit.arrival
                        )
                    )
                repaired.append(visit)
            visits = repaired

        return visits

    @staticmethod
    def _parse_visit_time(value: str | None) -> datetime | None:
        """Google returns RFC 3339 with a trailing Z, which older Pythons reject."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("route_optimization_unparseable_visit_time", value=value)
            return None


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

        # Highest urgency first (HOT_SHOT before T1 before T2 before T3),
        # then nearest available driver by naive Euclidean distance (fine
        # for a stub; a real optimizer uses road-network distance).
        tier_priority = {"HOT_SHOT": -1, "T1": 0, "T2": 1, "T3": 2}
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

        # Pickups in the greedy order, then the deliveries in the same order.
        #
        # The stub does not model drop locations at all, so it has no basis for
        # interleaving and inventing one here would be fabricated routing dressed up as
        # a plan. Emitting both legs matters anyway: it means every test that runs a
        # dispatch cycle exercises the same visit-driven construction the real solver
        # feeds, with output identical to what this stub produced before.
        route_assignments = [
            RouteAssignment(
                driver_id=driver_id,
                visits=[RouteVisit(order_id=oid, kind="pickup") for oid in stop_ids]
                + [RouteVisit(order_id=oid, kind="delivery") for oid in stop_ids],
            )
            for driver_id, stop_ids in assignments.items()
            if stop_ids
        ]
        return route_assignments, unassigned


_client: RouteOptimizationClient | None = None


def get_route_optimization_client() -> RouteOptimizationClient:
    """The process-wide routing client, built once.

    **Caching this is a correctness fix, not an optimisation.**
    `DispatchOptimizerService()` is constructed fresh on every dispatch cycle
    (app/optimizer/event_trigger.py, app/api/internal_routes.py,
    app/api/routes.py), so without a cache every cycle:

      - ran `google.auth.default()`, a BLOCKING credential discovery call, inside
        a constructor on the event loop;
      - threw away the credential cache, so `_bearer_token()` always saw
        `valid == False` and did a blocking token-endpoint round-trip - several
        hundred milliseconds of the 5s cycle budget, spent re-proving an identity
        we already had;
      - leaked an `httpx.AsyncClient` that nobody closed.

    Same shape as `app/geocoding/__init__.py`'s provider cache. Tests that need a
    different selection reset this module attribute.
    """
    global _client
    if _client is not None:
        return _client

    if settings.google_cloud_project_id:
        logger.info("optimizer_client_selected", engine="google_route_optimization")
        _client = GoogleRouteOptimizationClient(project_id=settings.google_cloud_project_id)
    else:
        logger.warning(
            "optimizer_client_selected",
            engine="stub_nearest_neighbor",
            reason="GOOGLE_CLOUD_PROJECT_ID not configured - running in stub mode",
        )
        _client = StubRouteOptimizationClient()
    return _client
