"""Resolve an address to the one `Location` that represents its dock.

IDN-1. The only operation identity needs today: given an address, hand back the
dock, creating it the first time it is seen.

Deliberately not here yet: the alias map and merge review queue (IDN-2), node
classes (IDN-3), and the receiver profile (IDN-4). This resolves on normalized
address alone, which is §2.2(b)'s "the existing geocoded_address dedup feeds
Location resolution rather than being replaced" and nothing more. Two spellings
of one dock that differ by more than whitespace and case will produce two rows;
collapsing those is IDN-2's job, done by a person for the founding set.
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.geocoding.base import normalize_address
from app.models.location import Location

# Normalized keys that are a placeholder for an address rather than an address.
#
# This list exists because of one real row in the design partner's export whose
# city, state and zip are literally `N/A`. Without it that row normalizes to the
# perfectly usable key "n/a", and every such row across every customer collapses
# into a single shared dock - a fictional place that then accumulates the dwell
# times, stop counts and profile of a dozen unrelated addresses. That is worse
# than the duplicate docks this table exists to remove, because it looks like a
# real dock and no downstream check can tell.
#
# Kept deliberately short and whole-string only. A substring match would strip
# "Unknown Road" of its identity, and the cost of missing a placeholder is one
# extra dock that IDN-2's review queue surfaces to a person - visible, and
# cheap. The cost of over-matching is a real address silently losing its dock.
_PLACEHOLDER_KEYS = frozenset(
    {
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "tbd",
        "-",
        "--",
        ".",
        "?",
    }
)


def _carries_a_place(key: str) -> bool:
    """Whether a normalized key names somewhere a driver could be sent.

    Every segment being a placeholder counts as no place: the export's
    `N/A, N/A, N/A` is the same non-answer as a bare `N/A`, written three times.
    """
    if not key:
        return False
    if key in _PLACEHOLDER_KEYS:
        return False
    segments = [segment.strip() for segment in key.split(",")]
    return not all(segment in _PLACEHOLDER_KEYS or not segment for segment in segments)


async def resolve_location(
    session: AsyncSession,
    *,
    address: str,
    lat: float | None = None,
    lng: float | None = None,
) -> Location:
    """Return the `Location` for `address`, creating it if this is the first sight.

    `lat`/`lng` are only used when creating. An existing dock is never moved by a
    later caller: coordinates that disagree mean either a geocoder changed its
    mind or two different places normalized together, and silently taking the
    newest answer would hide both. The first is harmless, the second is the
    failure this table exists to prevent, so neither should be resolved by
    overwriting.

    Raises `ValueError` on an address that names no place - either because it
    normalizes to nothing, or because it is a placeholder like `N/A`. Both would
    otherwise collect unrelated addresses into one shared fictional dock. The
    caller's correct response is to leave `Shop.location_id` null, which records
    "not resolved" as the fact it is.
    """
    key = normalize_address(address)
    if not _carries_a_place(key):
        raise ValueError(f"address does not name a resolvable place: {address!r}")

    existing = await session.scalar(
        select(Location).where(Location.normalized_address == key)
    )
    if existing is not None:
        return await canonical_location(session, existing)

    location = Location(normalized_address=key, address=address, lat=lat, lng=lng)
    try:
        # A SAVEPOINT, not a plain flush. Resolution is almost always one step
        # inside a larger unit of work - onboarding writes a Client and a Shop
        # and then asks for the dock - and the caller owns the transaction
        # boundary. Losing the race below must undo this INSERT and nothing
        # else; a session-wide rollback here would silently discard the caller's
        # own writes and hand back a Location as though all was well.
        async with session.begin_nested():
            session.add(location)
            await session.flush()
    except IntegrityError:
        # Another transaction created the same dock between the SELECT and the
        # INSERT. The unique constraint is what makes that a conflict instead of
        # a duplicate, and the right answer is theirs, not a second row.
        conflicting = await session.scalar(
            select(Location).where(Location.normalized_address == key)
        )
        if conflicting is None:
            # The IntegrityError was not the race we assumed. Surfacing it beats
            # returning something invented.
            raise
        # Through the chain, same as the hit above: the dock that won the race
        # may itself be an alias, and handing back an alias is the one thing
        # this module exists to prevent.
        return await canonical_location(session, conflicting)

    return location


# Chains are real and grow through ordinary use: applying A->B and then B->C
# leaves A->B->C, because `_apply` repoints shops but deliberately does not
# repoint other aliases. Flattening them would be cheaper to read but would
# break reversibility - a revert of B->C could no longer tell which rows it had
# collapsed. So chains stay, and this is a sanity bound rather than a promise
# that they are short: merges are rare and human-confirmed for the founding set,
# so a chain of 16 means something is writing `merged_into_id` directly.
_MAX_ALIAS_DEPTH = 16


async def canonical_location(session: AsyncSession, location: Location) -> Location:
    """Follow the alias chain to the dock that is actually the place.

    IDN-2. A merged `Location` keeps its row so its address stays a live lookup
    key; this is what turns that row into an alias rather than a dead end.

    Raises `RuntimeError` on a cycle or an implausibly long chain instead of
    looping. A cycle means two docks have been merged into each other, which no
    code path here can produce and which would otherwise hang a request - the
    loud failure is the point.
    """
    seen = {location.id}
    current = location
    for _ in range(_MAX_ALIAS_DEPTH):
        if current.merged_into_id is None:
            return current
        nxt = await session.get(Location, current.merged_into_id)
        if nxt is None:
            # The FK makes this unreachable short of manual surgery. Returning
            # the last good dock beats raising, but it is worth not pretending
            # the chain resolved cleanly.
            return current
        if nxt.id in seen:
            raise RuntimeError(
                f"alias cycle in locations: {nxt.id} is reachable from itself"
            )
        seen.add(nxt.id)
        current = nxt
    raise RuntimeError(
        f"alias chain from {location.id} exceeded {_MAX_ALIAS_DEPTH} hops"
    )
