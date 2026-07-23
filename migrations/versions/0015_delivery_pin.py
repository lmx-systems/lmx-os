"""delivery pin

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-23

Real PIN issuance/verification for proof of delivery (docs/ROADMAP.md
A4) - stops.delivery_pin is the real, issued PIN (generated and texted
to the customer when the dropoff stop is created); stops.pod_pin
(existing) is what the driver submits at complete_stop time, checked
against this. pin_verification_attempts caps brute-force guessing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stops", sa.Column("delivery_pin", sa.String(8), nullable=True))
    op.add_column(
        "stops",
        sa.Column("pin_verification_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("stops", "pin_verification_attempts")
    op.drop_column("stops", "delivery_pin")
