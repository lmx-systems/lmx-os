"""client users

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-25

Multi-user client accounts (docs/ROADMAP.md C4). Splits the single inline
portal login off app/models/client.py (portal_email/portal_password_hash)
into a real client_users table - many named users per client, each with
their own role (admin|member). Backfills every existing client's inline
login into an admin client_users row so no one loses access, then drops
the two now-redundant columns from clients.

Same password+JWT shape as ops_users (migration 0011), scoped to a
client_id instead of fleet-wide.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "client_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="admin"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_client_users_client_id", "client_users", ["client_id"])

    # Backfill: every client that already had a portal login becomes an
    # admin client_users row, so existing portal logins keep working
    # unchanged after this migration. Postgres 16 (docker-compose.yml) has
    # gen_random_uuid() built in - no pgcrypto extension needed. The user's
    # display name is seeded from the company name (there was no per-user
    # name before this existed); an admin can rename it afterward.
    op.execute(
        """
        INSERT INTO client_users (id, client_id, email, password_hash, name, role, is_active)
        SELECT gen_random_uuid(), id, portal_email, portal_password_hash, name, 'admin', true
        FROM clients
        WHERE portal_email IS NOT NULL AND portal_password_hash IS NOT NULL
        """
    )

    op.drop_column("clients", "portal_email")
    op.drop_column("clients", "portal_password_hash")


def downgrade() -> None:
    op.add_column("clients", sa.Column("portal_password_hash", sa.String(255), nullable=True))
    op.add_column("clients", sa.Column("portal_email", sa.String(255), nullable=True))
    # Reuse the exact constraint name migration 0007 created (and its own
    # downgrade drops), so the full downgrade chain past this point still
    # finds it under the name it expects.
    op.create_unique_constraint("uq_clients_portal_email", "clients", ["portal_email"])

    # Best-effort reverse backfill: restore one login per client from its
    # oldest admin user (the closest match to the single inline login this
    # table replaced). Multi-user data beyond that one row is genuinely
    # lost on downgrade - it has nowhere to live in the old single-column
    # shape, which is the whole reason the table exists.
    op.execute(
        """
        UPDATE clients c
        SET portal_email = cu.email, portal_password_hash = cu.password_hash
        FROM (
            SELECT DISTINCT ON (client_id) client_id, email, password_hash
            FROM client_users
            WHERE role = 'admin' AND is_active = true
            ORDER BY client_id, created_at ASC
        ) cu
        WHERE cu.client_id = c.id
        """
    )

    op.drop_index("ix_client_users_client_id", table_name="client_users")
    op.drop_table("client_users")
