"""drivers.is_active - a way to switch a driver off

A driver who left kept working sessions: the token renews itself on every app
open (`/driver/auth/refresh`), and the only revocation was per device, held in
Redis. Ops users and client users already had `is_active`; drivers did not.

Every existing driver is active. `deactivated_at` records when ops switched one
off, for the console and for anyone asking why a sign-in was refused.

Revision ID: 0070
Revises: 0069
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0070"
down_revision: Union[str, None] = "0069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "drivers", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column("drivers", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("drivers", "deactivated_at")
    op.drop_column("drivers", "is_active")
