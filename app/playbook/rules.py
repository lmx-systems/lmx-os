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
RULE_UNPLACED = "unplaced"
RULES = (RULE_FAILED_DELIVERY, RULE_UNPLACED)
# Rules that can act once their mode allows. The others only recommend: acting
# is built for a rule when its record says it would act well.
ACTING_RULES = (RULE_FAILED_DELIVERY,)

ESCALATE = "escalate"
# Offer the order to the driver the call names (`Call.target`), as a person's
# assign does (app/orders/assignment.py).
OFFER = "offer"
# The same, with the placeholder bonus on the offer (`settings.offer_bonus_cents`).
OFFER_WITH_BONUS = "offer_bonus"

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
    # Whom the action is for, when it is for somebody: a driver's id.
    target: str | None = None


@dataclass(frozen=True)
class Candidate:
    """A driver who could be offered an order nobody has: switched on, papers in
    order, idle with no route, and room on the van - every check a person's
    assign makes (app/orders/assignment.py). Only position is left to judge."""

    driver_id: str
    name: str
    # Straight-line miles from their last reported position to the shop; None
    # when they have never reported one.
    miles: float | None
    # Their app has not reported recently. The cycle never offers to such a
    # driver - they may not see it - which is usually why the order is unplaced.
    position_stale: bool
    # They were offered this order before and declined it or let it lapse.
    turned_it_down: bool = False


@dataclass(frozen=True)
class OfferHistory:
    """What became of this order's earlier offers."""

    # Offers declined or left to lapse.
    turned_down: int = 0
    # Whether one of them already carried a bonus.
    bonus_tried: bool = False


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


def decide_unplaced(
    candidates: list[Candidate],
    *,
    hub_closed: bool,
    history: OfferHistory = OfferHistory(),
    bonus_cents: int = 0,
) -> Call:
    """What to do with an order nobody has (`DEC-10`), one step up each time.

    1. **Never turned down:** offer it to the nearest free driver whose app is
       reporting; if every free driver's app has gone quiet, the nearest of those,
       whom a person would phone first.
    2. **Declined or left to lapse:** offer it again with the placeholder bonus,
       to the nearest free driver who has not turned it down, or failing that the
       nearest who has - a bonus is what might change their answer.
    3. **Turned down with a bonus too**, or nobody free, or the hub closed: a
       person, who brings in cover or tells the client the time has slipped.

    Nearest by a straight line to the shop, which is what a dispatcher reading
    the map does.
    """
    if hub_closed:
        return Call(ESCALATE, "The hub is closed today, so nothing is dispatched; a person decides what to tell the client.")
    if history.bonus_tried:
        return Call(
            ESCALATE,
            f"Turned down {history.turned_down} times, once with a bonus; a person brings in cover or tells the client.",
        )
    if not candidates:
        return Call(
            ESCALATE,
            "No driver at the hub is free with room on the van; a person brings in cover or tells the client.",
        )

    def by_distance(c: Candidate) -> tuple[bool, bool, float, str]:
        return (c.position_stale, c.miles is None, c.miles if c.miles is not None else 0.0, c.driver_id)

    if history.turned_down:
        fresh = [c for c in candidates if not c.turned_it_down]
        best = sorted(fresh or candidates, key=by_distance)[0]
        dollars = f"${bonus_cents / 100:.2f}"
        who = f"{best.name}, who turned it down before," if best.turned_it_down else best.name
        quiet = " Their app has gone quiet - phone them first." if best.position_stale else ""
        return Call(
            OFFER_WITH_BONUS,
            f"Turned down {history.turned_down} {'time' if history.turned_down == 1 else 'times'}; "
            f"offer it to {who} with a {dollars} bonus.{quiet}",
            target=best.driver_id,
        )

    best = sorted(candidates, key=by_distance)[0]
    if not best.position_stale:
        where = f"{best.miles:.1f} miles from the shop" if best.miles is not None else "position unknown"
        return Call(OFFER, f"{best.name} is free, {where}, with room on the van.", target=best.driver_id)
    where = f"last seen {best.miles:.1f} miles from the shop" if best.miles is not None else "never reported a position"
    return Call(
        OFFER,
        f"{best.name} is free and {where}, but their app has gone quiet - phone them before offering.",
        target=best.driver_id,
    )
