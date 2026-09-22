"""drivers.phone is unique — because login depends on it being so

`app/api/driver_routes.py`'s OTP path looks a driver up by phone and consumes
the result with `scalar_one_or_none()`, which **raises** on more than one row.
Two drivers sharing a number therefore locks *both* of them out of the app with
a 500, not one of them with a sensible error.

Nothing enforced it. There was also no way to create a driver at all - every row
is a hand-written insert (`docs/ROADMAP_AUDIT_2026-09.md`), which is exactly the
path least likely to check for a collision and the most likely to be run twice.

**At the database rather than in the new endpoint**, because the endpoint is not
the only writer and on today's evidence is not even the usual one. A constraint
here protects the hand-inserted row too; a check in the handler protects only
requests that go through the handler.

Not partial and not nullable: `Driver.phone` is `nullable=False`, so every row
has one and every one of them must be distinct.

## If this migration fails

It means two drivers already share a number and one of them cannot log in
today. Resolve the duplicate before re-running - there is no correct automatic
answer to which of two people owns a phone number.

Revision ID: 0063
Revises: 0062
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0063"
down_revision: Union[str, None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("uq_drivers_phone", "drivers", ["phone"], unique=True)


def downgrade() -> None:
    """Drops the constraint.

    Recoverable, unlike most downgrades here - nothing is lost. But it re-opens
    the state where creating a second driver with the same number silently
    breaks OTP login for both of them.
    """
    op.drop_index("uq_drivers_phone", table_name="drivers")
