"""Nothing new gets built with tests and no caller.

Six times now a roadmap row has read `BUILT` for a module that was written,
tested, merged, and wired to nothing: `DEC-0`'s recorder, the four Phase 2
modules that stopped its audit closing, `CON-4`'s exception endpoint with no
reader, `AGT-4`'s hold reasons that `run_cycle` discarded, `CON-4`'s
`released_but_unplaced` status that nothing wrote, and `ING-4`'s export parser
that was moved into `adapters/` without being registered.

Each was found by hand, late, by someone building the next thing on top of it.
This makes it a failing test instead, the same way
`tests/test_architecture_boundaries.py` turned "do not import the adapter from
the core" from a convention into a check.

## What counts as orphaned

A public, undecorated, top-level function in `app/` that no other production
module references by name. Decorated functions are excluded because a FastAPI
route or an event handler is called by its decorator, not by name; private
helpers are excluded by their underscore; and a function used only inside its
own module is not an orphan, which is where the first draft of this check got
almost all of its false positives.

Re-export from a package `__init__.py` does not count as use. That is exactly
how the previous six looked from the outside: importable, listed in `__all__`,
and called by nobody.

## What the allowlist is for

Every entry is a real gap, not a false positive. The list is the honest state of
the record layer and the identity layer, and shrinking it is work - `REC-3`'s
ledger has no writer in production, so it is empty. Adding to it should feel
like what it is: writing down that you built something nothing uses.
"""
import ast
import collections
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"

# Unwired today, with the reason. Each line is a debt, not an exemption.
#
# The record layer is the striking one. `REC-1`'s decision log is written by the
# optimizer on every cycle, and the three things meant to be measured against it
# are written by nothing: no delivery records an outcome, no late order records a
# consequence, no linkage detector runs. The chain the central claim depends on -
# decision, outcome, consequence - has its first link live and the rest inert.
#
# The identity layer is reached by production code through exactly one function,
# `receiver_key_for`, which is a pure string normaliser used by `EXP-2`'s
# stratification. Resolution, merging, node class and the profile store are not
# called from anywhere an order passes through.
KNOWN_ORPHANS: dict[str, str] = {
    # REC-3 - the ledger is written now, by `record_delivery_outcomes` from
    # `app/api/driver_routes.py` on every completed dropoff. What remains
    # unwired is the reading and correcting of it.
    "current_outcome": "REC-3: nothing reads the ledger back yet",
    "supersede_outcome": "REC-3: correction path with no operator surface",
    # REC-2 is wired: a dispatcher records a consequence through
    # `POST /orders/{id}/consequence`, and the nightly tick closes the windows
    # nobody judged. What is left is the reading back.
    "consequence_label": "REC-2: nothing reads a single order's label back",
    "record_driver_day_cost": "REC-2: superseded by record_costs_for_period; named only in a docstring",
    # REC-1's replay. The log itself is written live by the optimizer; this is
    # the verification half, and nothing in the product verifies.
    "replay_inputs": "REC-1: verification tool with no production caller",
    # IDN-2 is wired: `propose_duplicate_locations` fills the queue once a night
    # and `GET /operations/merge-proposals` is where it is worked. Two paths
    # into the same table are still unused, and both are deliberate holds
    # rather than oversights.
    "propose_merge": "IDN-2: queue one pair by hand - no surface, and the detector covers the case",
    "merge_locations": "IDN-2: the auto-merge path §2.2(c) allows only after the founding set is confirmed",
    # IDN-3 / IDN-4. `refresh_dwell_statistics` is wired now - the nightly tick
    # calls it through `refresh_hub_dwell_statistics`, and shops reach the
    # identity layer at all because `link_shop_to_dock` runs at creation. The
    # profile's *stated* fields still have no live writer: they are what a
    # person tells us, and nothing asks.
    "set_access": "IDN-4: profile field with no live writer",
    "set_autonomy_fit": "IDN-4: profile field with no live writer",
    "set_receiving_hours": "IDN-4: profile field with no live writer",
    # EXP-1 - a reader for an arm no client has contracted. Inert rather than
    # broken, and it becomes live the day somebody signs the clause.
    "arm_for_order": "EXP-1: reader, inert until a client contracts an arm",
    # STL-2 - the signature gate. `scripts/sign_basis.py` signs a basis and does
    # not call this; nothing refuses an unsigned one.
    "require_agreed": "STL-2: nothing enforces the signature it exists to require",
    # Built this session, and both are honest about what they are waiting for.
    "backfill_orders": "ING-3: blocked on ING-4's export adapter - nothing can feed it",
    "overrides_for_order": "CON-2: the console reads overrides through explain_order instead",
    "labelled_overrides": "CON-3: the training-set export has no reader yet",
}


def _public_functions() -> dict[str, str]:
    """Public, undecorated, top-level functions in `app/`, name -> file."""
    found: dict[str, str] = {}
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_") or node.decorator_list:
                continue
            found.setdefault(node.name, str(path.relative_to(ROOT)))
    return found


def _referenced_names(tree: ast.AST) -> collections.Counter:
    """Every name this module actually *uses*, from the syntax tree.

    Deliberately not a text search. A regex counts the module's own `__all__`
    entry, its docstring, and any comment mentioning the function - which made
    an earlier draft report the entire record layer as wired up, by the module
    talking about itself.
    """
    used: collections.Counter = collections.Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used[node.id] += 1
        elif isinstance(node, ast.Attribute):
            used[node.attr] += 1
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                used[alias.name] += 1
    return used


def _orphans() -> dict[str, str]:
    """Functions nothing calls.

    Three things count as a caller, and each was a false positive in an earlier
    draft of this check:

    * **Another module in `app/`** - the obvious one.
    * **The defining module itself.** A public helper used only by its own
      module is not an orphan, and treating it as one produced most of the
      first run's noise.
    * **A script in `scripts/`.** An operator running `settle_month.py` is using
      the code; `render_statement` is not dead because no web request reaches it.

    A re-export from a package `__init__.py` is not a caller. That is precisely
    what the orphans this file exists for looked like from outside: importable,
    listed in `__all__`, called by nobody.
    """
    functions = _public_functions()

    used_elsewhere: collections.Counter = collections.Counter()
    own_use: dict[str, collections.Counter] = {}
    for path in list(APP.rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        if "__pycache__" in str(path) or path.name == "__init__.py":
            continue
        rel = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text())
        names = _referenced_names(tree)
        own_use[rel] = names
        used_elsewhere.update(names)

    orphans: dict[str, str] = {}
    for name, defined_in in functions.items():
        total = used_elsewhere[name]
        # A function's own module referencing it counts, but its `def` does not
        # appear as a Name node, so nothing needs subtracting here.
        if total == 0 and own_use.get(defined_in, collections.Counter())[name] == 0:
            orphans[name] = defined_in
    return orphans


def test_no_module_is_built_with_tests_and_no_caller():
    """The check. A new name here means something was built that nothing uses.

    If this fails on code you just wrote, the fix is almost never to add the
    name to `KNOWN_ORPHANS` - it is to wire the thing up, or to notice that you
    do not need it yet. The allowlist is for debt that already exists and has
    been written down.
    """
    orphans = _orphans()
    new = {name: path for name, path in orphans.items() if name not in KNOWN_ORPHANS}

    assert not new, (
        "built with no caller:\n"
        + "\n".join(f"  {name}  ({path})" for name, path in sorted(new.items()))
        + "\n\nWire it up, or add it to KNOWN_ORPHANS with the roadmap ID and why."
    )


def test_the_allowlist_does_not_outlive_the_debt():
    """An entry that is no longer an orphan has been wired up - remove it.

    Without this the list becomes a place names go to be forgotten, which is the
    same failure it exists to catch, one level up.
    """
    orphans = _orphans()
    stale = sorted(set(KNOWN_ORPHANS) - set(orphans))

    assert not stale, (
        "these are wired up now - remove them from KNOWN_ORPHANS:\n"
        + "\n".join(f"  {name}" for name in stale)
    )


@pytest.mark.parametrize("name,reason", sorted(KNOWN_ORPHANS.items()))
def test_every_allowlisted_orphan_names_its_roadmap_item(name, reason):
    """A reason without an item is a shrug. The point of writing the debt down
    is that somebody can find the row it belongs to."""
    assert re.match(r"^[A-Z]{3}-\d+:", reason), (
        f"{name}'s reason must start with a roadmap ID, e.g. 'REC-3: ...' - got {reason!r}"
    )
