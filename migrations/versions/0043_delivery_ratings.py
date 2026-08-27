"""delivery_ratings - what the recipient thought of the delivery

docs/ROADMAP.md F13. A one-tap score plus an optional comment, captured from the
recipient's own tracking link after the delivery lands.

The unique constraint on (order_id, rated_by) is the interesting part: it makes a
second submission from the same party an update to their own row rather than a new
one, so counting rows counts people. `rated_by` exists on day one so that adding a
client-side rating later is an insert with a different author rather than a schema
change - see the model docstring for why the roadmap's "prompt to the shop" does not
survive contact with this data model.

A CHECK on the score range, because the value is written from an unauthenticated
endpoint and a 1-5 scale that can hold 9 is not a 1-5 scale. The API validates too;
this is the constraint that stays true if someone writes a row by hand.

Revision ID: 0043
Revises: 0042
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "delivery_ratings",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        # server_default matching TimestampMixin, which is where these come from - the
        # mixin declares no Python-side default, so a column without it inserts NULL.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id"),
            nullable=False,
        ),
        sa.Column("rated_by", sa.String(16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("first_submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("order_id", "rated_by", name="uq_delivery_rating_order_rater"),
        sa.CheckConstraint("score >= 1 AND score <= 5", name="ck_delivery_rating_score"),
    )
    op.create_index(
        "ix_delivery_ratings_order_id", "delivery_ratings", ["order_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_ratings_order_id", table_name="delivery_ratings")
    op.drop_table("delivery_ratings")
