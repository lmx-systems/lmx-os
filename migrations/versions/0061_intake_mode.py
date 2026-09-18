"""orders.intake_mode: whether this order is live work or history (`ING-3`)

*"History re-ingests without double-counting or mutating decisions."*

Ingesting an order does six things: it creates the record, prices it, assigns a
control arm, may record an abstention, puts it in the live hold queue, and
counts it. For an order that already happened, five of those are wrong - and
three of them are wrong in ways nothing can undo.

**Pricing.** `generate_invoice` selects delivered orders with a non-null
`fee_cents` and no invoice. A backfilled historical delivery matches that query
exactly, so the customer is billed a second time for work already invoiced. This
is the "double-counting" the done-when names.

**The arm.** `assign_arm` writes to `experiment_assignments`, which is
append-only and immutable by trigger (`0054`). An arm assigned to an order that
was delivered weeks ago is experiment data about an experiment that order was
never in, and it cannot be deleted afterwards - the measurement `EXP-1` exists
to produce would be contaminated permanently, by an import.

**The hold queue.** A backfilled order landing in Redis dispatches a driver to
collect a delivery that already happened.

Hence a mode on the row rather than a convention in a script. A flag is also why
this is not done by leaving `fee_cents` null: null already means "no rate was
configured at intake" (see `Order.fee_cents`), and overloading it would make an
unpriced live order and a historical one indistinguishable at exactly the moment
somebody is trying to work out why a customer was not billed.

Defaults to `live` and is NOT NULL, so every existing row - all of them live
work - reads correctly without a backfill of its own.

Revision ID: 0061
Revises: 0060
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "intake_mode",
            sa.String(length=16),
            nullable=False,
            server_default="live",
        ),
    )
    op.create_check_constraint(
        "ck_orders_intake_mode", "orders", "intake_mode IN ('live', 'backfill')"
    )
    # Partial, because the whole point of the column is that one value is the
    # overwhelming majority and the other is the one anything ever filters on.
    op.create_index(
        "ix_orders_backfilled",
        "orders",
        ["client_id", "delivered_at"],
        postgresql_where=sa.text("intake_mode = 'backfill'"),
    )


def downgrade() -> None:
    """Drops the distinction between history and live work.

    Every backfilled order becomes indistinguishable from one we are actually
    operating, which means the next invoice run bills for all of it. Not a
    reversible migration in any sense that matters: delete the backfilled rows
    before running this, or do not run it.
    """
    op.drop_index("ix_orders_backfilled", table_name="orders")
    op.drop_constraint("ck_orders_intake_mode", "orders", type_="check")
    op.drop_column("orders", "intake_mode")
