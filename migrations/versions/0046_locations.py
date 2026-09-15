"""locations: one row per physical dock, with shop_profiles pointing at it

IDN-1 (docs/ROADMAP_1.5.md Phase 1), implementing §2.2(b).

A Shop is a customer account and a Location is a place, and until now they have
been the same field. The design partner's export resolves 230 customer IDs to
224 names to an unknown, smaller number of real docks - five records for one
body shop, one shop entered twice with zeros for the letter O. Every per-dock
figure is wrong until this collapses.

Three steps:

  1. Create `locations`, keyed on a normalized address that is UNIQUE. The
     constraint is the point: it makes a concurrent double-resolution a conflict
     the database refuses rather than two docks somebody later has to merge.
  2. Add `shop_profiles.location_id`, nullable, with a real FK and no cascade -
     same reasoning as 0045's `rate_version_id`. Postgres then refuses to delete
     a dock that shops still point at.
  3. Backfill one Location per distinct normalized shop address and point each
     shop at it.

`location_id` stays nullable permanently. A shop whose address normalizes to
nothing - the `N/A` row is real - genuinely has no resolvable dock, and a NOT
NULL column would force us to invent one. Null here means "not resolved", which
is a fact worth keeping, not a gap to paper over.

**The normalizer is duplicated below, on purpose.** A migration that imports
application code breaks the moment that code moves, and this one has to keep
producing the same answer years from now. The copy is guarded instead:
`test_the_migration_normalizer_matches_the_application_one` fails if the two
ever diverge, which turns a silent duplicate-dock bug into a red build.

This migration is not reversible in the strict sense - see `downgrade`.

Revision ID: 0046
Revises: 0045
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Must stay identical to app/identity/resolution.py::_PLACEHOLDER_KEYS. Same
# guard test. Without this the export's `N/A` rows all back-fill into one shared
# fictional dock, which is the failure IDN-1 exists to prevent.
_PLACEHOLDER_KEYS = frozenset(
    {"n/a", "na", "none", "null", "unknown", "tbd", "-", "--", ".", "?"}
)


def _carries_a_place(key: str) -> bool:
    """Must stay identical to app/identity/resolution.py::_carries_a_place."""
    if not key:
        return False
    if key in _PLACEHOLDER_KEYS:
        return False
    segments = [segment.strip() for segment in key.split(",")]
    return not all(segment in _PLACEHOLDER_KEYS or not segment for segment in segments)


def _normalize_address(address: str) -> str:
    """Must stay identical to app/geocoding/base.py::normalize_address.

    Copied rather than imported so this migration does not depend on the shape
    of the application package. Guarded by a test - if that function changes and
    this does not, the build fails rather than the data silently splitting into
    duplicate docks.
    """
    collapsed = " ".join(address.split())
    return collapsed.strip(" ,.;").casefold()


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("normalized_address", sa.String(length=255), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("normalized_address", name="uq_locations_normalized_address"),
    )
    # No explicit index on normalized_address: the unique constraint above is
    # backed by one already.

    op.add_column(
        "shop_profiles",
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_shop_profiles_location_id",
        "shop_profiles",
        "locations",
        ["location_id"],
        ["id"],
    )
    op.create_index("ix_shop_profiles_location_id", "shop_profiles", ["location_id"])

    _backfill()


def _backfill() -> None:
    """One Location per distinct normalized shop address; every shop pointed at one.

    Done in Python rather than SQL so the normalizer above is the single
    definition of the key. The row count is in the hundreds, so the loop is not
    worth optimizing, and being able to read it matters more.

    A shop whose address names no place - empty, or a placeholder like the
    export's `N/A` row - is skipped and left with a null `location_id`. That is
    the honest outcome; see the module docstring.
    """
    import uuid

    bind = op.get_bind()
    shops = bind.execute(
        sa.text("SELECT id, address, lat, lng FROM shop_profiles ORDER BY created_at")
    ).fetchall()

    # normalized address -> location id, so the second shop at a dock reuses the
    # first one's row instead of colliding with the unique constraint.
    seen: dict[str, uuid.UUID] = {}

    for shop in shops:
        key = _normalize_address(shop.address or "")
        if not _carries_a_place(key):
            # No dock. `location_id` stays null, which is the honest record.
            continue

        location_id = seen.get(key)
        if location_id is None:
            location_id = uuid.uuid4()
            bind.execute(
                sa.text(
                    "INSERT INTO locations "
                    "(id, normalized_address, address, lat, lng) "
                    "VALUES (:id, :key, :address, :lat, :lng)"
                ),
                {
                    "id": location_id,
                    "key": key,
                    # The first shop to claim a dock supplies its display address
                    # and coordinates. Later shops at the same dock do not
                    # overwrite them: there is no basis for preferring one
                    # spelling of the same place over another.
                    "address": shop.address,
                    "lat": shop.lat,
                    "lng": shop.lng,
                },
            )
            seen[key] = location_id

        bind.execute(
            sa.text("UPDATE shop_profiles SET location_id = :loc WHERE id = :sid"),
            {"loc": location_id, "sid": shop.id},
        )


def downgrade() -> None:
    """Drops the dock table and the pointer to it.

    Not a true inverse, and worth being plain about: any merge decision recorded
    after this migration ran - two shops deliberately pointed at one dock - is
    destroyed here, because the only record of it was the shared `location_id`.
    Re-running `upgrade` afterwards rebuilds docks from addresses alone and will
    not reproduce a single one of those judgements.
    """
    op.drop_index("ix_shop_profiles_location_id", table_name="shop_profiles")
    op.drop_constraint(
        "fk_shop_profiles_location_id", "shop_profiles", type_="foreignkey"
    )
    op.drop_column("shop_profiles", "location_id")
    op.drop_table("locations")
