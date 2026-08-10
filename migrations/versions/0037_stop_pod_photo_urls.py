"""stop pod photo urls

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-10

Somewhere to keep every proof-of-delivery photo, now that an order can require more
than one (`ProofRequirements`, docs/LMX_LINK_PLAN.md §1.2; app/delivery/proof.py).

`stops.pod_photo_url` holds a single URL and predates configurable proof. Enforcing a
four-photo requirement while storing one would mean insisting on evidence we then
failed to keep - which is worse than not enforcing it, because the order record would
claim the proof exists.

Additive and nullable, with `pod_photo_url` still populated from the first photo, so
nothing that reads the old column changes and no backfill is needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stops", sa.Column("pod_photo_urls", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("stops", "pod_photo_urls")
