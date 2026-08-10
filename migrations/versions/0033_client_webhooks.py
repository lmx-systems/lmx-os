"""client webhooks

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-10

Outbound status webhooks (docs/ROADMAP.md F4, docs/LMX_LINK_PLAN.md §1.4 / T5) -
the first status sink that reaches outside this system.

Two tables, because they answer different questions: an endpoint is a client's
standing subscription, a delivery is one notification we owe. See
app/models/client_webhook.py for why the owed notification has to be a row rather
than an HTTP attempt.

`webhook_deliveries.sequence` is a BIGSERIAL rather than an ordinary integer
because it is the consumer's ordering key. Retries mean arrival order is not event
order - a `picked_up` that failed twice can land after the `delivered` that
followed it - and timestamps alone cannot break the tie when two transitions on one
order share a millisecond.

The unique constraint on (endpoint_id, event_id) is what makes enqueueing safe to
repeat: a replayed driver action cannot produce two POSTs of the same event to the
same consumer.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "client_webhook_endpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("secret", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_client_webhook_endpoints_client_id", "client_webhook_endpoints", ["client_id"]
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "endpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("client_webhook_endpoints.id"),
            nullable=False,
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        # BIGSERIAL: the consumer's ordering key, assigned by Postgres on insert.
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_delivery_event"),
    )
    op.create_index("ix_webhook_deliveries_endpoint_id", "webhook_deliveries", ["endpoint_id"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])
    # The sweep's only query is "pending and due", so it gets the composite rather
    # than two single-column indexes it would have to intersect.
    op.create_index(
        "ix_webhook_deliveries_due", "webhook_deliveries", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_due", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_endpoint_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_client_webhook_endpoints_client_id", table_name="client_webhook_endpoints")
    op.drop_table("client_webhook_endpoints")
