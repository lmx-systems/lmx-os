"""The guard that lets migration 0046 keep its own copy of the normalizer.

0046 backfills one `Location` per distinct normalized shop address. It cannot
import `app.geocoding.base` to do it - a migration that depends on the shape of
the application package breaks the first time that package moves, and this one
has to keep producing the same answer years from now. So it carries a copy.

A copy that drifts is worse than an import. If `normalize_address` ever starts
collapsing something the migration's copy does not, the two disagree about which
addresses are the same dock, and the symptom is duplicate `locations` rows that
nobody notices until a per-dock number comes out wrong - which is the exact
failure IDN-1 exists to end.

So the duplication is allowed and this test is the price of it.
"""
import importlib.util
import pathlib

import pytest

from app.geocoding.base import normalize_address
from app.identity import resolution

_MIGRATION = (
    pathlib.Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "0046_locations.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("migration_0046", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_normalizer():
    return _migration_module()._normalize_address


# Real shapes from the design partner's export, plus the edge cases that decide
# whether two records are one dock. Any address added to one side of the
# comparison is automatically checked on the other.
ADDRESSES = [
    "1200 E 6th St, Austin, TX",
    "  1200   E 6th St,  Austin, TX  ",
    "1200 E 6TH ST, AUSTIN, TX",
    "1200 E 6th St, Austin, TX.",
    "1200 E 6th St, Austin, TX;",
    # The export's genuinely broken rows.
    "N/A, N/A, N/A",
    "",
    "   ",
    ",,,",
    # Abbreviation and house number - the pair the normalizer must NOT merge.
    "1200 E 6th Street, Austin, TX",
    "1202 E 6th St, Austin, TX",
    # Non-ASCII, where casefold() and lower() genuinely differ.
    "Straße 1, Köln",
    "STRASSE 1, KÖLN",
]


@pytest.mark.parametrize("address", ADDRESSES)
def test_the_migration_normalizer_matches_the_application_one(address):
    assert _migration_normalizer()(address) == normalize_address(address)


def test_the_normalizer_still_refuses_to_merge_different_places():
    """Not about the copy - about the property both copies must have.

    If this ever passes trivially because the normalizer got clever about
    abbreviations, IDN-1 has started fusing docks that differ by a house number,
    and no downstream check can detect it.
    """
    assert normalize_address("1200 E 6th St, Austin, TX") != normalize_address(
        "1200 E 6th Street, Austin, TX"
    )
    assert normalize_address("1200 E 6th St, Austin, TX") != normalize_address(
        "1202 E 6th St, Austin, TX"
    )


def test_the_unresolvable_addresses_normalize_to_empty():
    """Which is what makes them skippable in the backfill rather than one shared dock.

    `,,,` is the one worth stating: it is not empty input, but it carries no
    place, and if it normalized to `","` every such row would collapse into a
    single fictional dock.
    """
    for blank in ("", "   ", ",,,", " , . ; "):
        assert normalize_address(blank) == ""


def test_the_migration_placeholder_set_matches_the_application_one():
    """The second duplicated piece, guarded the same way.

    `N/A` normalizes to the perfectly usable key "n/a". If the migration and the
    resolver disagree about whether that names a place, the backfill creates a
    shared fictional dock that the resolver then refuses to ever use again - so
    the dock exists, accumulates nothing, and quietly misstates the dock count.
    """
    assert _migration_module()._PLACEHOLDER_KEYS == resolution._PLACEHOLDER_KEYS


@pytest.mark.parametrize(
    "address",
    ADDRESSES + ["N/A", "n/a", "N/A, N/A, N/A", "unknown", "TBD", "-", "Unknown Road"],
)
def test_the_migration_place_test_matches_the_application_one(address):
    migration = _migration_module()
    key = normalize_address(address)
    assert migration._carries_a_place(key) == resolution._carries_a_place(key)


def test_a_placeholder_address_names_no_place():
    """The bug this was written for: every `N/A` row sharing one invented dock."""
    for placeholder in ("N/A", "n/a", "N/A, N/A, N/A", "unknown", "TBD", "-", "?"):
        key = normalize_address(placeholder)
        assert key, f"{placeholder!r} normalizes to a usable key - that is the trap"
        assert not resolution._carries_a_place(key)


def test_a_real_address_containing_a_placeholder_word_still_names_a_place():
    """Whole-string only. `Unknown Road` is a street, not a non-answer."""
    for real in ("Unknown Road, Austin, TX", "1 None St", "Na Pali Coast, HI"):
        assert resolution._carries_a_place(normalize_address(real))
