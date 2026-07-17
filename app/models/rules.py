"""
Learning-loop rule tables (component 6: Annotation and Learning Loop).

Nightly pattern detection over stop_flags/driver annotations proposes rules
here; a human (or, later, an automated confidence threshold) promotes a
proposed_rule into active_rules, at which point the Dispatch Optimizer and
Batch-Hold Queue start applying it.
"""
from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ProposedRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "proposed_rules"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. 'shop_hold_window_override', 'cluster_radius_override'
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # e.g. {"shop_id": "..."} or {"hub_id": "..."}
    proposed_change: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0)
    supporting_annotation_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), default="pending_review", nullable=False)
    # pending_review | approved | rejected


class ActiveRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "active_rules"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    promoted_from_proposed_rule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("proposed_rules.id"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
