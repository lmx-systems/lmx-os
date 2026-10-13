"""One call a playbook rule made about an order (`DEC-8`, decision D-SEAT).

A rule (`app/playbook/rules.py`) reads an order that needs something done and
chooses the action a person would otherwise choose. How much it is trusted is
its `mode`, and a rule earns its way up:

* **recommend** - it records the call and does nothing. A person still acts, and
  whether they chose the same is the evidence.
* **veto** - it acts at `due_at` unless a person acted first.
* **act** - it acts at once.

`outcome` is how the call ended: `acted` (the rule did it), `agreed` or
`overruled` (a person acted first, the same or otherwise), `escalated` (the rule
handed it to a person - it has no answer for this case), or `superseded` (the
order moved on without either). An overruled call is a labelled example of the
rule being wrong: as overrides become rare, these are the labels that remain.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

MODE_RECOMMEND = "recommend"
MODE_VETO = "veto"
MODE_ACT = "act"
MODES = (MODE_RECOMMEND, MODE_VETO, MODE_ACT)

OUTCOME_OPEN = "open"
OUTCOME_ACTED = "acted"
OUTCOME_AGREED = "agreed"
OUTCOME_OVERRULED = "overruled"
OUTCOME_ESCALATED = "escalated"
OUTCOME_SUPERSEDED = "superseded"


class PlaybookDecision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "playbook_decisions"
    __table_args__ = (
        CheckConstraint("mode IN ('recommend', 'veto', 'act')", name="ck_playbook_decisions_mode"),
        CheckConstraint(
            "outcome IN ('open', 'acted', 'agreed', 'overruled', 'escalated', 'superseded')",
            name="ck_playbook_decisions_outcome",
        ),
        # One call per attempt: a redelivery that fails again is a new attempt and a new call.
        UniqueConstraint("rule", "order_id", "attempt", name="uq_playbook_decisions_rule_order_attempt"),
        # What the sweep reads each pass: the calls still waiting, by when they fall due.
        Index("ix_playbook_decisions_open", "hub_id", "due_at", postgresql_where=text("outcome = 'open'")),
    )

    rule: Mapped[str] = mapped_column(String(48), nullable=False)
    hub_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # Why, in a sentence a person reads in the exceptions queue.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # When the rule will act; null in recommend mode, and for an escalation.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default=OUTCOME_OPEN, server_default=OUTCOME_OPEN)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Whom the call names - a driver to offer the order to (`DEC-10`); null when
    # it names nobody.
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # What a person did instead or as well, whom they chose, and who they were.
    human_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    human_target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ops_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
