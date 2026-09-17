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

**Stratified by dock, not drawn independently (`EXP-2`).** The first version
hashed each order on its own, which gives the right fraction across the book and
says nothing about any single dock. Over twenty-five orders at an 8% arm, one
dock landing in control four times is an ordinary run of luck - and four
deliberately slower deliveries to one customer, possibly in the same fortnight,
is a phone call rather than a statistic. EXP-2's done-when is *"no single dock
absorbs more than its share"*, so the draw is now a permuted block: within each
run of `round(1/fraction)` orders to a dock, exactly one is control, and which
one is chosen by hash.

*The cap that was rejected.* The obvious alternative - draw independently, then
force treatment when a dock is over quota - biases the arm. The orders it moves
are not a random subset: they are the ones that came after a dock had already
been unlucky, so the control group systematically under-represents the busiest
docks and the treatment group quietly absorbs the difference. Block
randomisation costs a count and gives the guarantee without the bias.

*A partial block is still fair.* A dock with five orders in a twelve-order block
never completes it, and each of those five has a 1/12 chance of being the chosen
position - so its marginal rate is the contracted fraction exactly, the same as
everyone else. Small docks are not quietly excluded by the stratification.
"""
import hashlib
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.experiment.exclusions import (
    ReceiverExcluded,
    excluded_receiver_count,
    is_excluded,
)
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

# When an order carries no usable dock key - an address that names no place, a
# source that does not send one - it is stratified against the client as a
# whole. That keeps the overall fraction right and gives up the per-dock
# guarantee for those orders, which is the honest trade: the alternative is
# excluding them from the experiment for a data-quality reason that has nothing
# to do with the customer.
STRATUM_UNKNOWN_RECEIVER = "__no_receiver__"


def block_size(fraction: float) -> int:
    """Orders per block, so that exactly one of them is control.

    At the contracted band this is 10 to 20. Rounded rather than truncated so a
    fraction of 0.08 gives 12 (a 8.3% rate) rather than 12.5 silently becoming
    12 or 13 depending on the direction somebody happened to round.
    """
    return max(2, round(1 / fraction))


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


class AlreadyEnrolled(RuntimeError):
    """Raised when enrolling a client that already has a contracted arm.

    Its own type because the caller's right response is a conversation, not a
    retry: changing a live arm's terms mid-period is what `EXP-3`'s terms-drift
    check blocks a statement on, and doing it by accident would invalidate a
    window nobody had finished measuring.
    """


async def enrol_control_arm(
    session: AsyncSession,
    client: Client,
    *,
    fraction: float,
    contracted_at: datetime,
    replacing: bool = False,
) -> Client:
    """Turn the arm on for one client, which nothing else in this codebase does.

    The gate `EXP-1` describes - a recorded contract date rather than a flag -
    had no way to be set. The columns are nullable with no default and nothing
    in `app/` or `scripts/` wrote them, so the only way to enrol a customer was
    SQL against production. This is the switch.

    Deliberately not an endpoint. Enrolling a customer in an experiment that
    gives a slice of their orders worse service is a rare, deliberate act tied
    to a signed clause, and the date has to come off that clause. A button
    invites somebody to press it; a command requires them to have the date in
    their hand.

    `replacing` is required to change a live arm. Pooling assignments made under
    two different fractions is two experiments described as one - `EXP-3` blocks
    a statement over any window that spans the change, which is correct and
    which somebody should choose rather than discover.
    """
    if not MIN_CONTROL_FRACTION <= fraction <= MAX_CONTROL_FRACTION:
        raise ValueError(
            f"control fraction must be between {MIN_CONTROL_FRACTION} and "
            f"{MAX_CONTROL_FRACTION}; {fraction} is outside the band the roadmap "
            "sets. Below it the arm says nothing within a quarter, above it we "
            "are giving a paying customer a worse service on more orders than "
            "the measurement needs."
        )
    if client.control_arm_contracted_at is not None and not replacing:
        raise AlreadyEnrolled(
            f"client {client.id} already has a contracted arm from "
            f"{client.control_arm_contracted_at:%Y-%m-%d} at "
            f"{client.control_arm_fraction:.0%}. Pass replacing=True to change "
            "it, and expect EXP-3 to block any statement whose window spans the "
            "change - two fractions pooled is two experiments described as one."
        )
    client.control_arm_fraction = fraction
    client.control_arm_contracted_at = contracted_at
    await session.flush()
    return client


async def withdraw_control_arm(session: AsyncSession, client: Client) -> Client:
    """Take a client back out of the experiment.

    Clears the gate. Assignments already made are untouched - they are
    append-only evidence of what happened, and a customer leaving the experiment
    does not unmake the orders that were in it.
    """
    client.control_arm_fraction = None
    client.control_arm_contracted_at = None
    await session.flush()
    return client


async def assign_arm(
    session: AsyncSession,
    order: Order,
    client: Client,
    *,
    receiver_key: str | None = None,
    now: datetime | None = None,
) -> ExperimentAssignment:
    """Put this order in an arm, at intake, once.

    Raises `ArmNotContractedError` when the client has no contracted arm -
    which is every client until somebody records the date their clause was
    agreed - and `ReceiverExcluded` when the dock is one they asked us to leave
    out. Two types rather than one, because "never agreed to an experiment" and
    "agreed, and named this dock as out of scope" read very differently in a log.

    `receiver_key` is the dock, already normalised, supplied by the caller.
    This package does not learn how an order maps to a dock: that mapping lives
    in `app/identity/`, the dispatch engine reads arm labels, and importing an
    edge package here would put the core one hop from it -
    `tests/test_architecture_boundaries.py` exists to catch exactly that.

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

    stratum = receiver_key or STRATUM_UNKNOWN_RECEIVER
    if receiver_key:
        if await is_excluded(session, client_id=client.id, receiver_key=receiver_key):
            raise ReceiverExcluded(
                f"receiver {receiver_key!r} is excluded from "
                f"{EXPERIMENT_CONTROL_ARM} for client {client.id}. The order is "
                "dispatched normally and takes no part in the measurement."
            )
    elif await excluded_receiver_count(session, client_id=client.id):
        # No dock key, and this customer has asked us to leave at least one dock
        # out. We cannot show this order is not going to that dock, and the
        # promise was specific - so it does not go in the arm.
        #
        # The alternative is enrolling it and being wrong some of the time,
        # which breaks an undertaking made before the customer signed. The cost
        # is a slightly smaller experiment at accounts that use exclusions, and
        # that is the right way for this trade to fall.
        raise ReceiverExcluded(
            f"client {client.id} has excluded at least one receiver and this "
            "order carries no resolvable dock, so it cannot be shown to be "
            "outside the exclusion. It takes no part in the measurement."
        )

    size = block_size(client.control_arm_fraction)
    # Serialise the count for this dock. Two intakes racing would both read the
    # same position and could both land on the chosen one, putting two control
    # orders in a block that guarantees one - which is the single promise EXP-2
    # makes. A transaction-scoped advisory lock is the cheapest way to mean it;
    # it is released on commit or rollback without a finally block.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{client.id}:{stratum}"},
    )
    index = int(
        await session.scalar(
            select(func.count())
            .select_from(ExperimentAssignment)
            .where(
                ExperimentAssignment.client_id == client.id,
                ExperimentAssignment.receiver_key == stratum,
                ExperimentAssignment.experiment == EXPERIMENT_CONTROL_ARM,
            )
        )
        or 0
    )
    block_index = index // size
    position = index % size

    # One draw per block, not per order: the block is the unit that has exactly
    # one control slot, so the hash has to name a slot rather than a verdict.
    draw = _draw(f"{EXPERIMENT_CONTROL_ARM}:{client.id}", f"{stratum}:{block_index}")
    chosen = min(int(draw * size), size - 1)
    arm = ARM_CONTROL if position == chosen else ARM_TREATMENT

    assignment = ExperimentAssignment(
        hub_id=order.hub_id,
        client_id=client.id,
        order_id=order.id,
        experiment=EXPERIMENT_CONTROL_ARM,
        arm=arm,
        assigned_at=now or datetime.now(timezone.utc),
        salt=f"{EXPERIMENT_CONTROL_ARM}:{client.id}",
        control_fraction=client.control_arm_fraction,
        draw=draw,
        contracted_at=client.control_arm_contracted_at,
        receiver_key=stratum,
        block_size=size,
        block_index=block_index,
        position_in_block=position,
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

    Block stratification keeps this checkable, but changes what is being
    checked. The old form was a pure function of the order id; this one depends
    on the order's position in its dock's sequence, which is history. So the
    position and block are stored on the row and recomputed against - a re-roll
    still stops matching, and so does a row whose position was edited to move it
    out of the control slot.
    """
    if assignment.block_size is None or assignment.position_in_block is None:
        # Written before EXP-2. Verified the way it was made.
        recomputed = _draw(assignment.salt, str(assignment.order_id))
        if abs(recomputed - assignment.draw) > 1e-12:
            return False
        expected = (
            ARM_CONTROL
            if recomputed < assignment.control_fraction
            else ARM_TREATMENT
        )
        return expected == assignment.arm

    recomputed = _draw(
        assignment.salt, f"{assignment.receiver_key}:{assignment.block_index}"
    )
    if abs(recomputed - assignment.draw) > 1e-12:
        return False
    chosen = min(int(recomputed * assignment.block_size), assignment.block_size - 1)
    expected = (
        ARM_CONTROL if assignment.position_in_block == chosen else ARM_TREATMENT
    )
    return expected == assignment.arm
