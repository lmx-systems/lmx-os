"""What intake noticed about an order and did not refuse

`docs/ROADMAP_1.5.md` §2.5, decisions D-UNK and D-DUP (12 Oct 2026).
`orders.intake_flags` is a JSON list of `{kind, ...}`: fields a source's adapter
did not know (`unknown_fields`) or had to default (`defaulted_fields`), and a
likely duplicate of an order the same client sent minutes before
(`possible_duplicate`). An empty list is "nothing noticed", so existing orders
get one.

Revision ID: 0075
Revises: 0074
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0075"
down_revision: Union[str, None] = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("intake_flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("orders", "intake_flags")
