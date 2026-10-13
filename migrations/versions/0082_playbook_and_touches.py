"""ops_touches and playbook_decisions: counting people, and the rules replacing them

`docs/ROADMAP_1.5.md` DEC-7, DEC-8 and DEC-9 (decision D-SEAT, 13 October 2026):
the end state is no one running the day, with an on-call person only for what
really needs one. Two tables make that measurable.

**`ops_touches`, one row per write a person made from the console.** Recorded
by a dependency on the ops routers (`app/playbook/touches.py`) after the write
succeeded, so a refusal is not a touch. Its category says what kind of work it
was - running the day, fixing the record, setting policy, or running the
business - and "person touches per 100 orders" is read from it. Append-only and
without foreign keys, for the reason `0078` gives: a record of who did something
must stay readable if the order or the account is purged.

**`playbook_decisions`, one row per call a rule made about an order.** What the
rule chose and why, the mode it was in (recommend, act after a veto window, or
act), when it will act, and how it ended: the rule acted, a person did the same
or something else, it escalated to a person, or the order moved on without
either. A person who chose differently is a labelled example of the rule being
wrong, which is what replaces the override as a label source once overrides
become rare. One per rule, order and attempt.

Revision ID: 0082
Revises: 0081
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082"
down_revision: Union[str, None] = "0081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION ops_touches_are_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'ops_touches is append-only: % on row % was rejected. '
        'A person having acted is a record of a moment, not a setting.',
        TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "ops_touches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("touched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ops_user_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=96), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path_params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('day', 'record', 'policy', 'business', 'unclassified')",
            name="ck_ops_touches_category",
        ),
    )
    op.create_index("ix_ops_touches_touched_at", "ops_touches", ["touched_at"])
    op.create_index("ix_ops_touches_hub_id", "ops_touches", ["hub_id"])
    op.execute(_IMMUTABLE_FN)
    op.execute(
        """
        CREATE TRIGGER ops_touches_no_update_or_delete
        BEFORE UPDATE OR DELETE ON ops_touches
        FOR EACH ROW EXECUTE FUNCTION ops_touches_are_immutable();
        """
    )

    op.create_table(
        "playbook_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rule", sa.String(length=48), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("human_action", sa.String(length=32), nullable=True),
        sa.Column("ops_user_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint("mode IN ('recommend', 'veto', 'act')", name="ck_playbook_decisions_mode"),
        sa.CheckConstraint(
            "outcome IN ('open', 'acted', 'agreed', 'overruled', 'escalated', 'superseded')",
            name="ck_playbook_decisions_outcome",
        ),
        sa.UniqueConstraint("rule", "order_id", "attempt", name="uq_playbook_decisions_rule_order_attempt"),
    )
    op.create_index("ix_playbook_decisions_hub_id", "playbook_decisions", ["hub_id"])
    op.create_index("ix_playbook_decisions_order_id", "playbook_decisions", ["order_id"])
    op.create_index(
        "ix_playbook_decisions_open",
        "playbook_decisions",
        ["hub_id", "due_at"],
        postgresql_where=sa.text("outcome = 'open'"),
    )


def downgrade() -> None:
    """Drops the count of what people did and every call the rules made.

    The orders keep what was done to them; who did it, and whether a rule had
    called it first, is gone.
    """
    op.drop_index("ix_playbook_decisions_open", table_name="playbook_decisions")
    op.drop_index("ix_playbook_decisions_order_id", table_name="playbook_decisions")
    op.drop_index("ix_playbook_decisions_hub_id", table_name="playbook_decisions")
    op.drop_table("playbook_decisions")
    op.execute("DROP TRIGGER IF EXISTS ops_touches_no_update_or_delete ON ops_touches")
    op.execute("DROP FUNCTION IF EXISTS ops_touches_are_immutable()")
    op.drop_index("ix_ops_touches_hub_id", table_name="ops_touches")
    op.drop_index("ix_ops_touches_touched_at", table_name="ops_touches")
    op.drop_table("ops_touches")
