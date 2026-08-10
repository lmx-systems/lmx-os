"""order tracking token

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-10

The capability behind the customer-facing tracking page (docs/ROADMAP.md F3,
app/tracking/service.py).

Nullable with no backfill, deliberately. Minting a token for every historical
order would create a live tracking credential for thousands of deliveries nobody
will ever look at - and `app/tracking/service.py::ensure_tracking_token` mints
lazily, so a legacy order gets one the first time it actually needs one and never
otherwise.

Unique because the public endpoint resolves an order BY this column, and indexed
because that lookup runs on every poll of a page that refreshes while a driver is
inbound. The unique constraint is also the last line of defence against a token
collision handing one recipient another's delivery.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("tracking_token", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_orders_tracking_token", "orders", ["tracking_token"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_orders_tracking_token", table_name="orders")
    op.drop_column("orders", "tracking_token")
