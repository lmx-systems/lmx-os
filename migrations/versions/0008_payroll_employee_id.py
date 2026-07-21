"""driver payroll employee link (Rippling, roadmap items B4/A9)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-20

Payroll provider decided: Rippling. Drivers are hired/onboarded in
Rippling itself (W-4, I-9, bank details never touch LMX); this column
links a Driver row to its Rippling employee id so the pay-period export
(app/payroll/) knows who to submit inputs for.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("drivers", sa.Column("payroll_employee_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("drivers", "payroll_employee_id")
