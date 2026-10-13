"""Off-platform records: a gig job logged as a golden record (DE-7)

`docs/ROADMAP_1.5.md` DE-7. A driver who delivers a job from Curri, Dispatch or
Roadie logs it in the app, and it becomes one record the way an LMX delivery is
one: a stand-in order holding the item, a pickup stop and a drop-off stop, all
fenced in an inactive hub (`app/record/off_platform.py`). Two changes here.

**The gig job knows its record.** `gig_jobs.record_order_id` is the stand-in
order (one record per job, so unique), and `record_bonus_cents` the bonus owed
for logging it - recorded, not paid: there is no payout path for it until Stripe
Connect exists.

**How an off-platform delivery's profile is graded.** D-TIER's delivery profile
is urgency, a time window, and both ends located. Two of those are written for
orders LMX holds, and an off-platform job never was:

  - **Urgency** is `sla_tier`, which is LMX's call on how long it may hold an
    order. Nobody held a platform job; its urgency is the platform's own deadline,
    so for `source_system = 'off_platform'` a drop-off deadline counts as the
    urgency. An off-platform order's `sla_tier` stays null rather than a tier
    LMX never chose.
  - **The pickup end is located** through the order's shop, and an off-platform
    job has no shop. Its pickup counter is located when its site has coordinates
    or the driver's pin at the counter was taken.

Every other record is graded exactly as before.

Revision ID: 0079
Revises: 0078
"""
import importlib.util
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0079"
down_revision: Union[str, None] = "0078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _view_from(filename: str) -> str:
    """A migration's `_RECORD_TIERS`, read from its file rather than copied here."""
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._RECORD_TIERS


_PROFILE_BEFORE = """        CASE WHEN r.stop_type = 'visit' THEN NULL ELSE COALESCE(
            o.sla_tier IS NOT NULL
            AND COALESCE(o.promised_at, o.delivery_window_end) IS NOT NULL
            AND o.delivery_lat IS NOT NULL AND o.delivery_lng IS NOT NULL
            AND sh.lat IS NOT NULL AND sh.lng IS NOT NULL,
            false
        ) END AS has_delivery_profile,"""

_PROFILE_AFTER = """        CASE WHEN r.stop_type = 'visit' THEN NULL ELSE COALESCE(
            (
                o.sla_tier IS NOT NULL
                -- DE-7: nobody held a platform job; its deadline is its urgency.
                OR (o.source_system = 'off_platform' AND o.delivery_window_end IS NOT NULL)
            )
            AND COALESCE(o.promised_at, o.delivery_window_end) IS NOT NULL
            AND o.delivery_lat IS NOT NULL AND o.delivery_lng IS NOT NULL
            AND (
                (sh.lat IS NOT NULL AND sh.lng IS NOT NULL)
                -- DE-7: no shop, so the pickup counter's site or pin locates it.
                OR (o.source_system = 'off_platform' AND EXISTS (
                    SELECT 1
                    FROM stop_orders pso
                    JOIN stops pst ON pst.id = pso.stop_id AND pst.stop_type = 'pickup'
                    LEFT JOIN handoff_points php ON php.id = pst.handoff_point_id
                    LEFT JOIN locations pl ON pl.id = php.location_id
                    WHERE pso.order_id = o.id
                      AND (
                          (pl.lat IS NOT NULL AND pl.lng IS NOT NULL)
                          OR (pst.completed_lat IS NOT NULL AND pst.completed_lng IS NOT NULL)
                          OR (pst.arrived_lat IS NOT NULL AND pst.arrived_lng IS NOT NULL)
                      )
                ))
            ),
            false
        ) END AS has_delivery_profile,"""


def _record_tiers() -> str:
    previous = _view_from("0077_visit_stops.py")
    assert _PROFILE_BEFORE in previous, "0077's delivery-profile clause moved; update 0079"
    return previous.replace(_PROFILE_BEFORE, _PROFILE_AFTER)


def upgrade() -> None:
    op.add_column(
        "gig_jobs",
        sa.Column(
            "record_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", name="fk_gig_jobs_record_order"),
            nullable=True,
        ),
    )
    op.create_unique_constraint("uq_gig_jobs_record_order", "gig_jobs", ["record_order_id"])
    op.add_column("gig_jobs", sa.Column("record_bonus_cents", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_gig_jobs_record_bonus_cents", "gig_jobs", "record_bonus_cents IS NULL OR record_bonus_cents >= 0"
    )
    op.execute("DROP VIEW IF EXISTS record_tiers")
    op.execute(_record_tiers())


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS record_tiers")
    op.execute(_view_from("0077_visit_stops.py"))
    op.drop_constraint("ck_gig_jobs_record_bonus_cents", "gig_jobs", type_="check")
    op.drop_column("gig_jobs", "record_bonus_cents")
    op.drop_constraint("uq_gig_jobs_record_order", "gig_jobs", type_="unique")
    op.drop_column("gig_jobs", "record_order_id")
