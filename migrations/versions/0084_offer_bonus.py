"""route_offers.bonus_cents: an offer that carries a bonus (`DEC-10`)

The playbook's step-up for an order nobody has (`app/playbook/rules.py`,
`decide_unplaced`): once it has been offered and declined or left to lapse, the
next offer carries a bonus. The amount is a placeholder (decision D-BONUS, 13
October 2026: $5, `settings.offer_bonus_cents`), copied onto the offer when it is
made so a later change to the setting never alters what a driver was promised.

Recorded, not paid: drivers are paid by the hour and nothing pays per job yet,
as with the off-platform record bonus (`gig_jobs.record_bonus_cents`). It is
owed once the order is delivered on the offer the driver accepted, and the
driver's earnings screen shows it as owed. Null for an offer with no bonus.

Revision ID: 0084
Revises: 0083
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0084"
down_revision: Union[str, None] = "0083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("route_offers", sa.Column("bonus_cents", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_route_offers_bonus_positive", "route_offers", "bonus_cents > 0")


def downgrade() -> None:
    """Drops what each offer promised on top. Bonuses already owed lose their record."""
    op.drop_constraint("ck_route_offers_bonus_positive", "route_offers", type_="check")
    op.drop_column("route_offers", "bonus_cents")
