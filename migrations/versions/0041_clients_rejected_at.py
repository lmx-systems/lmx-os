"""clients.rejected_at - when an application was declined

The privacy policy states that declined applications are kept for twelve months and
then deleted (app/legal/content/privacy.md), so something has to know when the
twelve months started.

`updated_at` was the tempting shortcut and is wrong: any future write to the row -
a backfill, a column addition, an ops tool touching it - would silently restart the
retention clock on somebody's rejected application. A dedicated timestamp is set once
by the reject endpoint and never moved.

**No backfill, deliberately.** Any rejection recorded before this column existed has
no date, and `prune_declined_applications` skips a row with a null `rejected_at`
rather than assuming one. Inventing a date would either delete a record early or
claim we knew something we did not. The sweep logs how many it skipped for this
reason, so a stuck row is visible rather than silent.

Revision ID: 0041
Revises: 0040
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clients", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("clients", "rejected_at")
