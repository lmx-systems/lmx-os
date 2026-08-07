"""client signup fields

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-07

What a public signup captures beyond what `clients` already held
(docs/LMX_LINK_PLAN.md; the signup funnel columns themselves landed in 0028).

`service_area` is free text on purpose: hubs have no service-area model, so a
signup cannot be routed to the right hub automatically. A pending client is
placed on a provisional hub and ops assigns the real one at approval, reading
this. Structured routing waits until there is more than one hub to route between.

`contact_phone` belongs to the company rather than to a login - it is how ops
calls back to qualify an applicant - which is why it is here and not on
client_users.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("service_area", sa.String(255), nullable=True))
    op.add_column("clients", sa.Column("contact_phone", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "contact_phone")
    op.drop_column("clients", "service_area")
