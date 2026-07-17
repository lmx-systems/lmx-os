"""initial schema: hubs, clients, shops, drivers, orders, routes, stops, rules

Revision ID: 0001
Revises:
Create Date: 2026-07-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hubs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="America/Los_Angeles"),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("pos_system", sa.String(32), nullable=False, server_default="flat_file"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "shop_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("external_ref", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_shop_profiles_external_ref", "shop_profiles", ["external_ref"])

    op.create_table(
        "drivers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("vehicle_capacity_units", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="off_shift"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="planned"),
        sa.Column("plan_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    sla_tier_enum = postgresql.ENUM("T1", "T2", "T3", name="sla_tier")
    order_status_enum = postgresql.ENUM(
        "received", "classified", "held", "queued", "assigned", "delivered", "cancelled",
        name="order_status",
    )

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shop_profiles.id"), nullable=False),
        sa.Column("external_order_ref", sa.String(120), nullable=False),
        sa.Column("source_system", sa.String(32), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("sla_tier", sla_tier_enum, nullable=True),
        sa.Column("hold_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("weight_units", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("status", order_status_enum, nullable=False, server_default="received"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_orders_external_order_ref", "orders", ["external_order_ref"])
    op.create_index("ix_orders_hub_status", "orders", ["hub_id", "status"])

    op.create_table(
        "stops",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("routes.id"), nullable=False),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shop_profiles.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pod_photo_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "stop_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("stop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stops.id"), nullable=False),
        sa.Column("flag_type", sa.String(64), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_by_driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "proposed_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("rule_type", sa.String(64), nullable=False),
        sa.Column("scope", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("proposed_change", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("supporting_annotation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_review"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "active_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("rule_type", sa.String(64), nullable=False),
        sa.Column("scope", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("value", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "promoted_from_proposed_rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proposed_rules.id"),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("active_rules")
    op.drop_table("proposed_rules")
    op.drop_table("stop_flags")
    op.drop_table("stops")
    op.drop_table("orders")
    op.execute("DROP TYPE IF EXISTS order_status")
    op.execute("DROP TYPE IF EXISTS sla_tier")
    op.drop_table("routes")
    op.drop_table("drivers")
    op.drop_table("shop_profiles")
    op.drop_table("clients")
    op.drop_table("hubs")
