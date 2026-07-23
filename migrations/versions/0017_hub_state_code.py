"""hub state code

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-23

Real, neutral fact state-specific overtime rules need (docs/ROADMAP.md
A9, app/payroll/overtime_rules.py) - which US state a hub is physically
in. Nullable and unpopulated for existing hubs on purpose: no Hub
create/edit API or UI exists yet (hubs are seed/DB-provisioned only), and
an unset value keeps every driver on today's federal-only overtime rule,
unchanged.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hubs", sa.Column("state_code", sa.String(2), nullable=True))


def downgrade() -> None:
    op.drop_column("hubs", "state_code")
