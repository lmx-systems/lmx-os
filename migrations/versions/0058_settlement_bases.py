"""settlement_bases: the definition a statement was issued under (`STL-2`)

*"Baseline changes need sign-off from both sides and are versioned."*

Necessary the moment `--recompute` existed. `STL-1` reads costs from the outcome
ledger and `scripts/settle_month.py --recompute` supersedes them when an input
changes, so a statement is not reproducible on its own - a customer who saw one
figure in August and another in September's recomputation has a dispute, and
nothing recorded what the first rested on.

Append-only in the way that matters: `inputs`, `inputs_hash` and
`cost_fingerprint` are never rewritten, so a basis cannot be edited into
agreeing with a later number. The signature columns and `superseded_at` are the
only things that change, which is why this carries no immutability trigger -
unlike `experiment_assignments`, the row is meant to gain a countersignature
after it is written.

One live basis per client per period, enforced by a partial unique index. Two
would be two answers to "what was this statement calculated under", and the
whole point is that there is one.

Revision ID: 0058
Revises: 0057
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "settlement_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.Column("cost_fingerprint", sa.String(length=64), nullable=False),
        # Kept out of `inputs` deliberately: `inputs_hash` covers the definition,
        # and folding the costs into it would make every recomputation look like
        # a change of method.
        sa.Column("costs", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("lmx_signed_by", sa.String(length=120), nullable=True),
        sa.Column("lmx_signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("customer_signed_by", sa.String(length=120), nullable=True),
        sa.Column("customer_signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_settlement_bases_version"),
        # A signature is a name and a date together. Half of one is a row that
        # looks signed to a query and is not.
        sa.CheckConstraint(
            "(lmx_signed_by IS NULL) = (lmx_signed_at IS NULL)",
            name="ck_settlement_bases_lmx_signature",
        ),
        sa.CheckConstraint(
            "(customer_signed_by IS NULL) = (customer_signed_at IS NULL)",
            name="ck_settlement_bases_customer_signature",
        ),
        sa.UniqueConstraint(
            "client_id", "version", name="uq_settlement_bases_client_version"
        ),
    )
    op.create_index("ix_settlement_bases_client_id", "settlement_bases", ["client_id"])
    op.create_index("ix_settlement_bases_issued_at", "settlement_bases", ["issued_at"])
    op.create_index("ix_settlement_bases_inputs_hash", "settlement_bases", ["inputs_hash"])
    # One live basis per client per period. Two would be two answers to "what
    # was this statement calculated under".
    op.create_index(
        "uq_settlement_bases_live_period",
        "settlement_bases",
        ["client_id", "period_start", "period_end"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    """Drops the record of what every issued statement was calculated under.

    Not recoverable. The statements themselves are PDFs somebody has received, and
    without these rows there is no way to say which definition produced a figure
    a customer is holding - which is the dispute this table exists to prevent.
    """
    op.drop_index("uq_settlement_bases_live_period", table_name="settlement_bases")
    for index in (
        "ix_settlement_bases_inputs_hash",
        "ix_settlement_bases_issued_at",
        "ix_settlement_bases_client_id",
    ):
        op.drop_index(index, table_name="settlement_bases")
    op.drop_table("settlement_bases")
