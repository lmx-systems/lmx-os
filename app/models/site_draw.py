"""Which sites are frozen as test sites, drawn before anything is collected there.

`docs/ROADMAP_1.5.md` DE-8, decision D-TEST. A modality model is judged on sites
it has never trained on, and "never" has to survive between runs: `ml/` used to
hold out by a hash split made per run, so a site could be test in one run and
training in the next. Here the draw happens once per site and is never redone.

One row per site that has been drawn, test or not - a site drawn into training
is recorded too, so a later draw cannot give it a second chance at the test pool.
The group (kind of site x setting) is copied onto the row as it was at the draw,
because a site whose kind is corrected later stays in the group it was drawn from.

How the draw works is in `app/record/site_draws.py`.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SiteDraw(Base):
    __tablename__ = "test_site_draws"
    __table_args__ = (
        CheckConstraint("share > 0 AND share <= 0.5", name="ck_test_site_draws_share"),
        CheckConstraint("region <> 'NJ'", name="ck_test_site_draws_not_nj"),
    )

    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    kind_of_site: Mapped[str] = mapped_column(String(32), nullable=False)
    setting: Mapped[str] = mapped_column(String(12), nullable=False)
    # This site's place in its group's sequence of draws, from 0.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # The region's test share and salt, the same on every row of a region.
    share: Mapped[float] = mapped_column(Float, nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    drawn_by: Mapped[str] = mapped_column(String(64), nullable=False)
    drawn_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
