"""Every endpoint is reachable from something a person uses.

`tests/test_no_new_orphans.py` skips decorated functions, because a FastAPI
route has no Python caller by design — the framework calls it. That exemption is
a hole the exact shape of the defect this month's audit is about: **an endpoint
with tests and no console caller looks finished from every angle except the one
that matters**, and the orphan check cannot see it. The audit found five such
routes by reading `routes.py` against the dashboard by hand. This is that read,
automated, so the sixth does not need somebody to go looking.

## How reachability is decided, and why not by matching whole paths

A route is reachable if the **last literal segment of its path** appears
anywhere in the three front ends. `/admin/hubs/{hub_id}/cod-disputes` is
reachable if `cod-disputes` is mentioned; `/driver/stops/{stop_id}/scan-parcel`
is not, because nothing says `scan-parcel`.

The obvious implementation — normalise both sides to `/admin/hubs/*/closures`
and compare — was written first and abandoned, after four separate false
positives that each looked like a real finding:

* a character class that excluded `?` and `=`, so every path with a query
  string failed to match **at all**;
* `` `/admin/signups?status=${encodeURIComponent(status)}` ``, whose `${...}`
  contains parentheses;
* `` `${apiBaseUrl()}/driver/me/route-events` ``, which does not begin with `/`;
* `` `...${status ? `?status=${x}` : ``}` ``, whose braces nest, so a
  non-greedy `[^}]*` stops at the first `}` and glues `${status` to the path.

Every one of those reported a route as unreachable that a front end plainly
calls, and the last of them was a call site written the same afternoon. **A
check that cries wolf gets allowlisted into uselessness**, so the mechanism has
to be one that is wrong in the safe direction.

Segment matching is that. It can miss a genuinely unreachable route whose last
segment happens to appear in the front end for some other reason — it under-
reports — and it does not confuse itself about interpolation, because it never
reconstructs a path. **This is a floor, not a proof.** A route it passes is not
certainly reachable; a route it fails is worth a person's attention.
"""
from __future__ import annotations

import collections
import importlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Everything a person can click, in all three of them.
FRONTENDS = ("dashboard/src", "client-portal/src", "driver-app/src")

ROUTERS = (
    "routes",
    "admin_routes",
    "client_routes",
    "driver_routes",
    "ops_auth_routes",
    "public_routes",
    "public_api_routes",
    "internal_routes",
    "webhooks",
)

# Routers whose callers are not people with browsers, so a front-end reference
# would be the surprising thing. Named rather than skipped silently: an
# unclassified router would quietly decide what counts as reachable, which is
# the question this file exists to answer.
NOT_CALLED_BY_A_FRONT_END: dict[str, str] = {
    "internal_routes": (
        "a scheduler and an operator with a shell - the nightly dispatch, the "
        "learning loop, retention pruning, webhook delivery"
    ),
    "webhooks": "Twilio calls these; nothing of ours does",
    "public_api_routes": (
        "a customer's own integration (docs/ORDER_API.md), which is not in this "
        "repository"
    ),
}

# Endpoints nothing a person uses can reach, each with the reason it is
# tolerated and what would close it. Same discipline as `KNOWN_ORPHANS`: an
# entry is a debt with a name on it, not a silence.
#
# `W1`'s returns flow is most of this list, which is the finding the check was
# built to make visible - and the shape of it is worth stating precisely. The
# *client* half shipped: `client-portal/src/components/ReturnsPanel.tsx` lists
# what is awaiting pickup and flags cores as ready. The **driver** half and the
# **ops** half did not. So a counter person can say the cores are ready, and the
# driver who arrives to collect them has no button and the operator who has to
# close the loop has no list.
KNOWN_UNREACHABLE: dict[str, str] = {
    "POST /driver/stops/{stop_id}/scan-parcel": (
        "W1 - per-parcel scanning. The app calls /driver/stops/{id}/scan, which "
        "takes a count; this takes one parcel at a time and no screen does"
    ),
    "POST /driver/stops/{stop_id}/collect-return": (
        "W1 slice 3 - the driver leg exists on the backend and the app has no screen for it"
    ),
    "POST /driver/stops/{stop_id}/return-not-ready": "W1 slice 3 - same",
    "POST /driver/stops/{stop_id}/return-to-shop": "W1 slice 3 - same",
    "POST /admin/returns/{return_id}/mark-returned": (
        "W1 slice 3 - the ops manual mark the item names, with no panel to make it "
        "from. The client half shipped (ReturnsPanel); this half did not"
    ),
    "POST /admin/returns/{return_id}/reschedule": "W1 slice 4 - the not_ready -> ready reschedule, ops side",
    "POST /driver/me/gig-jobs/evaluate": (
        "G3 - the app has no gig screen; offers arrive and are answered on the "
        "platform's own app today"
    ),
    "GET /admin/clients/{client_id}/sla-terms": (
        "W3 - what a client was promised, set by scripts/set_client_sla_terms.py. "
        "An operator cannot read back what they agreed to"
    ),
    "PUT /admin/clients/{client_id}/sla-terms": "W3 - same",
}


def _routes() -> list[tuple[str, str, str]]:
    """`(router module, method, path)` for every registered endpoint."""
    found: list[tuple[str, str, str]] = []
    for module in ROUTERS:
        router = importlib.import_module(f"app.api.{module}").router
        for route in router.routes:
            methods = getattr(route, "methods", None)
            if not methods:
                continue
            for method in sorted(methods):
                if method in {"HEAD", "OPTIONS"}:
                    continue
                found.append((module, method, route.path))
    return found


def _front_end_source() -> str:
    """Every front-end source file, concatenated.

    One blob rather than per-file, because which file mentions a segment is not
    the question — whether anything does is.
    """
    text: list[str] = []
    for directory in FRONTENDS:
        root = ROOT / directory
        if not root.exists():  # pragma: no cover - all three are in the repo
            continue
        for path in root.rglob("*.ts*"):
            text.append(path.read_text())
    return "\n".join(text)


def _distinguishing_segment(path: str) -> str | None:
    """The last part of a path that is not a parameter.

    `/admin/hubs/{hub_id}/closures` -> `closures`. It is what separates a route
    from its siblings, and therefore the thing a caller must name. A path that
    is nothing but parameters has none, and is skipped rather than guessed at.
    """
    literal = [part for part in path.strip("/").split("/") if not part.startswith("{")]
    return literal[-1] if literal else None


def test_every_router_is_classified():
    """A new router must say whether a front end is supposed to call it.

    The same rule `test_architecture_boundaries.py` applies to packages. A
    router nobody classified would either be checked against front ends it was
    never meant to have, or quietly exempted — and both are decisions that
    should be written down.
    """
    on_disk = {
        path.stem
        for path in (ROOT / "app" / "api").glob("*.py")
        if path.stem not in {"__init__"}
    }

    assert on_disk - set(ROUTERS) == set(), (
        "add these to ROUTERS, and to NOT_CALLED_BY_A_FRONT_END if no front end "
        "should call them: " + ", ".join(sorted(on_disk - set(ROUTERS)))
    )


def test_no_endpoint_is_unreachable_from_every_front_end():
    """An endpoint nothing a person uses can reach.

    Not the same as unused — an admin *can* curl it. It is the weaker sibling of
    a module with no caller, and the audit found five of them by hand
    (`docs/ROADMAP_AUDIT_2026-09.md`). The cost is the same either way: a
    capability that exists, is tested, and does nothing for anybody.
    """
    source = _front_end_source()

    unreachable: dict[str, str] = {}
    for module, method, path in _routes():
        if module in NOT_CALLED_BY_A_FRONT_END:
            continue
        segment = _distinguishing_segment(path)
        if segment is None or segment in source:
            continue
        key = f"{method} {path}"
        if key in KNOWN_UNREACHABLE:
            continue
        unreachable[key] = module

    assert not unreachable, (
        "no front end can reach these:\n"
        + "\n".join(f"    {key}  ({module})" for key, module in sorted(unreachable.items()))
        + "\n\n  Build the surface, or add it to KNOWN_UNREACHABLE with the "
        "roadmap item and why."
    )


def test_the_allowlist_does_not_outlive_the_gap():
    """An entry that is now reachable must be removed.

    The half that keeps the list honest. Without it, `KNOWN_UNREACHABLE` records
    what was true the day somebody wrote it and then quietly stops being true —
    which is how an allowlist becomes the thing it was meant to prevent.
    """
    source = _front_end_source()
    registered = {f"{method} {path}" for _, method, path in _routes()}

    now_reachable = {
        key
        for key in KNOWN_UNREACHABLE
        if key in registered
        and (segment := _distinguishing_segment(key.split(" ", 1)[1])) is not None
        and segment in source
    }
    assert not now_reachable, (
        "these are reachable now - remove them from KNOWN_UNREACHABLE: "
        + ", ".join(sorted(now_reachable))
    )

    gone = {key for key in KNOWN_UNREACHABLE if key not in registered}
    assert not gone, (
        "these routes no longer exist - remove them from KNOWN_UNREACHABLE: "
        + ", ".join(sorted(gone))
    )


def test_every_allowlisted_route_names_its_roadmap_item():
    """A reason with no item is a shrug.

    Borrowed verbatim from `test_no_new_orphans.py`, for the same reason: the
    entries that are hardest to justify are the ones most likely to be written
    vaguely.
    """
    vague = {
        key
        for key, reason in KNOWN_UNREACHABLE.items()
        if not any(character.isdigit() for character in reason)
    }
    assert not vague, (
        "give these a roadmap item (W4, G3, ...) and say what would close them: "
        + ", ".join(sorted(vague))
    )


def test_the_check_would_notice_a_new_one():
    """The check bites.

    An invariant nobody has seen fail is an invariant nobody knows works. This
    asserts the mechanism directly: a segment no front end mentions is not
    found in the front-end source, and one that is, is.
    """
    source = _front_end_source()

    assert _distinguishing_segment("/admin/hubs/{hub_id}/closures") == "closures"
    assert _distinguishing_segment("/admin/orders/{order_id}") == "orders"
    assert _distinguishing_segment("/{anything}") is None

    # A segment invented for this test, which nothing can mention.
    assert "cod-disputes" in source
    assert "kowalczyk-disputes" not in source


def test_the_routers_are_all_importable_and_carry_routes():
    """Guards the silent-pass shape this file could fail in.

    If `ROUTERS` named a module that had moved, `_routes()` would raise; if a
    router were empty, the check would pass by having nothing to check. The
    second is the dangerous one, because it looks exactly like success.
    """
    counted: collections.Counter = collections.Counter()
    for module, _, _ in _routes():
        counted[module] += 1

    empty = [module for module in ROUTERS if counted[module] == 0]
    assert not empty, f"these routers registered no routes: {', '.join(empty)}"
    assert sum(counted.values()) > 100, (
        f"only {sum(counted.values())} routes found - the collection is probably broken"
    )
