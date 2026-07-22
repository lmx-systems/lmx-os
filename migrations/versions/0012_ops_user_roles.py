"""ops user roles

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-22

Real role-based access for the ops dashboard (docs/ROADMAP.md S1's
"no roles yet" gap) - admin (everything) vs viewer (read-only). Defaults
existing/new rows to admin, since every ops user created before this
column existed was already effectively unrestricted.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ops_users",
        sa.Column("role", sa.String(16), nullable=False, server_default="admin"),
    )


def downgrade() -> None:
    op.drop_column("ops_users", "role")
