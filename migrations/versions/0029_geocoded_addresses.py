"""geocoded addresses

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-07

The address cache behind ad-hoc pickup (docs/LMX_LINK_PLAN.md §1.2, "geocode
once on first order per address, cache and reuse").

This table is what makes a rate-limited, no-account geocoder viable: without it
request volume is orders-per-day, with it it is new-addresses-per-day, and §2.2
principle 3 is explicit that those differ by orders of magnitude.

Failures are cached too - null lat/lng means "we asked and it didn't resolve" -
because the realistic failure is a typo the customer immediately retries. See
app/models/geocoded_address.py for why that is stored rather than left absent.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geocoded_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # The cache key. Unique so a concurrent double-geocode is a conflict to
        # resolve rather than two rows that could disagree with each other.
        sa.Column("normalized_address", sa.String(255), nullable=False),
        sa.Column("raw_address", sa.String(255), nullable=False),
        # Null together = asked and unresolved. A deliberate record, not a gap.
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("display_name", sa.String(500), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("normalized_address", name="uq_geocoded_addresses_normalized"),
    )
    op.create_index(
        "ix_geocoded_addresses_normalized", "geocoded_addresses", ["normalized_address"]
    )


def downgrade() -> None:
    op.drop_index("ix_geocoded_addresses_normalized", table_name="geocoded_addresses")
    op.drop_table("geocoded_addresses")
