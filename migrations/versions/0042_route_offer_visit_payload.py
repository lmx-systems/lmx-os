"""route_offers.visit_payload - the planned leg sequence, so the route driven is the route solved

`stop_payload` is a list of orders and is what the driver sees in an offer preview
("3 stops, these shops"). It cannot express a sequence of *legs*, so `accept_offer`
rebuilt every route as "every pickup, then every dropoff" - a legal route, but not the
one the optimizer costed. `visit_payload` carries the plan itself: order, leg, and the
solver's planned arrival.

Nullable, and `accept_offer` falls back to the old construction when it is absent. That
is not defensiveness about the schema, it is about time: offers live for
`job_offer_ttl_seconds` (120s by default), so at the moment this deploys there are real
offers already sitting in front of real drivers with no visit payload. Refusing those
would make a mid-shift deploy reject work a driver was about to accept. The fallback
stops mattering two minutes after rollout, and the tests keep it honest either way.

`stop_payload` is deliberately left alone rather than reshaped. The driver app reads it
for the offer preview, and changing a payload that in-flight offers are already holding
would break exactly the same drivers.

Revision ID: 0042
Revises: 0041
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "route_offers",
        sa.Column("visit_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("route_offers", "visit_payload")
