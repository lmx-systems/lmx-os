"""ops users - per-user dashboard auth (roadmap item S1)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-20

Real per-user identity + roles for the orchestrator dashboard, replacing
sole reliance on the shared X-API-Key stopgap. See app/models/ops_user.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ops_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="operator"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=True),
    )
    op.create_index("ix_ops_users_email", "ops_users", ["email"])


def downgrade() -> None:
    op.drop_index("ix_ops_users_email", table_name="ops_users")
    op.drop_table("ops_users")
