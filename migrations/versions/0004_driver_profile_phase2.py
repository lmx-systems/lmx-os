"""driver app phase 2: payment method, driver_documents

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-19

Supports the driver app's profile screen (1r) - see docs/NEXT_STEPS.md.
Earnings (1n/1o) and messaging (1p/1q) are explicitly out of scope for this
pass; only vehicle edit, documents, and payment-method display are built.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("drivers", sa.Column("payment_bank_last4", sa.String(4), nullable=True))

    op.create_table(
        "driver_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("doc_type", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.Date, nullable=False),
        sa.Column("file_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_driver_documents_driver_id", "driver_documents", ["driver_id"])


def downgrade() -> None:
    op.drop_table("driver_documents")
    op.drop_column("drivers", "payment_bank_last4")
