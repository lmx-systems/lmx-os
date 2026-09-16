"""Putting an order in an arm, once, verifiably (`EXP-1`).

5-10% of orders dispatched exactly as the customer would have. That small
fraction is what turns the cost-per-drop claim from a projection of our own
assumptions into something measured - and it only works if the split is
genuinely random, genuinely fixed, and genuinely disclosed.

**Off unless the contract says otherwise.** A client with no recorded clause
date gets no arm at all, and asking for one raises. That is the done-when's
third clause made mechanical: *"in the contract before the code."* It is
deliberately not a config flag somebody can flip in a hurry - turning it on
requires stating the date the customer agreed, which is a thing you either have
or do not.
"""
import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.experiment_assignment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    EXPERIMENT_CONTROL_ARM,
    ExperimentAssignment,
)
from app.models.order import Order

# The band the roadmap specifies. Enforced rather than documented: below 5% the
# control group is too small to say anything within a quarter, and above 10% we
# are deliberately giving a paying customer a worse service on more of their
# orders than the measurement needs. Both ends are a promise to somebody.
MIN_CONTROL_FRACTION = 0.05
MAX_CONTROL_FRACTION = 0.10


class ArmNotContractedError(RuntimeError):
    """Raised when an arm is requested for a client who has not agreed to one.

    Its own type rather than a ValueError because callers should be able to
    treat it as "this client is simply not in the experiment" - which is the
    normal case, not an error in the ordinary sense - without swallowing real
    programming mistakes alongside it.
    """


def control_arm_is_live(client: Client) -> bool:
    """Whether this client has a contracted, in-band control arm."""
    return (
        client.control_arm_contracted_at is not None
        and client.control_arm_fraction is not None
        and MIN_CONTROL_FRACTION <= client.control_arm_fraction <= MAX_CONTROL_FRACTION
    )


def _draw(salt: str, order_key: str) -> float:
    """A deterministic position in [0, 1) for this order under this salt.

    A hash rather than a random draw so the assignment can be recomputed by
    anyone holding the row. That is what lets `EXP-3` verify an arm instead of
    trusting it, and what makes a re-roll visible - the recomputed value would
    stop matching the stored one.

    The salt is per-client so two customers' assignments are independent. Using
    one global salt would correlate the arms of every order sharing a reference
    format, which is the kind of subtle non-randomness that survives a casual
    look at the split.
    """
    digest = hashlib.sha256(f"{salt}:{order_key}".encode()).digest()
    # 8 bytes is ample resolution for a fraction and keeps the arithmetic exact
    # in a float64, which matters because the comparison below is what decides
    # the arm.
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


async def assign_arm(
    session: AsyncSession,
    order: Order,
    client: Client,
    *,
    now: datetime | None = None,
) -> ExperimentAssignment:
    """Put this order in an arm, at intake, once.

    Raises `ArmNotContractedError` when the client has no contracted arm -
    which is every client until somebody records the date their clause was
    agreed.

    Idempotent: an order already assigned returns its existing assignment
    rather than drawing again. Intake can be retried, and a retry that re-rolled
    the arm would quietly bias the split towards whichever arm the retry
    happened to land in.
    """
    if not control_arm_is_live(client):
        raise ArmNotContractedError(
            f"client {client.id} has no contracted control arm. Record "
            "control_arm_contracted_at and a fraction between "
            f"{MIN_CONTROL_FRACTION} and {MAX_CONTROL_FRACTION} first - the arm goes "
            "in the contract before it goes in the code."
        )

    existing = await assignment_for(session, order.id)
    if existing is not None:
        return existing

    salt = f"{EXPERIMENT_CONTROL_ARM}:{client.id}"
    # Keyed on the order's own id rather than the customer's reference: a
    # reference can be reused or edited, and an arm that moved when somebody
    # corrected a typo would not be immutable in the sense that matters.
    draw = _draw(salt, str(order.id))
    arm = ARM_CONTROL if draw < client.control_arm_fraction else ARM_TREATMENT

    assignment = ExperimentAssignment(
        hub_id=order.hub_id,
        client_id=client.id,
        order_id=order.id,
        experiment=EXPERIMENT_CONTROL_ARM,
        arm=arm,
        assigned_at=now or datetime.now(timezone.utc),
        salt=salt,
        control_fraction=client.control_arm_fraction,
        draw=draw,
        contracted_at=client.control_arm_contracted_at,
    )
    session.add(assignment)
    await session.flush()
    return assignment


async def assignment_for(session: AsyncSession, order_id) -> ExperimentAssignment | None:
    return await session.scalar(
        select(ExperimentAssignment).where(ExperimentAssignment.order_id == order_id)
    )


async def arm_for_order(session: AsyncSession, order_id) -> str | None:
    """Which arm, or None if this order is not in the experiment.

    None is the ordinary answer. Most orders belong to clients with no
    contracted arm, and a caller that treated None as "treatment" would sweep
    every unenrolled customer into the comparison.
    """
    assignment = await assignment_for(session, order_id)
    return assignment.arm if assignment else None


def verify_assignment(assignment: ExperimentAssignment) -> bool:
    """Recompute the draw and check it still produces the stored arm.

    `EXP-3`'s integrity monitor will want this over a whole window. Here so
    that the property - an arm is checkable, not merely asserted - has one
    definition rather than being re-derived by whoever writes that monitor.
    """
    recomputed = _draw(assignment.salt, str(assignment.order_id))
    if abs(recomputed - assignment.draw) > 1e-12:
        return False
    expected = ARM_CONTROL if recomputed < assignment.control_fraction else ARM_TREATMENT
    return expected == assignment.arm
