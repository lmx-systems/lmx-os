"""invoices

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-22

Billing statement generation (docs/ROADMAP.md C3, app/billing/service.py):
a new invoices table plus orders.invoice_id linking each billed order to
the statement it was swept into. invoice_number is backed by its own
sequence, independent of the UUID primary key, so it reads like a real
human-facing invoice number - started at 1001, not 1, so an early invoice
doesn't look like a placeholder/test value.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE invoice_number_seq START WITH 1001")

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column(
            "invoice_number",
            sa.Integer(),
            server_default=sa.text("nextval('invoice_number_seq')"),
            nullable=False,
            unique=True,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
    )
    op.execute("ALTER SEQUENCE invoice_number_seq OWNED BY invoices.invoice_number")

    op.add_column(
        "orders", sa.Column("invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=True)
    )
    op.create_index("ix_orders_invoice_id", "orders", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_invoice_id", table_name="orders")
    op.drop_column("orders", "invoice_id")
    op.drop_table("invoices")
    op.execute("DROP SEQUENCE IF EXISTS invoice_number_seq")
