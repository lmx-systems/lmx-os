"""DRV-7: where the vehicle stopped, and who surveyed the dock.

`ROADMAP_1.5.md`'s `DRV-7` spec, step 1. Two columns and nothing else.

**`stop_point`** is the one question the receiver profile could not already
answer. Every other thing the survey asks has a column and a vocabulary
(`landing_surface`, `curb_access`, `door_path`, `obstruction`, `who_receives`,
`walk_distance_band`, `carry_effort`, `appointment_required`) - all validated,
all tested, all written by nothing until now. Where a driver can legally leave
the vehicle is not among them, and it is the fact that decides whether a stop
is servable at all.

**`surveyed_by_driver_id`** so a bad surveyor can be found and their answers
discounted. The same argument `driver_documents.reviewed_by_ops_user_id` makes:
an unattributed judgement is not much better than no judgement, and these
answers become `M5`'s training labels. Nullable, because rows surveyed before
this column existed have no answer and inventing one would be worse than the
gap.

Nullable and additive: no backfill, no default, nothing to rewrite.

Revision ID: 0065
Revises: 0064
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: Union[str, None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "receiver_profiles",
        sa.Column("stop_point", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "receiver_profiles",
        sa.Column("surveyed_by_driver_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_receiver_profiles_surveyed_by_driver",
        "receiver_profiles",
        "drivers",
        ["surveyed_by_driver_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_receiver_profiles_surveyed_by_driver",
        "receiver_profiles",
        type_="foreignkey",
    )
    op.drop_column("receiver_profiles", "surveyed_by_driver_id")
    op.drop_column("receiver_profiles", "stop_point")
