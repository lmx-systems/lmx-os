"""locations gain an alias pointer; location_merges is the queue and the audit log

IDN-2 (docs/ROADMAP_1.5.md Phase 1), implementing §2.2(c).

Two changes:

  1. `locations.merged_into_id`, a nullable self-referencing FK. A merged dock
     keeps its row, and that row is the alias: its `normalized_address` stays a
     live lookup key, so the next shop spelled that way lands on the canonical
     dock instead of recreating the duplicate. Deleting the absorbed row instead
     would undo the merge on the next import, which is how de-duplication
     efforts usually fail.
  2. `location_merges`, holding proposals and their outcomes in one table.
     A confirmed proposal *is* the audit record of the merge it authorised -
     splitting queue from log would create two tables that have to agree.

No backfill. Every merge is a judgement, and §2.2(c) puts the founding set in
front of a person deliberately; inventing merges here would be exactly the
silent bad merge the review step exists to prevent.

`moved_shop_ids` is what makes a revert exact rather than approximate - it
distinguishes shops this merge moved from shops that already pointed at the
target. Reversibility is a requirement of §2.2(c), not a nicety.

Revision ID: 0047
Revises: 0046
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column("merged_into_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_locations_merged_into_id", "locations", "locations", ["merged_into_id"], ["id"]
    )
    op.create_index("ix_locations_merged_into_id", "locations", ["merged_into_id"])

    op.create_table(
        "location_merges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "source_location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id"),
            nullable=False,
        ),
        sa.Column(
            "target_location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("decision_source", sa.String(length=16), nullable=True),
        sa.Column(
            "decided_by_ops_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ops_users.id"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moved_shop_ids", postgresql.JSONB(), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
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
        # A dock cannot be merged into itself. Cheap to state here, and the
        # database is the only place that holds regardless of which code path
        # writes the row.
        sa.CheckConstraint(
            "source_location_id <> target_location_id",
            name="ck_location_merges_not_self",
        ),
    )
    op.create_index(
        "ix_location_merges_source_location_id", "location_merges", ["source_location_id"]
    )
    op.create_index(
        "ix_location_merges_target_location_id", "location_merges", ["target_location_id"]
    )
    # The review queue is the hot read: "everything still proposed".
    op.create_index("ix_location_merges_status", "location_merges", ["status"])


def downgrade() -> None:
    """Destroys every merge decision ever recorded.

    Worth being plain about: this drops the audit log §2.2(c) asks for, and
    clears the alias pointers, so both the merges and the record that anyone
    made them are gone. Re-running `upgrade` gives back empty tables - a person
    would have to review the founding set again from scratch.
    """
    op.drop_index("ix_location_merges_status", table_name="location_merges")
    op.drop_index(
        "ix_location_merges_target_location_id", table_name="location_merges"
    )
    op.drop_index(
        "ix_location_merges_source_location_id", table_name="location_merges"
    )
    op.drop_table("location_merges")
    op.drop_index("ix_locations_merged_into_id", table_name="locations")
    op.drop_constraint("fk_locations_merged_into_id", "locations", type_="foreignkey")
    op.drop_column("locations", "merged_into_id")
