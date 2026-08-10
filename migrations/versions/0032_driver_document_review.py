"""driver document review

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-10

Separates what a driver asserts about their compliance documents from what LMX
established (docs/ROADMAP.md R4). See app/models/driver_document.py for the two
holes this closes.

**`expires_at` is RENAMED rather than reused, deliberately.** Keeping the old name
for the driver-supplied value would leave a field called "the expiry date" that no
gate may trust - and the next person to write a compliance check would reach for
it. Renaming makes the unverified value impossible to read by accident:
`claimed_expires_at` cannot be mistaken for an established fact.

**OPERATIONAL CONSEQUENCE, stated plainly: every existing driver becomes
non-compliant the moment this runs.** Existing rows keep their driver-supplied
date as `claimed_expires_at`, land in `review_status='pending'`, and have no
`verified_expires_at` - so the availability gate refuses to put them online until
an ops reviewer has actually looked at their license and insurance.

That is the correct direction to fail and it is not a side effect. Nothing in this
table was ever verified, so there is no verified state to preserve; backfilling
`verified` would be inventing a review that never happened, which is exactly the
defect being fixed. Real drivers on real roads is gated on R1/R2 regardless, so
the practical cost now is a short review pass over the pilot roster using the new
ops endpoints - not stranded drivers.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "driver_documents", "expires_at", new_column_name="claimed_expires_at"
    )
    op.add_column(
        "driver_documents", sa.Column("verified_expires_at", sa.Date(), nullable=True)
    )
    op.add_column(
        "driver_documents",
        sa.Column(
            "review_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "driver_documents",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "driver_documents",
        sa.Column("reviewed_by_ops_user_id", sa.dialects.postgresql.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_driver_documents_reviewed_by",
        "driver_documents",
        "ops_users",
        ["reviewed_by_ops_user_id"],
        ["id"],
    )
    op.add_column(
        "driver_documents", sa.Column("rejection_reason", sa.String(length=500), nullable=True)
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_driver_documents_reviewed_by", "driver_documents", type_="foreignkey"
    )
    op.drop_column("driver_documents", "rejection_reason")
    op.drop_column("driver_documents", "reviewed_by_ops_user_id")
    op.drop_column("driver_documents", "reviewed_at")
    op.drop_column("driver_documents", "review_status")
    op.drop_column("driver_documents", "verified_expires_at")
    op.alter_column(
        "driver_documents", "claimed_expires_at", new_column_name="expires_at"
    )
