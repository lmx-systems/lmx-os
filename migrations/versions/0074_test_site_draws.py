"""Frozen test sites: one draw per site, before collection

`docs/ROADMAP_1.5.md` DE-8, decision D-TEST. `test_site_draws` records, per
site, whether it was drawn into its region's test pool, the group it was drawn
from, and the share and salt the draw used. `app/models/site_draw.py` says
what each column means; `app/record/site_draws.py` says how the draw works.

New Jersey cannot be drawn (D-NJ): its records test the process and seed
training, and never count toward a test pool. A check constraint holds that, so
no later code path can draw it by accident.

Revision ID: 0074
Revises: 0073
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0074"
down_revision: Union[str, None] = "0073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_site_draws",
        sa.Column(
            "location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), primary_key=True
        ),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("kind_of_site", sa.String(32), nullable=False),
        sa.Column("setting", sa.String(12), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_test", sa.Boolean(), nullable=False),
        sa.Column("share", sa.Float(), nullable=False),
        sa.Column("salt", sa.String(64), nullable=False),
        sa.Column("drawn_by", sa.String(64), nullable=False),
        sa.Column("drawn_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("share > 0 AND share <= 0.5", name="ck_test_site_draws_share"),
        sa.CheckConstraint("region <> 'NJ'", name="ck_test_site_draws_not_nj"),
    )
    op.create_index("ix_test_site_draws_region", "test_site_draws", ["region"])


def downgrade() -> None:
    op.drop_index("ix_test_site_draws_region", table_name="test_site_draws")
    op.drop_table("test_site_draws")
