"""
One real `optimizeTours` call against a live Google Cloud project
(docs/ROADMAP.md E1).

**Why a script rather than a test.** Every other check in this repo runs offline.
This one costs money, needs credentials, and reaches a third party - so it must
never run in CI, and it must be trivial to run by hand exactly once when a project
is first wired up. `tests/test_optimizer_google_client.py` proves the mapping
matches what we *believe* the API accepts; only this proves the API agrees.

**What it is actually verifying.** The client
(`app/optimizer/google_routes_client.py`) has been complete-looking and unverified
for months, and a wrong request here does not fail loudly - it returns a
plausible plan. Three specific ways that can happen, all checked below:

  1. Labels don't round-trip, so assignments can't be mapped back to orders and
     the cycle silently assigns nothing.
  2. Vehicle costs are missing or ignored, so every feasible plan is equally
     optimal and the returned sequence is arbitrary. This is the failure that
     looks most like success.
  3. Pickups and deliveries are not both honoured, so the solver plans half the
     journey and its travel times are optimistic.

The scenario is built so the answer is knowable in advance: two drivers well apart,
four orders clustered two-and-two near each of them. A solver that is genuinely
minimising cost must give each driver the pair beside them. One that is ignoring
cost will still return something valid, and check 3 below is what catches it.

Usage:

    GOOGLE_CLOUD_PROJECT_ID=your-project \\
    GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json \\
    .venv/bin/python -m scripts.verify_route_optimization

Add --json to dump the full request and response for eyeballing.

Requires the Route Optimization API enabled on the project and
`roles/cloudoptimization.user` on the credential. If either is missing, the error
message printed is Google's own, which is usually the fix.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.config import settings
from app.optimizer.google_routes_client import (
    GoogleRouteOptimizationClient,
    RouteOptimizationError,
)
from app.schemas.optimizer import DriverCandidate, StopCandidate

# Two clusters ~15km apart in Austin. Far enough that a cost-minimising solver
# cannot be indifferent between serving the near pair and the far pair.
WEST = (30.2672, -97.7431)
EAST = (30.2500, -97.5700)


def _scenario() -> tuple[list[DriverCandidate], list[StopCandidate]]:
    drivers = [
        DriverCandidate(
            driver_id="driver-west", lat=WEST[0], lng=WEST[1], capacity_remaining_units=10
        ),
        DriverCandidate(
            driver_id="driver-east", lat=EAST[0], lng=EAST[1], capacity_remaining_units=10
        ),
    ]
    stops = [
        # Deliberately interleaved west/east/west/east in the request, so a solver
        # that just returns input order produces a visibly absurd plan.
        _stop("order-west-1", WEST, offset=0.004),
        _stop("order-east-1", EAST, offset=0.004),
        _stop("order-west-2", WEST, offset=0.008),
        _stop("order-east-2", EAST, offset=0.008),
    ]
    return drivers, stops


def _stop(label: str, near: tuple[float, float], *, offset: float) -> StopCandidate:
    """A collection near `near` with a drop a little further out, so both legs are
    real and the delivery is not the same point as the pickup."""
    return StopCandidate(
        stop_id=label,
        order_ids=[label],
        lat=near[0] + offset,
        lng=near[1] + offset,
        delivery_lat=near[0] + offset + 0.006,
        delivery_lng=near[1] + offset + 0.006,
        weight_units=1,
        sla_tier="T2",
    )


class _Report:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, ok: bool, name: str, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if detail:
            print(f"         {detail}")
        if not ok:
            self.failures.append(name)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="dump the full request and response"
    )
    args = parser.parse_args()

    project_id = settings.google_cloud_project_id
    if not project_id:
        print(
            "GOOGLE_CLOUD_PROJECT_ID is not set, so there is nothing to verify "
            "against.\nWithout it the app runs StubRouteOptimizationClient, which "
            "makes no network calls at all.",
            file=sys.stderr,
        )
        return 2

    print(f"Project: {project_id}")
    drivers, stops = _scenario()

    try:
        client = GoogleRouteOptimizationClient(project_id=project_id)
    except Exception as exc:  # noqa: BLE001 - credential discovery, reported as-is
        print(
            f"\nCould not load credentials: {type(exc).__name__}: {exc}\n"
            "Set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON key, or "
            "run `gcloud auth application-default login`.",
            file=sys.stderr,
        )
        return 2

    request_body = client._build_request(drivers, stops)
    if args.json:
        print("\n--- request ---")
        print(json.dumps(request_body, indent=2))

    print("\nCalling optimizeTours...")
    try:
        assignments, unassigned = await client.optimize(drivers, stops)
    except RouteOptimizationError as exc:
        # The whole reason _raise_for_error_response exists: this message is
        # normally the actual fix.
        print(f"\nThe call failed:\n  {exc}", file=sys.stderr)
        print(
            "\nCommon causes:\n"
            "  - Route Optimization API not enabled on this project\n"
            "  - the credential lacks roles/cloudoptimization.user\n"
            "  - billing not enabled",
            file=sys.stderr,
        )
        return 1
    finally:
        await client._http.aclose()

    if args.json:
        print("\n--- parsed ---")
        print(json.dumps({"assignments": [a.model_dump() for a in assignments],
                          "unassigned": unassigned}, indent=2))

    print("\nResults:")
    for assignment in assignments:
        print(f"  {assignment.driver_id}: {' -> '.join(assignment.stop_ids)}")
    if unassigned:
        print(f"  unassigned: {', '.join(unassigned)}")

    report = _Report()
    print("\nChecks:")

    # 1. Labels round-trip. If they don't, service.py can't map an assignment back
    # to an order and the cycle assigns nothing while looking successful.
    returned = {stop_id for a in assignments for stop_id in a.stop_ids} | set(unassigned)
    expected = {s.stop_id for s in stops}
    report.check(
        returned == expected,
        "every order label came back exactly once",
        f"missing: {sorted(expected - returned)}  unexpected: {sorted(returned - expected)}",
    )
    report.check(
        {a.driver_id for a in assignments} <= {d.driver_id for d in drivers},
        "vehicle labels round-trip",
    )

    # 2. No order was dropped. With penalties this far above route cost, a skip
    # here means something is infeasible rather than expensive.
    report.check(not unassigned, "nothing was skipped")

    # 3. THE ONE THAT MATTERS. Each driver should get the pair beside them. If
    # vehicle costs are absent or ignored, the solver is indifferent and this is
    # the check that notices - everything above would still pass.
    by_driver = {a.driver_id: set(a.stop_ids) for a in assignments}
    west_correct = by_driver.get("driver-west") == {"order-west-1", "order-west-2"}
    east_correct = by_driver.get("driver-east") == {"order-east-1", "order-east-2"}
    report.check(
        west_correct and east_correct,
        "each driver got the cluster beside them (the solver is really minimising)",
        "If this fails while everything above passes, the request has no effective "
        "objective function - check costPerHour/costPerKilometer on the vehicles.",
    )

    # 4. Both legs were modelled. A shipment with a pickup and a delivery produces
    # two visits; we dedupe to one order id, so the raw visit count is the evidence
    # that Google honoured both.
    report.check(
        all("pickups" in s and "deliveries" in s for s in request_body["model"]["shipments"]),
        "every shipment was sent with both a collection and a drop",
    )

    print()
    if report.failures:
        print(f"{len(report.failures)} check(s) failed: {', '.join(report.failures)}")
        return 1
    print("All checks passed - the request/response mapping is verified (E1).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
