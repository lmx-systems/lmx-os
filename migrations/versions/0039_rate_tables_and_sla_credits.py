"""rate tables and sla credits

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-10

Rate-table billing (docs/ROADMAP.md F5) and SLA-breach credits (W3) - one migration
because they are one surface: a credit is a percentage of a fee, so a richer fee and a
credit against it cannot be reasoned about separately.

**Rate components are additive, not a `basis` enum.** Courier rates are written as "$8 plus
$1.50 a mile, minimum $12", not as a choice between per-drop and per-mile. An enum would
force every hybrid contract to be approximated, and the approximation surfaces later as an
argument about an invoice. All the new components default to 0 and `rate_per_drop_cents`
becomes the base, so every existing rate prices exactly as it did.

`orders.fee_breakdown` records HOW a fee was reached. With a flat per-drop rate the
question never came up; with a rate table, "why is this line $19.40" is a question a client
will ask, and reconstructing it later from a rate card that may since have changed is not
an answer.

`client_sla_terms` is the half W3 was missing. Credits are owed against a delivery
commitment, and none existed - app/sla/engine.py defines HOLD windows (when we must set
off), not delivery times. See the model for why the target is contract data per client and
per tier rather than a constant chosen here.

Invoice gains credit columns so a statement can show what was charged, what was credited,
and the net - a single total that silently nets credits is an invoice a client cannot check.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in (
        sa.Column("rate_per_mile_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_per_piece_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "rate_per_weight_unit_cents", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("minimum_charge_cents", sa.Integer(), nullable=True),
    ):
        op.add_column("client_rates", column)

    op.add_column("orders", sa.Column("fee_breakdown", postgresql.JSONB(), nullable=True))

    op.create_table(
        "client_sla_terms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("sla_tier", sa.String(length=16), nullable=False),
        sa.Column("delivery_target_minutes", sa.Integer(), nullable=False),
        sa.Column("credit_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credit_minimum_cents", sa.Integer(), nullable=True),
        sa.Column("credit_maximum_cents", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("client_id", "sla_tier", name="uq_client_sla_terms_client_tier"),
    )
    op.create_index("ix_client_sla_terms_client_id", "client_sla_terms", ["client_id"])

    # Gross and credits alongside the net, because a statement showing only a net total is
    # one a client cannot check against their own records.
    op.add_column(
        "invoices",
        sa.Column("gross_cents", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "invoices",
        sa.Column("credit_cents", sa.Integer(), nullable=False, server_default="0"),
    )
    # Existing invoices predate credits, so their gross IS their total and nothing was
    # credited. Backfilled rather than left at zero: a historical statement reading
    # "$0 charged, $4,180 total" would be worse than the column not existing.
    op.execute("UPDATE invoices SET gross_cents = total_cents")

    op.create_table(
        "invoice_credits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("sla_tier", sa.String(length=16), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("promised_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minutes_late", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_invoice_credits_invoice_id", "invoice_credits", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_credits_invoice_id", table_name="invoice_credits")
    op.drop_table("invoice_credits")
    op.drop_column("invoices", "credit_cents")
    op.drop_column("invoices", "gross_cents")
    op.drop_index("ix_client_sla_terms_client_id", table_name="client_sla_terms")
    op.drop_table("client_sla_terms")
    op.drop_column("orders", "fee_breakdown")
    for name in (
        "minimum_charge_cents",
        "rate_per_weight_unit_cents",
        "rate_per_piece_cents",
        "rate_per_mile_cents",
    ):
        op.drop_column("client_rates", name)
