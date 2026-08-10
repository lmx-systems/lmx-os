"""fields the LMX Link scorecard needs

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-10

Two facts §3.4's success metrics depend on that nothing was recording
(docs/LMX_LINK_PLAN.md, app/reporting/lmx_link.py).

`orders.entry_seconds` — how long a client took to enter this order. The portal has
been *sending* it since L6 and the API has been *logging* it, but a log line is not a
dataset: "order entry time, second order onward, under 30 seconds" is a distribution
over orders, and answering it meant either persisting the number or building log
aggregation to recover something we already had in hand.

`clients.approved_at` — when a signup was approved. Approval only flipped
`signup_status` to `active`, so the instant was lost. That made §3.4's *headline*
metric — "time from new customer says yes to first order delivered", target same day,
described in the plan as "the entire point of LMX Link" — uncomputable. Nullable with
no backfill: for clients approved before this, the moment genuinely isn't recoverable,
and inventing one from `updated_at` would produce a number that looks like data.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("entry_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "clients", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("clients", "approved_at")
    op.drop_column("orders", "entry_seconds")
