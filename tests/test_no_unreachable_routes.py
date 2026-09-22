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

# **Which front end is supposed to reach which router.** Matching against all
# three was the first version, and it let a real one through within hours:
# `GET /admin/hubs/{id}/returns` has no dashboard caller, and the segment
# `returns` matched anyway because `client-portal` has a `ReturnsPanel` calling
# a *different* endpoint. An admin route referenced only in the client portal is
# not reachable by an admin.
#
# Scoping is the fix, and it is also the more honest question: not "does anybody
# anywhere mention this word" but "can the person this route is for get to it".
AUDIENCE: dict[str, tuple[str, ...]] = {
    "routes": ("dashboard/src",),           # ops console
    "admin_routes": ("dashboard/src",),     # ops console, admin-gated
    "ops_auth_routes": ("dashboard/src",),
    "client_routes": ("client-portal/src",),
    "driver_routes": ("driver-app/src",),
    "public_routes": ("client-portal/src", "dashboard/src"),  # the tracking page
}

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
# `W1`'s returns flow was most of this list when the check was written - the
# finding it was built to make visible. The *client* half had shipped
# (`client-portal/src/components/ReturnsPanel.tsx`); the **driver** and **ops**
# halves had not, so a counter person could say the cores were ready and the
# driver who arrived to collect them had no button.
#
# **Both halves are built now**, and all five entries came straight back off
# this list within hours of it existing - the companion test doing its job on
# the person who wrote it, twice in one afternoon.
# Endpoints that exist for a machine, not a person. Distinct from
# `KNOWN_UNREACHABLE` on purpose: that list is debt with a name on it, and these
# are never going to have a screen. Folding them together would make the debt
# list longer and less believable.
NOT_A_FEATURE: frozenset[str] = frozenset(
    {
        "GET /health",   # the load balancer
        "GET /metrics",  # Prometheus
    }
)

KNOWN_UNREACHABLE: dict[str, str] = {
    "POST /driver/stops/{stop_id}/scan-parcel": (
        "W1 - per-parcel scanning. The app calls /driver/stops/{id}/scan, which "
        "takes a count; this takes one parcel at a time and no screen does"
    ),
    "GET /driver/me/devices": (
        "S1 - a driver cannot see or sign out their own sessions. An admin can, "
        "as of the CON-1 panel work; the driver's own half has no screen"
    ),
    "DELETE /driver/me/devices/{device_id}": "S1 - same",
    "GET /driver/stops/{stop_id}/parcels": (
        "W10 - the per-parcel list, sibling of scan-parcel below and unreachable "
        "for the same reason: the app scans a count, not parcels"
    ),
    "GET /driver/me/gig-jobs": "G3 - the app has no gig screen",
    "POST /driver/me/gig-jobs": "G3 - same",
    "PATCH /driver/me/gig-jobs/{gig_job_id}": "G3 - same",
    "GET /admin/clients/{client_id}/rates": (
        "F5 - the dashboard sets rates once, inside signup approval, and has no "
        "panel to read or change them afterwards. A rate is a contract term "
        "nobody can look up"
    ),
    "PUT /admin/clients/{client_id}/rates": "F5 - same",
    "POST /fleet/{hub_id}/drivers/location": (
        "F1 - the superseded ops-admin write path. The app posts to "
        "/driver/me/location; F1's own note says this one would never have "
        "populated a position in production"
    ),
    "POST /fleet/{hub_id}/drivers/state": "F1 - same",
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


def _front_end_source(directories: tuple[str, ...] = FRONTENDS) -> str:
    """The named front ends' source, concatenated.

    One blob rather than per-file, because *which* file mentions a segment is
    not the question — whether the right front end does is, and `AUDIENCE`
    decides which that is.
    """
    text: list[str] = []
    for directory in directories:
        root = ROOT / directory
        if not root.exists():  # pragma: no cover - all three are in the repo
            continue
        for path in root.rglob("*.ts*"):
            text.append(path.read_text())
    return "\n".join(text)


def _distinguishing_segment(path: str) -> str | None:
    """The last part of a path that is not a parameter, with its leading slash.

    `/admin/hubs/{hub_id}/closures` -> `/closures`. It is what separates a route
    from its siblings, and therefore the thing a caller must name. A path that
    is nothing but parameters has none, and is skipped rather than guessed at.

    **The slash is not decoration.** Matching the bare word let
    `GET /admin/hubs/{id}/returns` pass while nothing in the dashboard called
    it: `returns` is ordinary English and appears in a dozen comments
    ("this returns the ..."). `/returns` appears in none of them. Every other
    segment in this codebase is hyphenated or plural enough not to collide, and
    this one word was enough to hide a whole panel's absence.
    """
    literal = [part for part in path.strip("/").split("/") if not part.startswith("{")]
    return f"/{literal[-1]}" if literal else None


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

    # Every checked router must say *whose* front end is supposed to reach it.
    # Without this a new router would be silently checked against nothing.
    unplaced = set(ROUTERS) - set(AUDIENCE) - set(NOT_CALLED_BY_A_FRONT_END)
    assert not unplaced, (
        "say which front end should reach these, in AUDIENCE: "
        + ", ".join(sorted(unplaced))
    )


def test_no_endpoint_is_unreachable_from_every_front_end():
    """An endpoint nothing a person uses can reach.

    Not the same as unused — an admin *can* curl it. It is the weaker sibling of
    a module with no caller, and the audit found five of them by hand
    (`docs/ROADMAP_AUDIT_2026-09.md`). The cost is the same either way: a
    capability that exists, is tested, and does nothing for anybody.
    """
    sources = {
        module: _front_end_source(directories)
        for module, directories in AUDIENCE.items()
    }

    unreachable: dict[str, str] = {}
    for module, method, path in _routes():
        if module in NOT_CALLED_BY_A_FRONT_END:
            continue
        key = f"{method} {path}"
        if key in NOT_A_FEATURE or key in KNOWN_UNREACHABLE:
            continue
        segment = _distinguishing_segment(path)
        if segment is None or segment in sources[module]:
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
    registered = {
        f"{method} {path}": module for module, method, path in _routes()
    }
    sources = {
        module: _front_end_source(directories)
        for module, directories in AUDIENCE.items()
    }

    now_reachable = {
        key
        for key in KNOWN_UNREACHABLE
        if key in registered
        and (segment := _distinguishing_segment(key.split(" ", 1)[1])) is not None
        and segment in sources[registered[key]]
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
    source = _front_end_source(("dashboard/src",))

    assert _distinguishing_segment("/admin/hubs/{hub_id}/closures") == "/closures"
    assert _distinguishing_segment("/admin/orders/{order_id}") == "/orders"
    assert _distinguishing_segment("/{anything}") is None

    # The slash is what makes this a path rather than a word. `returns` is
    # ordinary English and appears in comments; `/returns` does not.
    assert "/cod-disputes" in source
    assert "/kowalczyk-disputes" not in source


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
