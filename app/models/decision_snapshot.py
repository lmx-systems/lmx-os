"""What the dispatcher knew when it decided, frozen (`docs/ROADMAP_1.5.md` REC-1).

`shadow_decision` records what a cycle *decided* - counts, durations, the
assignments it produced. This records what it *saw*: the released orders with
their deadlines and coordinates, the drivers with their positions and remaining
capacity, the engine that ran. Those are different things, and only the second
one makes a decision reproducible.

**Why that matters, concretely.** Phase 2 exists to produce a measured
cost-per-drop delta with a control arm, and Phase 3 makes LMX the one who
decided. The first time a customer disputes a savings statement, the question
is not what we chose - it is what we knew when we chose it. §2.3 is blunt that
a savings share is *"a collections problem disguised as a revenue line"*; a
decision nobody can reconstruct is what turns it into one.

**The rule this table exists to keep: no field may reference data created after
the decision.** That is the hard part, and it is why the inputs are stored as
values rather than as foreign keys. An `order_id` pointing at `orders` would
read back today's tier and today's deadline, not the ones the optimizer
actually had - so a replay months later would silently reconstruct the wrong
decision and look authoritative doing it. The ids are kept too, for tracing,
but nothing in a replay depends on them still resolving.

**Immutable is enforced by the database, not by convention.** Migration 0051
installs a trigger that rejects UPDATE and DELETE on this table. An audit log
that application code can quietly rewrite is not an audit log, and the one
moment somebody would want to rewrite it is the moment it matters most.

Outcomes are deliberately absent. REC-3's ledger attaches them by key, so
recording what happened never requires touching the row that says what was
decided.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPrimaryKeyMixin

# A live cycle acted on its decision; a shadow cycle recorded one and did
# nothing (W9). Both are decisions and both belong here - comparing them is the
# entire point of shadow mode, and that comparison needs the inputs of each.
MODE_LIVE = "live"
MODE_SHADOW = "shadow"


class DecisionSnapshot(Base, UUIDPrimaryKeyMixin):
    """One dispatch cycle's inputs and output, as they were at that instant.

    No `TimestampMixin`: `updated_at` would be a field that changes on a row
    nothing is allowed to change, which is exactly the wrong signal to leave in
    an append-only table.
    """

    __tablename__ = "decision_snapshots"

    # Not a foreign key - see the migration. A decision about a hub that is
    # later removed must still be readable, and the optimizer runs cycles for
    # hubs that have no row.
    hub_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # When the cycle planned, by the planner's own clock, carried from the plan
    # rather than stamped on write - a slow commit must not move the decision.
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(48), nullable=False)

    # The frozen inputs. A dict of plain values - see the module docstring for
    # why this is not a set of foreign keys.
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # What it decided, from the same plan object, so the pair is consistent by
    # construction rather than by two writers agreeing.
    assignments: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    unassigned_stop_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Why each held order was released this cycle, or was not. `run_hold_cycle`
    # has always produced this and `run_cycle` always discarded it, so the
    # system held orders and recorded no reason for any of it - in a product
    # whose central claim is that the hold IS the product. `AGT-4` cannot
    # explain a hold the record does not contain, which is what surfaced it.
    #
    # An output, so it sits beside `assignments` rather than inside `inputs`:
    # putting a decision into the frozen inputs would make the replay hash
    # depend on what the cycle concluded rather than on what it saw.
    hold_decisions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # SHA-256 over the canonical form of `inputs`. Two jobs: it detects a row
    # that has been altered despite the trigger (a restore, a migration, a
    # superuser), and it lets two cycles be compared for "did the optimizer see
    # the same world" without diffing two large JSON blobs.
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    plan_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    hub_closed: Mapped[bool] = mapped_column(nullable=False, default=False)

    # Denormalised counts, so the common questions do not require unpacking the
    # JSON. Derived from `inputs` at write time and never recomputed - if they
    # ever disagree with it, the row has been tampered with, which is a finding
    # rather than a number to correct.
    stop_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    driver_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assigned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
