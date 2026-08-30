"""shadow_decisions - what LMX OS would have done, while somebody else dispatched

docs/ROADMAP.md W9, session decision D3. Every initial customer engagement runs live on
the Elite EXTRA scaffold while LMX OS decides in parallel on the same orders; these are
the recorded parallel decisions the cutover scorecard is computed from.

Two tables, and the second is the point. D3 is explicit that aggregate metrics look fine
while the two systems agree - the divergent orders are the entire point - so
`shadow_order_decisions` carries one row per order per cycle. A cycle-level summary
alone would report an agreement percentage and leave nobody able to ask which orders.

`shadow_order_decisions.order_id` is deliberately NOT a foreign key. A shadow decision
is evidence in a cutover argument and has to survive its order being deleted under a
retention policy (R3); a FK with a cascade would delete the evidence, and one without
would block the retention sweep. The cascade that does exist is from the parent cycle
row, where deleting the cycle genuinely should take its per-order rows with it.

Revision ID: 0044
Revises: 0043
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shadow_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hub_closed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("engine", sa.String(48), nullable=False),
        sa.Column("plan_duration_seconds", sa.Float(), nullable=False),
        sa.Column("held_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("released_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("driver_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assigned_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unassigned_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "assignment_payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_shadow_decisions_hub_id", "shadow_decisions", ["hub_id"])
    op.create_index("ix_shadow_decisions_planned_at", "shadow_decisions", ["planned_at"])

    op.create_table(
        "shadow_order_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "shadow_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("shadow_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # No FK - see the module docstring. The evidence outlives the order.
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sequence_index", sa.Integer(), nullable=True),
        sa.Column("sla_tier", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # 'assigned' and 'unassigned' are the only two decisions a cycle can record.
        # Enforced here as well as in the service, because a third value would make
        # every divergence query silently incomplete rather than loudly wrong.
        sa.CheckConstraint(
            "decision IN ('assigned', 'unassigned')",
            name="ck_shadow_order_decision_value",
        ),
    )
    op.create_index(
        "ix_shadow_order_decisions_shadow_decision_id",
        "shadow_order_decisions",
        ["shadow_decision_id"],
    )
    op.create_index("ix_shadow_order_decisions_order_id", "shadow_order_decisions", ["order_id"])
    op.create_index("ix_shadow_order_decisions_hub_id", "shadow_order_decisions", ["hub_id"])
    op.create_index(
        "ix_shadow_order_decisions_planned_at", "shadow_order_decisions", ["planned_at"]
    )


def downgrade() -> None:
    op.drop_table("shadow_order_decisions")
    op.drop_table("shadow_decisions")
