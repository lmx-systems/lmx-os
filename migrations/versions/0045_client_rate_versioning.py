"""client_rates becomes append-only and effective-dated; orders record which version priced them

T2.5 A1. A rate card was one row per (client, tier), and changing a price UPDATEd it. The
orders were never at risk - `fee_cents` and `fee_breakdown` are frozen at ingestion - but
the card's own history was destroyed. "What was this client's T2 rate on 15 August" had no
answer, and neither did "which rate priced this drop", which is the audit trail H1 asks for.

Three changes, and the order matters:

  1. Add `effective_from`, backfilled from `created_at`. Approximate for any row edited
     before this migration, and deliberately not dressed up as more - the instant an
     overwritten version started applying is not recoverable, and inventing one would put
     a fabricated date into an audit trail.
  2. Replace the unique constraint. `(client_id, sla_tier)` is exactly what made versioning
     impossible, since a second version is a second row for the same pair. Dropped BEFORE
     any second version can exist, so the window where both are true never opens.
  3. Add `orders.rate_version_id`. A real FK with no cascade, matching
     app/legal/retention.py's reasoning: Postgres then refuses to delete a rate version
     that priced an order.

Null `rate_version_id` on every pre-existing order is correct and permanent. Those orders
were priced by a row that may since have been overwritten, so the honest answer to "which
version" is that we no longer know - not a guess at the row that happens to be there now.

Revision ID: 0045
Revises: 0044
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable first, backfill, then NOT NULL - the standard three-step, because the table
    # may already hold rows and a NOT NULL column cannot be added to them without a value.
    op.add_column(
        "client_rates",
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE client_rates SET effective_from = created_at WHERE effective_from IS NULL")
    # NOT NULL *with* a server default, rather than NOT NULL alone. A rate created without
    # an explicit date is effective immediately, which is the correct meaning and also the
    # safe one - the alternative turns every constructor that predates this column into a
    # runtime IntegrityError, and there are a lot of them.
    op.alter_column(
        "client_rates",
        "effective_from",
        nullable=False,
        server_default=sa.func.now(),
    )
    op.create_index("ix_client_rates_effective_from", "client_rates", ["effective_from"])

    op.drop_constraint("uq_client_rates_client_tier", "client_rates", type_="unique")
    op.create_unique_constraint(
        "uq_client_rates_client_tier_effective",
        "client_rates",
        ["client_id", "sla_tier", "effective_from"],
    )

    op.add_column(
        "orders",
        sa.Column("rate_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_orders_rate_version_id",
        "orders",
        "client_rates",
        ["rate_version_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_orders_rate_version_id", "orders", type_="foreignkey")
    op.drop_column("orders", "rate_version_id")

    # Restoring the old constraint can fail, and that is correct rather than unfortunate:
    # if any client/tier has more than one version by now, there is no single row to go
    # back to and the downgrade would have to pick one arbitrarily. Refusing is the honest
    # outcome - collapse the versions by hand first if this really has to be undone.
    op.drop_constraint("uq_client_rates_client_tier_effective", "client_rates", type_="unique")
    op.create_unique_constraint(
        "uq_client_rates_client_tier", "client_rates", ["client_id", "sla_tier"]
    )

    op.drop_index("ix_client_rates_effective_from", table_name="client_rates")
    op.drop_column("client_rates", "effective_from")
