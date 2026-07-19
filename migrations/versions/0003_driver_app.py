"""driver app: profile fields, delivery fields, stop dropoff/scan/POD, route_offers, stop_orders

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-18

Supports the driver app's Phase 1 core loop (see docs/NEXT_STEPS.md item
12): real driver auth, a job-offer/accept model, and the first Route/Stop
API endpoints this codebase has ever had. See app/models/route_offer.py
and app/models/stop.py for why each of these exists.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Screen 1c, "Vehicle & profile setup".
    op.add_column("drivers", sa.Column("vehicle_type", sa.String(16), nullable=True))
    op.add_column("drivers", sa.Column("plate_number", sa.String(32), nullable=True))
    op.add_column("drivers", sa.Column("delivery_zone", sa.String(120), nullable=True))

    # Customer/drop-off side of an order - never modeled before (see
    # app/models/order.py's comment on why this was missing).
    op.add_column("orders", sa.Column("delivery_address", sa.String(255), nullable=True))
    op.add_column("orders", sa.Column("delivery_lat", sa.Numeric(9, 6), nullable=True))
    op.add_column("orders", sa.Column("delivery_lng", sa.Numeric(9, 6), nullable=True))
    op.add_column("orders", sa.Column("delivery_contact_name", sa.String(120), nullable=True))
    op.add_column("orders", sa.Column("delivery_contact_phone", sa.String(32), nullable=True))
    op.add_column("orders", sa.Column("delivery_notes", sa.String(500), nullable=True))

    # stops.shop_id becomes nullable - a dropoff stop has no shop, only a
    # customer delivery address (Order.delivery_lat/lng).
    op.alter_column("stops", "shop_id", nullable=True)
    op.add_column("stops", sa.Column("stop_type", sa.String(16), nullable=False, server_default="pickup"))
    op.add_column("stops", sa.Column("parcel_count", sa.Integer, nullable=False, server_default="1"))
    op.add_column("stops", sa.Column("scanned_count", sa.Integer, nullable=False, server_default="0"))
    op.add_column("stops", sa.Column("pod_method", sa.String(16), nullable=True))
    op.add_column("stops", sa.Column("pod_signature_url", sa.String(500), nullable=True))
    op.add_column("stops", sa.Column("pod_pin", sa.String(16), nullable=True))

    op.create_table(
        "stop_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("stop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stops.id"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_stop_orders_stop_id", "stop_orders", ["stop_id"])
    op.create_index("ix_stop_orders_order_id", "stop_orders", ["order_id"])

    op.create_table(
        "route_offers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="offered"),
        sa.Column("stop_payload", postgresql.JSONB, nullable=False),
        sa.Column("offered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("routes.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_route_offers_driver_id_status", "route_offers", ["driver_id", "status"])


def downgrade() -> None:
    op.drop_table("route_offers")
    op.drop_table("stop_orders")

    op.drop_column("stops", "pod_pin")
    op.drop_column("stops", "pod_signature_url")
    op.drop_column("stops", "pod_method")
    op.drop_column("stops", "scanned_count")
    op.drop_column("stops", "parcel_count")
    op.drop_column("stops", "stop_type")
    op.alter_column("stops", "shop_id", nullable=False)

    op.drop_column("orders", "delivery_notes")
    op.drop_column("orders", "delivery_contact_phone")
    op.drop_column("orders", "delivery_contact_name")
    op.drop_column("orders", "delivery_lng")
    op.drop_column("orders", "delivery_lat")
    op.drop_column("orders", "delivery_address")

    op.drop_column("drivers", "delivery_zone")
    op.drop_column("drivers", "plate_number")
    op.drop_column("drivers", "vehicle_type")
