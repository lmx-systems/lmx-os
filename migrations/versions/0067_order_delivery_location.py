"""The dock an order is delivered to.

A drop-off had no dock of its own: `Stop.shop_id` is the pickup's and is null on
a drop-off, and the identity layer reached a drop-off's dock through the order's
shop - which is the pickup. So the dock survey, taken at the delivery door, was
filed under the distributor's yard, and the receiving door M5 and M1 are about
had no row to be about.

This column is the delivery address resolved through IDN-1 at intake, the same
way a shop's is. Null when the address names no place (IDN-1 leaves those
unresolved rather than pooling them) and for every order that predates it.

Revision ID: 0067
Revises: 0066
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: Union[str, None] = "0066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("delivery_location_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_orders_delivery_location",
        "orders",
        "locations",
        ["delivery_location_id"],
        ["id"],
    )
    op.create_index("ix_orders_delivery_location_id", "orders", ["delivery_location_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_delivery_location_id", table_name="orders")
    op.drop_constraint("fk_orders_delivery_location", "orders", type_="foreignkey")
    op.drop_column("orders", "delivery_location_id")
