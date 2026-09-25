"""DRV-7: a staging table for surveys from drivers who are not ours.

`ROADMAP_1.5.md`'s `DRV-7` row states the constraint this table exists to
satisfy: **a stranger's submission must never write to `receiver_profiles`
directly.** That table is the layer `M5` trains on. An unauthenticated public
form writing into it means anybody on the internet can label our training data,
and the damage would not surface as an error — it would surface months later as
a model that learned a dock is unreachable because somebody said so.

So submissions land here, unmatched and unreviewed, and reach a profile only
through an import that a person runs. Three columns carry that decision:
`location_id` (null until something matches it to a real dock),
`reviewed_at`/`imported_at` (null until somebody acts), and `rejected_reason`
(why it will never be imported, kept rather than deleted — a pattern of junk
from one source is evidence, and a deleted row is not).

**The answers are the same vocabulary as `DRV-7`'s, not a parallel one.** The
row already says why: *"its answer set moves to these vocabularies rather than
being translated afterwards"*. A translation layer between a public form and
`receiver_profiles` would be a second place the vocabulary lives, and the two
would drift the first time a value was added to one.

**Coordinates are the only identity a stranger can give us.** They have no
`Location`, no account, and no reason to know our address formatting. `lat`/`lng`
come from the browser, so they are nullable and often wrong; `submitted_address`
and `business_name` are free text kept for the human doing the matching, never
parsed.

Revision ID: 0066
Revises: 0065
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066"
down_revision: Union[str, None] = "0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dock_log_submissions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        # What the submitter said this dock is. Free text, never parsed - it is
        # for the person deciding whether this matches a Location we hold.
        sa.Column("business_name", sa.String(length=200), nullable=True),
        sa.Column("submitted_address", sa.String(length=300), nullable=True),
        # From the browser. Nullable because consent can be refused and the
        # survey is still worth having with an address alone.
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        # The eight answers, same vocabularies and widths as receiver_profiles.
        sa.Column("stop_point", sa.String(length=16), nullable=True),
        sa.Column("curb_access", sa.String(length=16), nullable=True),
        sa.Column("walk_distance_band", sa.String(length=16), nullable=True),
        sa.Column("door_path", sa.String(length=16), nullable=True),
        sa.Column("obstruction", sa.String(length=16), nullable=True),
        sa.Column("who_receives", sa.String(length=16), nullable=True),
        sa.Column("landing_surface", sa.String(length=16), nullable=True),
        sa.Column("appointment_required", sa.Boolean(), nullable=True),
        # Triage. Null location_id means nothing has matched this to a dock yet.
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_ops_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        # Kept, not deleted. A pattern of junk from one source is evidence.
        sa.Column("rejected_reason", sa.String(length=200), nullable=True),
        # Not for display and not joinable to a person - it is the only thing
        # that makes a flood attributable after the fact, and the rate limiter
        # keys on the same value.
        sa.Column("submitted_from_ip", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], name="fk_dock_log_submissions_location"),
        sa.ForeignKeyConstraint(
            ["reviewed_by_ops_user_id"], ["ops_users.id"], name="fk_dock_log_submissions_reviewer"
        ),
    )
    # The review queue's only query: what has nobody looked at yet, oldest
    # first. Partial, because reviewed rows are the ones that accumulate and
    # indexing them would grow an index nothing reads.
    op.create_index(
        "ix_dock_log_submissions_unreviewed",
        "dock_log_submissions",
        ["created_at"],
        postgresql_where=sa.text("reviewed_at IS NULL"),
    )
    op.create_index("ix_dock_log_submissions_location", "dock_log_submissions", ["location_id"])

    # Where a profile's access answers came from. `surveyed_at` cannot carry
    # this: a public submission deliberately leaves it null so `is_surveyed`
    # stays false and our own drivers are still asked. Without this column a
    # stranger's answers would sit in the same fields as a driver's observation
    # with nothing distinguishing them, and `M5` would train on both as though
    # somebody had been to the door.
    op.add_column(
        "receiver_profiles",
        sa.Column("access_source", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("receiver_profiles", "access_source")
    op.drop_index("ix_dock_log_submissions_location", table_name="dock_log_submissions")
    op.drop_index("ix_dock_log_submissions_unreviewed", table_name="dock_log_submissions")
    op.drop_table("dock_log_submissions")
