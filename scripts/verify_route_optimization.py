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
  4. Collection deadlines are ignored, so the premium tier is not prioritised (L23).
     `accept_offer` currently hoists HOT_SHOT legs to compensate, and **check 5 below
     is the evidence that retires that hoist** - a hot shot collected third looks like
     a perfectly normal route, so this cannot be eyeballed.

The scenario is built so the answer is knowable in advance: two drivers well apart,
four orders clustered two-and-two near each of them. A solver that is genuinely
minimising cost must give each driver the pair beside them. One that is ignoring
cost will still return something valid, and check 3 below is what catches it. The
west pair is additionally separated only by its collection deadline, which is what
check 5 reads.

---

FIRST-TIME SETUP
================

Route Optimization is **IAM-gated, not an API-key product** - `GOOGLE_MAPS_API_KEY`
is for the other Maps Platform APIs and will not work here. That is why this client
uses Application Default Credentials and asks for the `cloud-platform` scope.

On the project (billing must already be enabled):

    gcloud services enable routeoptimization.googleapis.com --project=PROJECT_ID
    gcloud projects add-iam-policy-binding PROJECT_ID \\
      --member="user:you@example.com" --role="roles/cloudoptimization.user"

Locally:

    brew install --cask google-cloud-sdk
    gcloud auth login
    gcloud auth application-default login
    gcloud auth application-default set-quota-project PROJECT_ID

**That last line is the one people miss.** ADC user credentials bill against a quota
project, and without one the call fails with an error that does not obviously say so.

**Do not create a service account key for this.** A downloaded JSON key is a
long-lived credential that ends up in a backup or a commit, and none is needed: ADC
covers this local run, and Cloud Run uses its own service account via workload
identity with the same role and no key file at all. `GOOGLE_APPLICATION_CREDENTIALS`
remains supported for the case where a key genuinely is the only option.

Usage:

    # GOOGLE_CLOUD_PROJECT_ID in .env, or inline:
    GOOGLE_CLOUD_PROJECT_ID=your-project .venv/bin/python -m scripts.verify_route_optimization

**Run it as a module.** `python scripts/verify_route_optimization.py` fails with
`No module named 'app'`, because the repo root is only on `sys.path` under `-m`.

Add --json to dump the full request and response for eyeballing.

If the call fails, the message printed is Google's own, which is usually the actual
fix - API not enabled, credential missing `roles/cloudoptimization.user`, or billing
off.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

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
    now = datetime.now(timezone.utc)
    stops = [
        # Deliberately interleaved west/east/west/east in the request, so a solver
        # that just returns input order produces a visibly absurd plan.
        #
        # The west pair also carries the urgency test. Both are beside driver-west, so
        # distance cannot separate them - the only thing that can is the collection
        # deadline. The HOT_SHOT is due in ten minutes and the T3 in six hours, and
        # `_build_shipment` sends those as soft windows with per-tier lateness costs.
        # If the solver honours them, the hot shot is collected first.
        _stop("order-west-hot", WEST, offset=0.004, tier="HOT_SHOT",
              collect_by=now + timedelta(minutes=10)),
        _stop("order-east-1", EAST, offset=0.004, collect_by=now + timedelta(minutes=90)),
        _stop("order-west-later", WEST, offset=0.008, tier="T3",
              collect_by=now + timedelta(hours=6)),
        _stop("order-east-2", EAST, offset=0.008, collect_by=now + timedelta(minutes=90)),
    ]
    return drivers, stops


def _stop(
    label: str,
    near: tuple[float, float],
    *,
    offset: float,
    tier: str = "T2",
    collect_by: datetime | None = None,
) -> StopCandidate:
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
        sla_tier=tier,
        collect_by=collect_by,
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
    parser = argparse.ArgumentParser(
        description=__doc__,
        # Raw, so the setup commands in the docstring survive `--help` as something
        # copy-pasteable. argparse reflows by default, which ran them together into one
        # unusable line - and this docstring exists precisely to be followed by hand.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
        legs = " -> ".join(
            f"{v.order_id}({'P' if v.kind == 'pickup' else 'D'})" for v in assignment.visits
        )
        print(f"  {assignment.driver_id}: {legs}")
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
    west_correct = by_driver.get("driver-west") == {"order-west-hot", "order-west-later"}
    east_correct = by_driver.get("driver-east") == {"order-east-1", "order-east-2"}
    report.check(
        west_correct and east_correct,
        "each driver got the cluster beside them (the solver is really minimising)",
        "If this fails while everything above passes, the request has no effective "
        "objective function - check costPerHour/costPerKilometer on the vehicles.",
    )

    # 4. Both legs came back, per order. Since L22 the visits are carried through
    # rather than deduplicated, so this is now checkable on the response rather than
    # only on the request we sent.
    report.check(
        all("pickups" in s and "deliveries" in s for s in request_body["model"]["shipments"]),
        "every shipment was sent with both a collection and a drop",
    )
    legs_by_order: dict[str, set[str]] = {}
    for assignment in assignments:
        for visit in assignment.visits:
            legs_by_order.setdefault(visit.order_id, set()).add(visit.kind)
    incomplete = {o: sorted(k) for o, k in legs_by_order.items() if k != {"pickup", "delivery"}}
    report.check(
        not incomplete,
        "every assigned order came back with both legs",
        f"incomplete: {incomplete}",
    )

    # 5. THE ONE THAT WOULD LET THE HOT_SHOT HOIST GO (docs/ROADMAP.md L23).
    #
    # `accept_offer` currently hoists a HOT_SHOT's legs to the front of the route,
    # overriding the plan - which is also what stops the solver's own arrival times being
    # usable as ETAs. That override exists because the solver was never told when a
    # collection was due. It now is, as a soft window with a per-tier lateness cost.
    #
    # Both west orders sit beside driver-west, so distance cannot separate them. If the
    # hot shot is collected first, the windows are doing the work and the hoist can be
    # deleted. If it is not, the hoist is still the only thing protecting the premium
    # tier and must stay.
    west = next((a for a in assignments if a.driver_id == "driver-west"), None)
    west_pickups = [v.order_id for v in (west.visits if west else []) if v.kind == "pickup"]
    report.check(
        west_pickups[:1] == ["order-west-hot"],
        "the urgent order is collected first (collection windows are honoured)",
        f"west pickup order: {west_pickups}\n"
        "         If this fails, leave the HOT_SHOT hoist in accept_offer alone - the "
        "solver is not sequencing by deadline, and removing it would silently stop "
        "prioritising a tier customers pay extra for.",
    )

    # Informational rather than a check: whether this particular geometry produced an
    # interleaved plan. It is a property of the input, not of the integration, so a
    # non-interleaved answer here is not a failure.
    for assignment in assignments:
        kinds = [v.kind for v in assignment.visits]
        first_delivery = kinds.index("delivery") if "delivery" in kinds else len(kinds)
        interleaved = any(k == "pickup" for k in kinds[first_delivery:])
        print(
            f"  [info] {assignment.driver_id}: "
            f"{'interleaved legs' if interleaved else 'collect-all-then-drop-all'}"
        )

    print()
    if report.failures:
        print(f"{len(report.failures)} check(s) failed: {', '.join(report.failures)}")
        return 1
    print(
        "All checks passed - the request/response mapping is verified (E1), "
        "visits round-trip (L22), and collection windows are honoured (L23)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
