"""playbook_decisions.target: whom a call names (`DEC-10`)

The rule for orders nobody has (`app/playbook/rules.py`, `decide_unplaced`)
recommends offering the order to a particular driver, and whether a person
agreed depends on whether they chose the same one. `target` is that driver's
id, and the driver a person chose goes in `human_target` beside `human_action`.
Null for a call that names nobody - a failed delivery's, or an escalation.

Revision ID: 0083
Revises: 0082
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0083"
down_revision: Union[str, None] = "0082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("playbook_decisions", sa.Column("target", sa.String(length=64), nullable=True))
    op.add_column("playbook_decisions", sa.Column("human_target", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drops whom each call named and whom a person chose instead."""
    op.drop_column("playbook_decisions", "human_target")
    op.drop_column("playbook_decisions", "target")
