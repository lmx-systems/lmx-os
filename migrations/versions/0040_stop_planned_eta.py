"""stops.planned_eta - the ETA as first predicted, kept for accuracy scoring

`Stop.eta` is refreshed as a route progresses, which is what a driver needs. That
makes it useless for the thing `app/models/stop.py` says it is for: comparing
`arrived_at` against `eta` as ETA-accuracy ground truth (docs/ROADMAP.md I1). A value
recomputed until the moment of arrival is accurate by construction and measures
nothing.

So the two are separated. `planned_eta` is written once, on the first computation for
a stop - in practice when the driver accepts the offer - and never updated.
`arrived_at - planned_eta` is then a real error over a real horizon.

No backfill. Every existing stop has a null `eta` because nothing ever wrote one, so
there is no history to derive a prediction from, and inventing one would manufacture
exactly the measurement this column exists to make honest.

Revision ID: 0040
Revises: 0039
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # timezone=True, as with every other timestamp on this table - see the comment on
    # Order.hold_deadline for the bug a naive column caused here before.
    op.add_column(
        "stops", sa.Column("planned_eta", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("stops", "planned_eta")
