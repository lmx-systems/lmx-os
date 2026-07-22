"""driver push tokens

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-22

Push notifications for new job offers (docs/ROADMAP.md A1) - adds an
Expo push token per driver_devices row, registered by the driver app once
signed in (POST /driver/me/push-token).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("driver_devices", sa.Column("expo_push_token", sa.String(255), nullable=True))
    op.add_column(
        "driver_devices", sa.Column("push_token_registered_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("driver_devices", "push_token_registered_at")
    op.drop_column("driver_devices", "expo_push_token")
