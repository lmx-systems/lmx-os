"""stop pod_left_at

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-19

Screen 1m's "Left at" proof-of-delivery field (app/models/stop.py) had no
column to write to - the driver app collected it but silently discarded it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stops", sa.Column("pod_left_at", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("stops", "pod_left_at")
