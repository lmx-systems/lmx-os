"""The playbook's rules, as pure functions (`DEC-9` first).

A rule takes what is known about an order that needs something done and returns
a call: the action, and the reason in a sentence. No database, no clock - the
sweep (`app/playbook/sweep.py`) gathers the facts and records the call, which is
what lets every branch here be tested in a line.

**A rule may escalate.** "A person should decide this" is a legitimate answer,
and the right one wherever the call is about money, about cancelling something a
customer asked for, or about parts that are lost. Escalations are counted, so
the share a rule cannot decide is visible rather than hidden in its accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.delivery.resolution import RETURN_TO_SHOP, REDELIVER

RULE_FAILED_DELIVERY = "failed_delivery"
RULES = (RULE_FAILED_DELIVERY,)

ESCALATE = "escalate"

# The driver's "Flag an issue" reasons (app/schemas/driver_app.py,
# StopFailureReason). Strings here so the rule does not import the API schema.
SHOP_CLOSED = "SHOP_CLOSED"
ACCESS_ISSUE = "ACCESS_ISSUE"
COD_DISPUTE = "COD_DISPUTE"
PARTS_MISSING = "PARTS_MISSING"
REFUSED = "REFUSED"


@dataclass(frozen=True)
class Call:
    action: str
    reason: str


def decide_failed_delivery(
    failure_reason: str | None, *, attempt: int, failed_at: str | None, max_attempts: int
) -> Call:
    """What to do with a delivery that failed (`DEC-9`).

    `attempt` is the order's `delivery_attempts` (the first is 1); `failed_at` is
    the type of the stop that failed, `pickup` or `dropoff`, or None if no failed
    stop is on record. A failed pickup never left the shop, so it can be retried
    or escalated but never "returned".

    Never cancels. Cancelling an order a customer placed is the customer's call
    or a person's, not a rule's.
    """
    retries_left = attempt < max_attempts
    at_pickup = failed_at == "pickup"

    if failure_reason is None or failure_reason not in (
        SHOP_CLOSED, ACCESS_ISSUE, COD_DISPUTE, PARTS_MISSING, REFUSED
    ):
        return Call(ESCALATE, "No reason the playbook knows was recorded for the failure, so a person decides.")

    if failure_reason == COD_DISPUTE:
        return Call(ESCALATE, "A dispute over payment at the door is about money; a person settles it.")

    if failure_reason == REFUSED:
        if at_pickup:
            return Call(ESCALATE, "The shop would not hand the parts over; that is a conversation with the client.")
        return Call(RETURN_TO_SHOP, "The receiver refused the parts, and another attempt would be refused too.")

    if failure_reason == PARTS_MISSING:
        if not at_pickup:
            return Call(ESCALATE, "Parts that left the shop are missing from the van; a person finds them.")
        if retries_left:
            return Call(REDELIVER, "The shop did not have the parts ready; collect them on another attempt.")
        return Call(
            ESCALATE,
            f"The shop has not had the parts ready on {attempt} attempts; a person speaks to the client.",
        )

    # SHOP_CLOSED or ACCESS_ISSUE: nobody could get in.
    blocked = "closed" if failure_reason == SHOP_CLOSED else "could not be reached"
    end = "shop" if at_pickup else "drop-off"
    if retries_left:
        return Call(REDELIVER, f"The {end} {blocked} on attempt {attempt}; try again in the next window.")
    if at_pickup:
        # Nothing was collected, so there is nothing to send back - and giving up
        # on the order is cancelling it, which is not a rule's call.
        return Call(
            ESCALATE,
            f"The shop {blocked} on all {attempt} attempts and nothing was collected; a person decides.",
        )
    return Call(
        RETURN_TO_SHOP,
        f"The drop-off {blocked} on all {attempt} attempts; the parts go back to the shop.",
    )
