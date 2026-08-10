"""source ref uniqueness is per client

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-10

**Fixes a latent bug in 0028's `uq_orders_source_ref`.** That index is unique on
`(source_system, source_order_ref)` with no client scope - but a source order
reference is a CLIENT'S internal numbering, and internal numbering collides across
companies constantly. Two distributors both running an order numbered `INVOICE-1001`
means the second one's order is rejected by the database, with a 500 and no
explanation, because the first got there first.

It was theoretical while every adapter was a per-tenant connector LMX configured
(one Epicor tenant, one client, long refs). The public order API
(docs/ORDER_API.md, LMX Link T5) makes it certain: every client submitting through
it shares `source_system = 'client_api'`, so the only thing keeping two clients apart
was their choice of reference format. Found by a test that had two clients submit the
same reference on purpose.

Scoping by client is what 0028 meant - "a source can't create the same order twice",
where the source is a client's system. Strictly *less* restrictive than the old
index, so no existing row can violate it and no data migration is needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_orders_source_ref", table_name="orders")
    op.create_index(
        "uq_orders_source_ref",
        "orders",
        ["client_id", "source_system", "source_order_ref"],
        unique=True,
        postgresql_where=sa.text("source_order_ref IS NOT NULL"),
    )


def downgrade() -> None:
    # Reinstates the bug, deliberately - a downgrade should restore the previous
    # schema, not a better one. Can fail if two clients have since used the same
    # reference, which is exactly the situation this migration exists to allow.
    op.drop_index("uq_orders_source_ref", table_name="orders")
    op.create_index(
        "uq_orders_source_ref",
        "orders",
        ["source_system", "source_order_ref"],
        unique=True,
        postgresql_where=sa.text("source_order_ref IS NOT NULL"),
    )
