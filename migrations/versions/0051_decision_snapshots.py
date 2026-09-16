"""decision_snapshots: what the dispatcher knew when it decided, append-only

REC-1 (docs/ROADMAP_1.5.md Phase 1). `shadow_decisions` records what a cycle
decided; this records what it saw. Only the second makes a decision
reproducible, and Phase 2's whole purpose is a number somebody can defend.

**The inputs are values, not foreign keys, and that is the design.** REC-1's
done-when is "a decision replays to reproduce its inputs exactly; no field may
reference data created after the decision". An order_id pointing at `orders`
would read back today's tier and today's deadline rather than the ones the
optimizer had - and it would do that silently, producing a confident
reconstruction of a decision that never happened. So the released orders, the
driver positions and the shop names are copied into `inputs` at decision time
and nothing in a replay resolves a reference.

`hub_id` is a plain UUID, not a foreign key, and that is the same principle
rather than an exception to it. A decision log must be able to record a
decision about a hub that is later removed - an FK would either block that
removal or cascade away the evidence, and evidence that disappears when the
subject does is not evidence. It also turned out to be load-bearing in a way
worth recording: `run_cycle` is legitimately called for a hub with no row,
which an FK made fatal.

**Immutability is a trigger, not a convention.** An audit log the application
can quietly rewrite is not an audit log, and the moment somebody would want to
rewrite one is the moment it matters most. UPDATE and DELETE are rejected at
the database. That is deliberately inconvenient: correcting a decision record
should require a migration somebody has to write and review, not an UPDATE
somebody can run.

Retention is not set here and is a real open question - these rows are the
evidence behind a savings statement, so they must outlast the dispute window
(the same interaction `docs/ROADMAP.md` R3 records between proof retention and
the claim window). Nothing prunes them today.

Revision ID: 0051
Revises: 0050
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Rejects any attempt to change or remove a decision. RAISE EXCEPTION rather
# than RETURN NULL: silently discarding an UPDATE would leave the caller
# believing it had worked, which is the failure this is guarding against.
_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION decision_snapshots_are_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'decision_snapshots is append-only: % on row % was rejected. '
        'A decision record is evidence; correcting one needs a migration, not an UPDATE.',
        TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "decision_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("engine", sa.String(length=48), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("assignments", postgresql.JSONB(), nullable=False),
        sa.Column("unassigned_stop_ids", postgresql.JSONB(), nullable=False),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_duration_seconds", sa.Float(), nullable=False),
        sa.Column("hub_closed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stop_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("driver_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assigned_count", sa.Integer(), nullable=False, server_default="0"),
        # No created_at/updated_at. `decided_at` is the only time that means
        # anything here, and an `updated_at` on a row nothing may update is a
        # misleading field to leave lying about.
        sa.CheckConstraint("mode IN ('live', 'shadow')", name="ck_decision_snapshots_mode"),
    )
    op.create_index("ix_decision_snapshots_hub_id", "decision_snapshots", ["hub_id"])
    op.create_index("ix_decision_snapshots_decided_at", "decision_snapshots", ["decided_at"])
    op.create_index("ix_decision_snapshots_mode", "decision_snapshots", ["mode"])
    # Answering "did these two cycles see the same world" without diffing JSON.
    op.create_index("ix_decision_snapshots_inputs_hash", "decision_snapshots", ["inputs_hash"])

    op.execute(_IMMUTABLE_FN)
    op.execute(
        """
        CREATE TRIGGER decision_snapshots_no_update_or_delete
        BEFORE UPDATE OR DELETE ON decision_snapshots
        FOR EACH ROW EXECUTE FUNCTION decision_snapshots_are_immutable();
        """
    )


def downgrade() -> None:
    """Drops the decision log.

    Worth stating plainly: these rows are the only record of what the
    optimizer knew at each decision, and nothing regenerates them. A dispute
    about a delivery made before this ran would have no evidence behind it
    afterwards.
    """
    op.execute("DROP TRIGGER IF EXISTS decision_snapshots_no_update_or_delete ON decision_snapshots")
    op.execute("DROP FUNCTION IF EXISTS decision_snapshots_are_immutable()")
    op.drop_index("ix_decision_snapshots_inputs_hash", table_name="decision_snapshots")
    op.drop_index("ix_decision_snapshots_mode", table_name="decision_snapshots")
    op.drop_index("ix_decision_snapshots_decided_at", table_name="decision_snapshots")
    op.drop_index("ix_decision_snapshots_hub_id", table_name="decision_snapshots")
    op.drop_table("decision_snapshots")
