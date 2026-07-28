"""ground truth capture

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-28

Ground-truth event capture (docs/ROADMAP.md I1) - the instrumentation the
intelligence layer is data-gated on. Three real timestamps/events that
were previously proxied or not captured at all:
- orders.delivered_at: a real delivery timestamp, replacing the
  updated_at-as-delivered-at proxy billing/portal relied on. Backfilled
  from updated_at for already-delivered orders so historical invoices and
  the portal keep showing the same dates.
- stops.arrived_at: when the driver actually arrived (time-at-stop and
  ETA-accuracy ground truth).
- route_offers.decline_reason: why a driver declined an offer.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("stops", sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("route_offers", sa.Column("decline_reason", sa.String(64), nullable=True))

    # Backfill delivered_at for orders already delivered, from the proxy
    # those rows were measured by, so switching billing/portal to the real
    # column doesn't blank out historical delivery dates.
    op.execute("UPDATE orders SET delivered_at = updated_at WHERE status = 'delivered'")


def downgrade() -> None:
    op.drop_column("route_offers", "decline_reason")
    op.drop_column("stops", "arrived_at")
    op.drop_column("orders", "delivered_at")
