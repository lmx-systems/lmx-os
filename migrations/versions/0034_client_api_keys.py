"""client api keys

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-10

Credentials a client's own system authenticates with to submit orders
(docs/ROADMAP.md F4 / LMX Link T5, docs/ORDER_API.md).

Closes a real hole rather than adding a convenience: the existing
`/ingestion/{hub}/{client}/{source}` endpoint calls itself the webhook target for a
client's POS, but sits behind the ops-user middleware - so wiring a POS to it meant
handing that POS an LMX ops login. See app/models/client_api_key.py for why the key
is hashed here while the outbound webhook secret is not.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "client_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_client_api_keys_client_id", "client_api_keys", ["client_id"])
    # Unique because every inbound order hashes the presented key and looks it up
    # here - the hot path, and a collision would authenticate one client as another.
    op.create_index(
        "ix_client_api_keys_token_hash", "client_api_keys", ["token_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_client_api_keys_token_hash", table_name="client_api_keys")
    op.drop_index("ix_client_api_keys_client_id", table_name="client_api_keys")
    op.drop_table("client_api_keys")
