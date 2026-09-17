"""EXP-2: per-dock stratification, and docks a customer asked us to skip

`docs/ROADMAP_1.5.md` EXP-2: *"no single dock absorbs more than its share;
fragile accounts excludable."* Two halves, one migration.

**The stratification columns** turn `experiment_assignments` from a record of
independent draws into a record of positions in a block. EXP-1 hashed each order
alone, which gives the right fraction across a book and no guarantee about any
one dock: at an 8% arm, one dock among twenty-five orders landing in control
four times is ordinary luck, and four deliberately slower deliveries to one
customer is a phone call. Within a block of `block_size` orders to one dock,
exactly one position is control.

Nullable, and **not backfilled**. A row written before EXP-2 was decided by an
independent draw, and writing a block onto it would make the record describe a
method that was not used. `verify_assignment` checks each row the way it was
made. In practice the table is empty - no client has a contracted arm - but a
migration that assumed that would be a migration that lied on the one deployment
where it was wrong.

**The exclusions table** is deliberately not append-only, unlike its neighbours.
`experiment_assignments` and `outcome_ledger` carry triggers because they are
evidence of a decision and nothing should be able to move them. This is
configuration with a history: a customer must be able to change their mind, so
revoking sets `revoked_at` and the row stays. What must not happen is a dock
being quietly un-excluded with no trace, which is why there is no delete path.

Revision ID: 0055
Revises: 0054
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "experiment_assignments",
        sa.Column("receiver_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "experiment_assignments", sa.Column("block_size", sa.Integer(), nullable=True)
    )
    op.add_column(
        "experiment_assignments", sa.Column("block_index", sa.Integer(), nullable=True)
    )
    op.add_column(
        "experiment_assignments",
        sa.Column("position_in_block", sa.Integer(), nullable=True),
    )
    # The count that decides an order's position runs on exactly this shape, on
    # the intake path, under an advisory lock. Without the index it is a
    # sequential scan of every assignment the client has ever had, and it gets
    # slower for the busiest customers first.
    op.create_index(
        "ix_experiment_assignments_stratum",
        "experiment_assignments",
        ["client_id", "receiver_key", "experiment"],
    )
    # A position outside its own block would mean the block arithmetic was
    # wrong, and the guarantee EXP-2 makes is arithmetic.
    op.create_check_constraint(
        "ck_experiment_assignments_position",
        "experiment_assignments",
        "position_in_block IS NULL OR block_size IS NULL OR "
        "(position_in_block >= 0 AND position_in_block < block_size)",
    )

    op.create_table(
        "experiment_exclusions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receiver_key", sa.String(length=255), nullable=False),
        sa.Column("experiment", sa.String(length=32), nullable=False),
        # Required at the database, not only in Python. Somebody will ask later
        # why the measurement skipped this dock, and an empty column cannot
        # answer.
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("requested_by", sa.String(length=16), nullable=False),
        sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("length(trim(reason)) > 0", name="ck_experiment_exclusions_reason"),
        sa.CheckConstraint(
            "requested_by IN ('customer', 'lmx')",
            name="ck_experiment_exclusions_requested_by",
        ),
    )
    op.create_index(
        "ix_experiment_exclusions_client_id", "experiment_exclusions", ["client_id"]
    )
    op.create_index(
        "ix_experiment_exclusions_receiver_key", "experiment_exclusions", ["receiver_key"]
    )
    op.create_index(
        "ix_experiment_exclusions_experiment", "experiment_exclusions", ["experiment"]
    )
    op.create_index(
        "ix_experiment_exclusions_excluded_at", "experiment_exclusions", ["excluded_at"]
    )
    # One live exclusion per dock per experiment. Two would be two answers to
    # "is this dock in the arm", and revoking one would silently leave the other
    # standing - the failure mode being a customer told their dock was back in
    # the measurement when it was not.
    op.create_index(
        "uq_experiment_exclusions_live",
        "experiment_exclusions",
        ["client_id", "receiver_key", "experiment"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Drops the exclusions and the stratification.

    The exclusions do not come back, and they are the half that matters: each
    row is a customer's stated request about their own account. Losing them
    means the next assignment run puts docks back into the arm that somebody
    asked us to keep out of it, with nothing in the system that remembers why
    they were out.
    """
    op.drop_index("uq_experiment_exclusions_live", table_name="experiment_exclusions")
    for index in (
        "ix_experiment_exclusions_excluded_at",
        "ix_experiment_exclusions_experiment",
        "ix_experiment_exclusions_receiver_key",
        "ix_experiment_exclusions_client_id",
    ):
        op.drop_index(index, table_name="experiment_exclusions")
    op.drop_table("experiment_exclusions")

    op.drop_constraint(
        "ck_experiment_assignments_position", "experiment_assignments", type_="check"
    )
    op.drop_index("ix_experiment_assignments_stratum", table_name="experiment_assignments")
    for column in ("position_in_block", "block_index", "block_size", "receiver_key"):
        op.drop_column("experiment_assignments", column)
