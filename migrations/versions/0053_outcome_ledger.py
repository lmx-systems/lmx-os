"""outcome_ledger: what happened, attached to the decision that caused it

REC-3 (docs/ROADMAP_1.5.md Phase 1). Done when "outcomes attach by key without
touching the decision row".

That requirement is already physically enforced - 0051's trigger rejects UPDATE
on `decision_snapshots`, so there is no way to write an outcome onto a decision
even by accident. The reason it is worth stating as a done-when is that the
obvious design is the forbidden one: a `delivered_on_time` column on the
decision, filled in later. A decision is what was known at an instant. Writing
an outcome into it produces a row that is partly a record of the past and
partly a running total, and "what did we know" stops being separable from "how
did it turn out" - which is the single question a disputed savings statement
turns on.

**Append-only, like the decisions it points at.** A correction is a new entry
carrying `supersedes`, not an edit. An outcome can genuinely turn out wrong - a
delivery marked failed that completed, a dispute resolved the other way - and
the honest record of that is two rows, not one rewritten one.

No foreign keys, same reasoning as 0051 and 0052. An outcome must stay readable
if its order or its decision is ever purged.

`decision_snapshot_id` is nullable on purpose: an order delivered before REC-1
existed, or one a human dispatcher moved by hand, still produced an outcome.
Refusing to record those would bias every later measurement towards exactly the
deliveries the system handled - which is the population whose performance is
being claimed.

Revision ID: 0053
Revises: 0052
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION outcome_ledger_is_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'outcome_ledger is append-only: % on row % was rejected. '
        'Corrections are new entries carrying supersedes, not edits.',
        TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "outcome_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_type", sa.String(length=8), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=False),
        sa.Column("supersedes", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "subject_type IN ('order', 'stop')", name="ck_outcome_ledger_subject_type"
        ),
        sa.CheckConstraint(
            "kind IN ('delivered', 'failed', 'dwell', 'disputed')",
            name="ck_outcome_ledger_kind",
        ),
        # An entry cannot supersede itself. Cheap to state where it holds
        # regardless of which code path writes the row.
        sa.CheckConstraint("supersedes IS NULL OR supersedes <> id", name="ck_outcome_ledger_self"),
    )
    op.create_index("ix_outcome_ledger_hub_id", "outcome_ledger", ["hub_id"])
    # The two hot reads: everything about one subject, and everything
    # descending from one decision.
    op.create_index("ix_outcome_ledger_subject_id", "outcome_ledger", ["subject_id"])
    op.create_index(
        "ix_outcome_ledger_decision_snapshot_id", "outcome_ledger", ["decision_snapshot_id"]
    )
    op.create_index("ix_outcome_ledger_kind", "outcome_ledger", ["kind"])
    op.create_index("ix_outcome_ledger_recorded_at", "outcome_ledger", ["recorded_at"])

    op.execute(_IMMUTABLE_FN)
    op.execute(
        """
        CREATE TRIGGER outcome_ledger_no_update_or_delete
        BEFORE UPDATE OR DELETE ON outcome_ledger
        FOR EACH ROW EXECUTE FUNCTION outcome_ledger_is_append_only();
        """
    )


def downgrade() -> None:
    """Drops every recorded outcome, including the corrections.

    The delivery outcomes are partly reconstructible from `orders`, but the
    promise each was judged against is not - SLA terms change, and the whole
    reason an outcome stores the commitment it was measured against is that
    recomputing it later judges a delivery by rules that did not apply to it.
    """
    op.execute("DROP TRIGGER IF EXISTS outcome_ledger_no_update_or_delete ON outcome_ledger")
    op.execute("DROP FUNCTION IF EXISTS outcome_ledger_is_append_only()")
    for index in (
        "ix_outcome_ledger_recorded_at",
        "ix_outcome_ledger_kind",
        "ix_outcome_ledger_decision_snapshot_id",
        "ix_outcome_ledger_subject_id",
        "ix_outcome_ledger_hub_id",
    ):
        op.drop_index(index, table_name="outcome_ledger")
    op.drop_table("outcome_ledger")
