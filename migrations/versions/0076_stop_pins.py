"""Where the phone was at a stop: the stop pin and the door pin

`docs/ROADMAP_1.5.md` DE-4. The stop pin is taken at the Arrived tap or on
entering the stop's geofence, the door pin at Delivered; until now no
coordinate was recorded at either, so a handoff point's pins and the walk
between them had nothing to come from. Nullable: a stop the app recorded no
position for has none, and that is unknown, not zero.

Revision ID: 0076
Revises: 0075
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: Union[str, None] = "0075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    "arrived_lat", "arrived_lng", "arrived_accuracy_m",
    "completed_lat", "completed_lng", "completed_accuracy_m",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("stops", sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("stops", name)
