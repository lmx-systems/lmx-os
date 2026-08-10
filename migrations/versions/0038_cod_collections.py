"""cod collections

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-10

COD collection and disputes (docs/ROADMAP.md W2, story DO-8).

`orders.cod_amount_cents` is what the driver must collect at the door **on the
distributor's behalf** - a different number from `fee_cents` (what LMX bills the client)
and `quoted_amount_cents` (what the client was quoted). Keeping it separate is not
tidiness: it is the money that isn't ours, and conflating it with either of the others
would make a dispute look like a billing question.

`PayerType` gains `cash_on_delivery`. Until now `COD_DISPUTE` existed as a stop failure
reason for a payment mode the order object could not express, so a driver could flag a
COD dispute on an order that was never COD. Stored as a plain String(24) already, so the
new value needs no type change - only the code that validates it.

Nullable with no backfill: no order in the system has ever been COD.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("cod_amount_cents", sa.Integer(), nullable=True))

    op.create_table(
        "cod_collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("stop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stops.id"), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=True),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shop_profiles.id"), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("amount_due_cents", sa.Integer(), nullable=False),
        sa.Column("amount_collected_cents", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(length=16), nullable=True),
        sa.Column("dispute_note", sa.String(length=500), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_cod_collections_order_id", "cod_collections", ["order_id"])
    op.create_index("ix_cod_collections_outcome", "cod_collections", ["outcome"])
    # The repeat-dispute report groups by account, so it gets its own index rather than
    # scanning every collection ever taken.
    op.create_index("ix_cod_collections_client_id", "cod_collections", ["client_id"])
    op.create_index("ix_cod_collections_shop_id", "cod_collections", ["shop_id"])


def downgrade() -> None:
    op.drop_index("ix_cod_collections_shop_id", table_name="cod_collections")
    op.drop_index("ix_cod_collections_client_id", table_name="cod_collections")
    op.drop_index("ix_cod_collections_outcome", table_name="cod_collections")
    op.drop_index("ix_cod_collections_order_id", table_name="cod_collections")
    op.drop_table("cod_collections")
    op.drop_column("orders", "cod_amount_cents")
