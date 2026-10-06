"""A stop flag the system inferred, beside the ones a driver raised.

The learning loop reads `hold_window_too_short` / `hold_window_too_long` flags
and nothing wrote them: the driver app has no control for either, so the loop
never fired and the scorecard's "held wrong" rate was never a measurement.
`app/learning_loop/not_ready.py` now infers the first of those from a pickup
stop's dwell against its dock's usual dwell - a driver who stood at the counter
far longer than that counter usually takes was waiting for an order that was
not ready.

Three changes, all on `stop_flags`:

  - `source` says who raised it. `driver` for every existing row, `inferred`
    for the new writer, so a reader that means "a driver reported this" (the
    exception queue) can say so, and one that means "the window was wrong" (the
    scorecard, the loop) can count both.
  - `created_by_driver_id` becomes nullable, because an inferred flag has no
    driver behind it and inventing one would be a lie the column cannot mark.
  - A partial unique index on (stop_id, flag_type) for inferred rows, so a
    re-run of the nightly job can never flag the same stop twice. Partial
    because a driver may legitimately flag a stop twice with a note each time.

Revision ID: 0068
Revises: 0067
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: Union[str, None] = "0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stop_flags",
        sa.Column("source", sa.String(16), nullable=False, server_default="driver"),
    )
    op.alter_column("stop_flags", "created_by_driver_id", nullable=True)
    op.create_index(
        "uq_stop_flags_inferred_once",
        "stop_flags",
        ["stop_id", "flag_type"],
        unique=True,
        postgresql_where=sa.text("source = 'inferred'"),
    )


def downgrade() -> None:
    op.drop_index("uq_stop_flags_inferred_once", table_name="stop_flags")
    # Inferred rows have no driver, and the column is about to refuse NULL.
    op.execute("DELETE FROM stop_flags WHERE source = 'inferred'")
    op.alter_column("stop_flags", "created_by_driver_id", nullable=False)
    op.drop_column("stop_flags", "source")
