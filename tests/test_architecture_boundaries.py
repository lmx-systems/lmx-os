"""
The import direction that `CLAUDE.md` asserts, checked instead of hoped for.

> **One canonical order object. Many source adapters. Many status sinks. The core
> never knows where an order came from.** If an adapter needs a change inside the SLA
> engine, hold queue, or optimizer, *the contract is wrong* - fix the contract, not
> the core.

That claim is the reason two demand paths and four intake routes coexist without the
dispatch engine growing a branch per customer. It has held so far - measured, not
assumed - but nothing enforced it. One `from app.webhooks import ...` inside
`app/optimizer/` would break it silently, pass every other test, and only surface
later as a circular import or as a routing change that cannot be made without touching
a customer integration.

These are offline tests. They parse the tree with `ast` rather than importing it, so a
violation is reported as a file and a line rather than as an ImportError from
somewhere in the middle of a chain.

Three layers, and only one rule between them:

  - **Foundation** - `models`, `db`, `config`, `schemas`, `events`, `redis_client`,
    `messaging`. Anyone may import these.
  - **Core** - the dispatch engine. `CLAUDE.md` names the hold queue, the SLA engine
    and the optimizer specifically; `fleet_state`, `delivery` and `compliance` are the
    same layer because they decide and execute what a driver does.
  - **Edge** - source adapters and client-facing surfaces. Every way an order arrives,
    every way status leaves, everything a client or a recipient touches.

**Core must not import Edge.** Edge importing Core is the whole point and is not
restricted.
"""
import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# The dispatch engine. A change here affects every customer at once, which is exactly
# why it must not be reachable from any one customer's integration.
CORE = (
    "batch_queue",
    "optimizer",
    "sla",
    "fleet_state",
    "delivery",
    "compliance",
)

# Adapters and client-facing surfaces. Anything whose shape is negotiated with somebody
# outside this company.
EDGE = (
    "ingestion",
    "client_api",
    "client_auth",
    "webhooks",
    "billing",
    "reporting",
    "legal",
    "geocoding",
    "tracking",
    "returns",
    "learning_loop",
    "api",
)

# Deliberately in neither list, with reasons - so the classification is a statement
# rather than an oversight.
#
#   orders      - the contract itself. The canonical state machine and the sink
#                 fan-out, imported from both sides by design. Guarded separately
#                 below, because sinks.py reaches Edge-ward and has to stay lazy.
#   gig_platform- a demand path. It used to hold the shared travel-time model, which
#                 made the core depend on it; that moved to `app/travel.py`, and
#                 `test_the_core_does_not_depend_on_a_demand_path` now forbids the
#                 dependency rather than capping it. What is left here is genuinely
#                 gig economics - vehicle cost per mile, driver cost per hour, the
#                 reposition fraction - which only this path asks about. In neither
#                 list because it is a demand path rather than an adapter.
#   payroll,
#   storage,
#   health,
#   ops_auth,
#   driver_auth,
#   hub_calendar - infrastructure and internal operations, not order-shaped.
#   events      - the in-process hub event bus. Foundation: core publishes to it, edge
#                 subscribes, and it knows nothing about either.
UNCLASSIFIED = (
    "orders",
    "gig_platform",
    "events",
    "payroll",
    "storage",
    "health",
    "ops_auth",
    "driver_auth",
    "hub_calendar",
    "messaging",
    "models",
    "schemas",
)


def _app_imports(path: pathlib.Path) -> list[tuple[str, int]]:
    """Every `app.<package>` this file imports, with the line it is on."""
    found: list[tuple[str, int]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            found.append((node.module.split(".")[1], node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    found.append((alias.name.split(".")[1], node.lineno))
    return found


def _files(package: str) -> list[pathlib.Path]:
    return [p for p in sorted((APP / package).rglob("*.py")) if "__pycache__" not in p.parts]


# ---------------------------------------------------------------------------
# Anti-vacuity. A test that names packages has to fail when the names stop existing,
# or a rename turns it into a test of nothing that keeps passing forever.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("package", CORE + EDGE)
def test_every_named_package_still_exists(package):
    assert (APP / package).is_dir(), (
        f"app/{package}/ is gone. If it moved, update this test's CORE/EDGE lists - "
        "leaving a stale name here silently stops checking a real boundary."
    )


def test_every_package_in_app_is_classified():
    """A new package has to be placed deliberately.

    Without this, `app/whatever/` could be added tomorrow, import the entire Edge, and
    no test would notice - the boundary would be enforced only for the packages someone
    remembered to list.
    """
    present = {
        p.name
        for p in APP.iterdir()
        if p.is_dir() and p.name != "__pycache__" and (p / "__init__.py").exists()
    }
    classified = set(CORE) | set(EDGE) | set(UNCLASSIFIED)
    unplaced = present - classified
    assert not unplaced, (
        f"new package(s) {sorted(unplaced)} are not classified as CORE, EDGE, or "
        "explicitly UNCLASSIFIED with a reason. Decide which side of the boundary "
        "they are on - that decision is the point of this test."
    )


def test_core_and_edge_do_not_overlap():
    assert not (set(CORE) & set(EDGE))


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("package", CORE)
def test_core_does_not_import_the_edge(package):
    """The dispatch engine cannot reach a source adapter or a client-facing surface.

    If this fails, resist the obvious fix. The import is a symptom: something in the
    core needed to know where an order came from, which per `CLAUDE.md` means the
    contract is wrong. Widen the contract (`app/schemas/lmx_order.py`, `app/orders/`),
    do not add the import.
    """
    violations = [
        f"app/{package}/{path.relative_to(APP / package)}:{line} imports app.{module}"
        for path in _files(package)
        for module, line in _app_imports(path)
        if module in EDGE
    ]
    assert not violations, "core reaching into the edge:\n  " + "\n  ".join(violations)


def test_the_edge_really_does_import_the_core():
    """The other direction, asserted so the rule above cannot pass vacuously.

    If intake stopped depending on the hold queue and the SLA engine entirely, these
    would not be two layers of one system any more - and the test above would be
    guarding a boundary that no longer has anything on the other side of it.
    """
    reaching = {
        module
        for path in _files("ingestion")
        for module, _ in _app_imports(path)
        if module in CORE
    }
    assert {"batch_queue", "sla"} <= reaching, (
        f"ingestion no longer imports the hold queue and the SLA engine (found: "
        f"{sorted(reaching)}). Either the architecture changed materially or this test "
        "is now checking nothing."
    )


# ---------------------------------------------------------------------------
# The two documented inversions, which are load-bearing and easy to undo by accident
# ---------------------------------------------------------------------------


def test_the_sink_fan_out_stays_lazy():
    """`app/orders/sinks.py` builds its sinks with imports inside the function.

    Deliberate, and the comment there says so: the webhook sink lives at the Edge, and a
    module-level import would drag `app.webhooks` into the import graph of everything
    that advances an order status - which is the entire core. It was a circular import
    the first time it was written that way.
    """
    path = APP / "orders" / "sinks.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_level = {
        node.module.split(".")[1]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.")
    }
    leaked = module_level & set(EDGE)
    assert not leaked, (
        f"app/orders/sinks.py imports {sorted(leaked)} at module level. Move it back "
        "inside _build_sinks() - the lazy import is what keeps the edge out of the "
        "core's import graph."
    )


def test_the_core_does_not_depend_on_a_demand_path():
    """`app/delivery/` must not import `app/gig_platform/`, at all.

    This replaces a test that *bounded* that dependency rather than forbidding it. The
    travel-time model four surfaces share used to live in
    `app/gig_platform/economics.py`, so the dispatch core depended on one particular way
    of getting work for a primitive that has nothing to do with gig platforms. The
    sharing was right and the address was wrong; it now lives in `app/travel.py` and the
    dependency is gone rather than merely capped.

    `gig_platform` stays out of CORE and EDGE both - it is a demand path, and what
    remains in it (vehicle cost per mile, driver cost per hour, the reposition fraction)
    answers "is this job worth taking", which only that path asks.
    """
    reaching = {
        f"app/delivery/{path.relative_to(APP / 'delivery')}:{line}"
        for path in _files("delivery")
        for module, line in _app_imports(path)
        if module == "gig_platform"
    }
    assert not reaching, (
        "app/delivery/ imports app/gig_platform/ again:\n  " + "\n  ".join(sorted(reaching))
    )


def test_the_shared_travel_model_has_exactly_one_home():
    """Four surfaces quote a customer or a driver a time from these numbers.

    A second definition anywhere is the bug this guards: a driver, a recipient and a
    counter person reading three different arrival times for the same delivery.
    """
    import re

    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "travel.py":
            continue
        text = path.read_text(encoding="utf-8")
        # A *definition*, not a use. `= 18.0` or `def minutes_for_miles`.
        if re.search(r"^\s*PLACEHOLDER_AVERAGE_SPEED_MPH\s*=", text, re.M):
            offenders.append(f"{path.relative_to(APP)} redefines PLACEHOLDER_AVERAGE_SPEED_MPH")
        if re.search(r"^\s*def minutes_for_miles\b", text, re.M):
            offenders.append(f"{path.relative_to(APP)} redefines minutes_for_miles")
    assert not offenders, "the travel model must live only in app/travel.py:\n  " + "\n  ".join(offenders)
