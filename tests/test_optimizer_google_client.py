from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.optimizer import google_routes_client
from app.optimizer.google_routes_client import (
    SLA_TIER_LATENESS_COST_PER_HOUR,
    SLA_TIER_SKIP_PENALTY,
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


def test_build_request_penalizes_hot_shot_skips_more_than_t1(client):
    stops = [
        StopCandidate(stop_id="s_t1", order_ids=["o1"], lat=0, lng=0, weight_units=1, sla_tier="T1"),
        StopCandidate(stop_id="s_hot", order_ids=["o2"], lat=0, lng=0, weight_units=1, sla_tier="HOT_SHOT"),
    ]
    body = client._build_request([], stops)
    penalties = {s["label"]: s["penaltyCost"] for s in body["model"]["shipments"]}
    assert penalties["s_hot"] > penalties["s_t1"]


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


@pytest.fixture(autouse=True)
def _uncached_client():
    """The factory now caches process-wide, so selection tests must start clean.

    That cache is a correctness fix rather than an optimisation - see the
    factory's docstring - but it does mean a test that changes the setting has to
    clear it, in both directions.
    """
    google_routes_client._client = None
    yield
    google_routes_client._client = None


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


def test_the_client_is_built_once_not_per_dispatch_cycle():
    """**The bug this guards.** DispatchOptimizerService() is constructed fresh on
    every cycle, so an uncached factory ran blocking credential discovery on the
    event loop and forced a token refresh every cycle - hundreds of milliseconds
    of a 5s budget spent re-proving an identity we already had."""
    with patch("app.optimizer.google_routes_client.settings") as mock_settings, patch(
        "google.auth.default", return_value=(_fake_credentials(), "lmx-os")
    ) as auth:
        mock_settings.google_cloud_project_id = "lmx-os"
        first = get_route_optimization_client()
        for _ in range(5):
            assert get_route_optimization_client() is first
    assert auth.call_count == 1


# ---------------------------------------------------------------------------
# The solver needs an objective (E1)
# ---------------------------------------------------------------------------


def test_vehicles_carry_cost_coefficients(client):
    """**Without these the request has no objective function.** Vehicle costs
    default to zero in the API, so a model whose only costs are skip penalties
    tells the solver "serve everything you can, and I don't care how" - every
    feasible plan scores identically and the returned sequence is arbitrary. We
    would be paying for a routing solver and asking it to optimize nothing. A
    response built that way still parses perfectly, which is why this needed a
    test rather than a live call to notice."""
    vehicle = client._build_request(
        [DriverCandidate(driver_id="d1", lat=34.05, lng=-118.25, capacity_remaining_units=5)],
        [],
    )["model"]["vehicles"][0]

    assert vehicle["costPerHour"] > 0
    assert vehicle["costPerKilometer"] > 0


def test_skip_penalties_still_dominate_any_achievable_route_cost():
    """The two knobs interact: if driving ever cost more than skipping, the solver
    would start abandoning orders to save fuel. A realistic single-cycle route is a
    couple of hours and a few tens of kilometres."""
    from app.optimizer.google_routes_client import (
        COST_PER_HOUR,
        COST_PER_KILOMETER,
        SLA_TIER_SKIP_PENALTY,
    )

    worst_plausible_route = (4 * COST_PER_HOUR) + (150 * COST_PER_KILOMETER)

    assert worst_plausible_route < min(SLA_TIER_SKIP_PENALTY.values())


# ---------------------------------------------------------------------------
# The real journey, not half of it (E1)
# ---------------------------------------------------------------------------


def _stop(**overrides) -> StopCandidate:
    base = dict(
        stop_id="s1", order_ids=["o1"], lat=34.06, lng=-118.24, weight_units=2, sla_tier="T1"
    )
    base.update(overrides)
    return StopCandidate(**base)


def test_a_stop_with_a_known_drop_is_sent_as_a_pickup_and_a_delivery(client):
    """**The mapping defect this fixes.** `stop.lat/lng` is the SHOP - the pickup -
    and it used to be sent as the shipment's only `deliveries` entry. The solver was
    therefore told the job ended on collection, so the delivery drive was never
    costed and sequencing ignored where the van actually had to go next."""
    shipment = client._build_request(
        [], [_stop(delivery_lat=34.10, delivery_lng=-118.30)]
    )["model"]["shipments"][0]

    assert shipment["pickups"] == [
        {"arrivalLocation": {"latitude": 34.06, "longitude": -118.24}}
    ]
    assert shipment["deliveries"] == [
        {"arrivalLocation": {"latitude": 34.10, "longitude": -118.30}}
    ]


def test_a_stop_with_no_geocoded_drop_falls_back_to_a_single_visit(client):
    """`Order.delivery_lat` is nullable and no source adapter populates it yet, so
    refusing to dispatch would strand orders that are collectable today."""
    shipment = client._build_request([], [_stop()])["model"]["shipments"][0]

    assert "pickups" not in shipment
    assert shipment["deliveries"] == [
        {"arrivalLocation": {"latitude": 34.06, "longitude": -118.24}}
    ]


def test_the_two_legs_share_one_shipment_so_they_cannot_be_split(client):
    """Route Optimization performs a shipment's visit requests atomically, so
    pairing the legs also guarantees a driver is never given a collection whose
    delivery doesn't fit the plan."""
    shipments = client._build_request(
        [], [_stop(delivery_lat=34.10, delivery_lng=-118.30)]
    )["model"]["shipments"]

    assert len(shipments) == 1
    assert shipments[0]["label"] == "s1"


def test_parse_does_not_list_an_order_twice_for_its_two_visits(client):
    """**A bug the pickup/delivery change would otherwise have introduced.** Both
    visits carry the same shipmentLabel, so the previous flat comprehension would
    have put every order in the offer twice."""
    assignments, _ = client._parse_response(
        {
            "routes": [
                {
                    "vehicleLabel": "d1",
                    "visits": [
                        {"shipmentLabel": "s1", "isPickup": True},
                        {"shipmentLabel": "s1", "isPickup": False},
                    ],
                }
            ]
        }
    )

    assert assignments[0].stop_ids == ["s1"]


def test_parse_keeps_the_pickup_sequence_when_legs_interleave(client):
    """First-appearance order is the collection sequence, which is what a
    RouteOffer needs - service.py turns stop_ids into a list of collections and
    the pickup/delivery Stop rows are generated later by accept_offer."""
    assignments, _ = client._parse_response(
        {
            "routes": [
                {
                    "vehicleLabel": "d1",
                    "visits": [
                        {"shipmentLabel": "s1", "isPickup": True},
                        {"shipmentLabel": "s2", "isPickup": True},
                        {"shipmentLabel": "s2", "isPickup": False},
                        {"shipmentLabel": "s1", "isPickup": False},
                    ],
                }
            ]
        }
    )

    assert assignments[0].stop_ids == ["s1", "s2"]


# ---------------------------------------------------------------------------
# Failures that can be diagnosed (E1)
# ---------------------------------------------------------------------------


def _transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_googles_error_message_survives_into_the_exception(client):
    """`raise_for_status()` discarded the body, and for this API the body IS the
    diagnosis: a 403 is either "the API is not enabled on this project" or "this
    service account lacks roles/cloudoptimization.user", and the status code alone
    cannot tell those apart."""
    from app.optimizer.google_routes_client import RouteOptimizationError

    client._http = _transport(
        lambda request: httpx.Response(
            403,
            json={
                "error": {
                    "message": "Cloud Optimization API has not been used in project lmx-os before or it is disabled",
                    "status": "PERMISSION_DENIED",
                }
            },
        )
    )

    with pytest.raises(RouteOptimizationError, match="has not been used in project"):
        await client.optimize([], [])


async def test_a_bad_request_is_not_retried(client):
    """Retrying a 400 spends the 5s cycle budget twice to fail identically. The
    original decorator retried any exception at all."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "invalid vehicle label"}})

    client._http = _transport(handler)

    with pytest.raises(Exception, match="invalid vehicle label"):
        await client.optimize([], [])
    assert len(calls) == 1


async def test_a_server_error_is_retried(client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={"error": {"message": "backend unavailable"}})

    client._http = _transport(handler)

    with pytest.raises(Exception):
        await client.optimize([], [])
    assert len(calls) == 2, "one retry, per stop_after_attempt(2)"


async def test_the_real_exception_is_raised_not_tenacitys_wrapper(client):
    """Without reraise=True the caller got `RetryError[...]` and Google's actual
    complaint was buried in __cause__ - which, combined with the body being
    dropped, made a failed call almost undiagnosable."""
    from tenacity import RetryError

    client._http = _transport(
        lambda request: httpx.Response(503, json={"error": {"message": "backend unavailable"}})
    )

    with pytest.raises(Exception) as exc:
        await client.optimize([], [])

    assert not isinstance(exc.value, RetryError)
    assert "backend unavailable" in str(exc.value)


async def test_a_transport_failure_is_retried_and_named(client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectTimeout("no route to host")

    client._http = _transport(handler)

    with pytest.raises(Exception, match="request failed"):
        await client.optimize([], [])
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# The live verification harness asserts something achievable
# ---------------------------------------------------------------------------


async def test_the_verification_scenario_is_satisfiable():
    """The E1 harness (`scripts/verify_route_optimization.py`) gets run once,
    against a paid API, on a project someone just set up. If its central assertion
    were impossible the run would report a failure that isn't there and send
    someone debugging their GCP config for no reason.

    Checked with the stub solver, which minimises distance by construction: the
    two-cluster scenario really does have "each driver takes the pair beside them"
    as its answer, and the deliberately interleaved input order doesn't change
    that."""
    from app.optimizer.google_routes_client import StubRouteOptimizationClient
    from scripts.verify_route_optimization import _scenario

    drivers, stops = _scenario()
    assignments, unassigned = await StubRouteOptimizationClient().optimize(drivers, stops)

    by_driver = {a.driver_id: set(a.stop_ids) for a in assignments}
    assert not unassigned
    assert by_driver["driver-west"] == {"order-west-hot", "order-west-later"}
    assert by_driver["driver-east"] == {"order-east-1", "order-east-2"}

    # And the urgency check (L23) is satisfiable too. The stub does not model time at
    # all, but it does sort by tier, so "the urgent order is collected first" has a
    # reachable answer - which is what this test exists to establish before anyone spends
    # a paid API call finding out.
    west = next(a for a in assignments if a.driver_id == "driver-west")
    west_pickups = [v.order_id for v in west.visits if v.kind == "pickup"]
    assert west_pickups[0] == "order-west-hot"


def test_the_verification_scenario_sends_both_legs():
    """Otherwise the harness's "both legs were modelled" check passes vacuously."""
    from scripts.verify_route_optimization import _scenario

    _, stops = _scenario()

    assert stops, "scenario has no stops"
    for stop in stops:
        assert stop.has_delivery_location
        # A drop at the same point as the collection would make the delivery leg
        # free, so the scenario wouldn't exercise it.
        assert (stop.lat, stop.lng) != (stop.delivery_lat, stop.delivery_lng)


# ---------------------------------------------------------------------------
# Visit-level sequencing (docs/ROADMAP.md L22)
# ---------------------------------------------------------------------------


def test_an_interleaved_plan_survives_parsing():
    """The response has always carried this and we always threw it away.

    `_visit_sequence` used to deduplicate by `shipmentLabel` and return a flat list of
    order ids, because that was the only shape `RouteAssignment` could hold. A plan that
    collects both orders and then delivers B before A cannot be expressed that way, so
    the drop ordering was discarded here - and the route was rebuilt downstream as
    "every pickup, then every dropoff".
    """
    payload = {
        "routes": [
            {
                "vehicleLabel": "driver-1",
                "visits": [
                    {"shipmentLabel": "A", "isPickup": True, "startTime": "2026-08-11T18:00:00Z"},
                    {"shipmentLabel": "B", "isPickup": True, "startTime": "2026-08-11T18:09:00Z"},
                    {"shipmentLabel": "B", "isPickup": False, "startTime": "2026-08-11T18:21:00Z"},
                    {"shipmentLabel": "A", "isPickup": False, "startTime": "2026-08-11T18:34:00Z"},
                ],
            }
        ],
        "skippedShipments": [],
    }
    assignments, unassigned = GoogleRouteOptimizationClient._parse_response(payload)
    assert unassigned == []
    visits = assignments[0].visits
    assert [(v.order_id, v.kind) for v in visits] == [
        ("A", "pickup"),
        ("B", "pickup"),
        ("B", "delivery"),
        ("A", "delivery"),
    ]


def test_stop_ids_is_still_the_collection_order():
    """Derived rather than stored, so it cannot disagree with the plan.

    `app/optimizer/service.py` asks "which orders did this driver get", and each order
    must appear once - two visits per shipment used to make that a real bug where offers
    were built with duplicated stops.
    """
    payload = {
        "routes": [
            {
                "vehicleLabel": "driver-1",
                "visits": [
                    {"shipmentLabel": "A", "isPickup": True},
                    {"shipmentLabel": "B", "isPickup": True},
                    {"shipmentLabel": "B", "isPickup": False},
                    {"shipmentLabel": "A", "isPickup": False},
                ],
            }
        ]
    }
    assignment = GoogleRouteOptimizationClient._parse_response(payload)[0][0]
    assert assignment.stop_ids == ["A", "B"]


def test_planned_arrival_times_are_carried_through():
    """The eventual replacement for a straight-line ETA.

    Absolute and therefore perishable, but the *intervals* are real road-network travel
    times, which is what app/delivery/eta.py currently has to approximate at an assumed
    average speed.
    """
    payload = {
        "routes": [
            {
                "vehicleLabel": "driver-1",
                "visits": [
                    {"shipmentLabel": "A", "isPickup": True, "startTime": "2026-08-11T18:00:00Z"},
                    {"shipmentLabel": "A", "isPickup": False, "startTime": "2026-08-11T18:26:00Z"},
                ],
            }
        ]
    }
    visits = GoogleRouteOptimizationClient._parse_response(payload)[0][0].visits
    assert visits[0].arrival is not None
    assert (visits[1].arrival - visits[0].arrival).total_seconds() == 26 * 60


def test_an_unparseable_arrival_time_does_not_lose_the_visit():
    """The leg is real work whether or not we can read its timestamp.

    Dropping the visit would silently remove a collection from the route; dropping only
    the time costs an estimate that app/delivery/eta.py computes for itself anyway.
    """
    payload = {
        "routes": [
            {
                "vehicleLabel": "driver-1",
                "visits": [
                    {"shipmentLabel": "A", "isPickup": True, "startTime": "not-a-time"},
                    {"shipmentLabel": "A", "isPickup": False},
                ],
            }
        ]
    }
    visits = GoogleRouteOptimizationClient._parse_response(payload)[0][0].visits
    assert [(v.order_id, v.kind, v.arrival) for v in visits] == [
        ("A", "pickup", None),
        ("A", "delivery", None),
    ]


def test_an_order_with_no_modelled_delivery_gets_a_synthesised_pickup():
    """`Order.delivery_lat` is nullable, so `_build_shipment` files the single visit
    under `deliveries` at the shop's own location.

    That arrives here as a delivery with no pickup. Left alone it would become a drop
    with no collection, which `complete_stop`'s pickup guard refuses forever - a stop the
    driver can neither finish nor escape. The collection is real work, so it is inserted
    immediately before the drop.
    """
    payload = {
        "routes": [
            {
                "vehicleLabel": "driver-1",
                "visits": [
                    {"shipmentLabel": "A", "isPickup": True},
                    {"shipmentLabel": "A", "isPickup": False},
                    {"shipmentLabel": "C", "isPickup": False},
                ],
            }
        ]
    }
    visits = GoogleRouteOptimizationClient._parse_response(payload)[0][0].visits
    assert [(v.order_id, v.kind) for v in visits] == [
        ("A", "pickup"),
        ("A", "delivery"),
        ("C", "pickup"),
        ("C", "delivery"),
    ]


def test_a_visit_with_no_shipment_label_is_ignored():
    """Nothing downstream can act on a leg that names no order."""
    payload = {
        "routes": [
            {
                "vehicleLabel": "driver-1",
                "visits": [
                    {"shipmentLabel": "A", "isPickup": True},
                    {"isPickup": False},
                    {"shipmentLabel": "A", "isPickup": False},
                ],
            }
        ]
    }
    visits = GoogleRouteOptimizationClient._parse_response(payload)[0][0].visits
    assert [(v.order_id, v.kind) for v in visits] == [("A", "pickup"), ("A", "delivery")]


async def test_the_stub_emits_both_legs_for_every_order():
    """So every test that runs a dispatch cycle exercises the plan-driven path.

    The stub has no drop locations and therefore no basis for interleaving - inventing
    one would be fabricated routing dressed up as a plan. What it must do is emit both
    legs, so the construction the real solver feeds is the construction under test
    everywhere, with output identical to what this stub produced before.
    """
    from app.optimizer.google_routes_client import StubRouteOptimizationClient

    drivers = [
        DriverCandidate(driver_id="d1", lat=30.26, lng=-97.73, capacity_remaining_units=10)
    ]
    stops = [
        StopCandidate(
            stop_id="A", order_ids=["A"], lat=30.27, lng=-97.74, sla_tier="T2", weight_units=1
        ),
        StopCandidate(
            stop_id="B", order_ids=["B"], lat=30.28, lng=-97.75, sla_tier="T2", weight_units=1
        ),
    ]
    assignments, unassigned = await StubRouteOptimizationClient().optimize(drivers, stops)
    assert unassigned == []
    visits = assignments[0].visits
    assert [v.kind for v in visits] == ["pickup", "pickup", "delivery", "delivery"]
    assert assignments[0].stop_ids == ["A", "B"]


# ---------------------------------------------------------------------------
# Collection time windows (docs/ROADMAP.md L23)
# ---------------------------------------------------------------------------


def _candidate(stop_id, tier, collect_by, *, with_drop=True):
    return StopCandidate(
        stop_id=stop_id,
        order_ids=[stop_id],
        lat=30.27,
        lng=-97.74,
        delivery_lat=30.30 if with_drop else None,
        delivery_lng=-97.80 if with_drop else None,
        sla_tier=tier,
        weight_units=1,
        collect_by=collect_by,
    )


def _windows(request: dict) -> dict[str, dict | None]:
    """label -> the collection leg's single time window, if any."""
    out: dict[str, dict | None] = {}
    for shipment in request["model"]["shipments"]:
        leg = (shipment.get("pickups") or shipment["deliveries"])[0]
        windows = leg.get("timeWindows")
        out[shipment["label"]] = windows[0] if windows else None
    return out


def _request(stops):
    drivers = [
        DriverCandidate(driver_id="d1", lat=30.26, lng=-97.73, capacity_remaining_units=10)
    ]
    return GoogleRouteOptimizationClient._build_request(drivers, stops)


def test_the_deadline_reaches_the_solver_as_a_window_on_the_collection_leg():
    """The gap this closes.

    The solver was told a HOT_SHOT must not be skipped (a million-unit penalty) and never
    told *when* it was due, so it had no reason to schedule one early. The blunt hoist in
    `accept_offer` was the only thing prioritising the premium tier - and because it
    overrides the plan, it is also what makes the solver's own arrival times unusable as
    ETAs.
    """
    now = datetime.now(timezone.utc)
    window = _windows(_request([_candidate("H", "HOT_SHOT", now + timedelta(minutes=20))]))["H"]
    assert window is not None
    assert window["softEndTime"].endswith("Z")
    assert window["costPerHourAfterSoftEndTime"] == SLA_TIER_LATENESS_COST_PER_HOUR["HOT_SHOT"]


def test_the_window_is_soft_never_hard():
    """A hard deadline the solver cannot meet turns a late collection into no collection.

    An unmeetable `endTime` makes the shipment infeasible, so it lands in
    `skippedShipments` and nobody collects it - far worse for the customer than arriving
    late. Exactly the reasoning that already makes shipments skippable rather than
    mandatory, applied to time.
    """
    now = datetime.now(timezone.utc)
    window = _windows(_request([_candidate("A", "T2", now + timedelta(minutes=5))]))["A"]
    assert "softEndTime" in window
    assert "endTime" not in window
    assert "startTime" not in window


def test_urgency_is_ordered_by_tier_and_never_outweighs_abandonment():
    """Two orderings, and the second one matters as much as the first.

    Lateness costs rank HOT_SHOT above T1 above T2 above T3, which is the sequencing
    signal. They must also stay well below the skip penalties: if being an hour late cost
    more than dropping the order, the solver would rather abandon a T3 than deliver it
    late.
    """
    tiers = ["HOT_SHOT", "T1", "T2", "T3"]
    costs = [SLA_TIER_LATENESS_COST_PER_HOUR[t] for t in tiers]
    assert costs == sorted(costs, reverse=True)
    for tier in tiers:
        assert SLA_TIER_LATENESS_COST_PER_HOUR[tier] < SLA_TIER_SKIP_PENALTY[tier]


def test_an_already_overdue_order_is_clamped_into_the_horizon():
    """The normal case, not an edge case.

    The batch-hold queue releases an order *when its deadline passes*, so most orders
    reach the solver already overdue and their raw deadline sits before
    `globalStartTime` - which the API rejects. Clamping says "as early as possible" and
    leaves the per-tier hourly cost to decide which overdue order goes first.
    """
    now = datetime.now(timezone.utc)
    request = _request([_candidate("L", "T1", now - timedelta(hours=3))])
    window = _windows(request)["L"]
    assert window["softEndTime"] == request["model"]["globalStartTime"]


def test_a_deadline_beyond_the_horizon_is_clamped_too():
    """A T3 due tomorrow is outside an eight-hour model. Sending it unclamped would put a
    time window outside the global window and fail the whole request - taking every other
    order on the cycle down with it."""
    now = datetime.now(timezone.utc)
    request = _request([_candidate("F", "T3", now + timedelta(days=5))])
    assert _windows(request)["F"]["softEndTime"] == request["model"]["globalEndTime"]


def test_no_commitment_means_no_window():
    """Silence rather than invention.

    An order can reach the optimizer with no committed collection time. A window made up
    from nothing would have the solver optimising against a promise nobody made.
    """
    assert _windows(_request([_candidate("N", "T2", None)]))["N"] is None


def test_an_order_with_no_drop_location_still_gets_its_window():
    """`Order.delivery_lat` is nullable, so `_build_shipment` files the single visit under
    `deliveries`. That visit *is* the collection, so the deadline belongs on it - putting
    it only on `pickups` would silently drop the constraint for exactly the orders whose
    routing is already the most approximate."""
    now = datetime.now(timezone.utc)
    request = _request([_candidate("S", "HOT_SHOT", now + timedelta(minutes=15), with_drop=False)])
    shipment = request["model"]["shipments"][0]
    assert "pickups" not in shipment
    assert shipment["deliveries"][0]["timeWindows"][0]["costPerHourAfterSoftEndTime"] == (
        SLA_TIER_LATENESS_COST_PER_HOUR["HOT_SHOT"]
    )


def test_every_window_lies_inside_the_global_window():
    """The invariant behind both clamps, asserted over a mixed batch rather than one
    order at a time - a request is rejected as a whole, so one bad window costs the whole
    cycle."""
    now = datetime.now(timezone.utc)
    request = _request(
        [
            _candidate("A", "HOT_SHOT", now - timedelta(days=2)),
            _candidate("B", "T1", now + timedelta(minutes=30)),
            _candidate("C", "T3", now + timedelta(days=9)),
            _candidate("D", "T2", None),
        ]
    )
    start = request["model"]["globalStartTime"]
    end = request["model"]["globalEndTime"]
    for label, window in _windows(request).items():
        if window is None:
            continue
        assert start <= window["softEndTime"] <= end, label
