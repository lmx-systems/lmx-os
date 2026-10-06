"""Layer 4 of the golden record: a person's verdict on one record, per machine.

`docs/ROADMAP_1.5.md` DE-1, decision D-GR. For each machine - drone, sidewalk
robot, car - a scorer says yes, no or can't tell, and for every "no" which
checklist question failed. These verdicts are the autonomy qualifier, the fifth
of a record's elements (`record_tiers`, migration 0069): a record is labelled
when its first scorer has given all three machines a verdict.

A second scorer works blind (`is_blind_second`): they can't see the first, and
agreement between the two is what lets a record count toward a test pool
(`DE-9`). The first scorer's verdicts are the ones the tier reads.

`machine_limits_version` names the machine limits the verdict was scored against
(the drone weight limit is still open). Nullable until `DE-2` adds the versioned
limits table and makes it required.

Named `machine_labels` rather than the addendum's `labels`: the console already
"labels" docks with a node class (`IDN-3`), and the two must not be confused.
"""
import uuid

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

MACHINES = ("drone", "sidewalk_robot", "car")
VERDICTS = ("yes", "no", "cant_tell")


class MachineLabel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "machine_labels"

    # The record: one delivery or visit, which is a stop.
    stop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stops.id"), nullable=False, index=True)
    handoff_point_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("handoff_points.id"), nullable=True
    )
    machine: Mapped[str] = mapped_column(String(16), nullable=False)
    verdict: Mapped[str] = mapped_column(String(12), nullable=False)
    # The scorer's confidence, 0 to 1, where they gave one.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # For a "no": the checklist questions that failed.
    reason_codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    scorer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_blind_second: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    machine_limits_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
