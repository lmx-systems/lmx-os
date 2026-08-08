"""
What a client submits from the portal, and what they get told back
(docs/LMX_LINK_PLAN.md §2.2).

Deliberately NOT the full `LMXOrder`. That object carries hub ids, commitment
ownership, assignment scope, modality and economics - internal machinery a
counter person has no business supplying and no way to know. This is the narrow
subset a person standing at a parts counter with a customer on the phone can
actually fill in, and `app/api/client_routes.py` widens it into the contract.

The design constraint that shaped every field here is §3.4's target: an order
entered in under 60 seconds, second order onward. Anything that cannot be
answered from memory in a few seconds is optional.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# §2.2 principle 4: "Deadline as a choice, not a datetime picker. 'Now / within
# the hour / today / tomorrow' maps to SLA tiers. Nobody at a counter operates a
# calendar widget."
DeadlineChoice = Literal["now", "within_the_hour", "today", "tomorrow"]

# Each choice becomes the urgency flag the SLA engine already reads from a
# payload (app/sla/engine.py's classify_tier). Routing the client's choice
# through the *existing* heuristic rather than letting them name a tier directly
# matters: LMX owns the commitment (§1.3), so a customer states urgency and we
# decide what that means. It also leaves the spec-verified engine untouched -
# E4/E5 corrected its windows against the technical design doc, and a second
# classification path would put that verification at risk.
_DEADLINE_FLAGS: dict[str, dict[str, bool]] = {
    "now": {"hot_shot": True},
    "within_the_hour": {"rush": True},
    "today": {},  # no flag - falls through to the standard T2 bucket
    "tomorrow": {"scheduled_delivery": True},
}


def deadline_payload_flags(choice: str) -> dict[str, bool]:
    return dict(_DEADLINE_FLAGS.get(choice, {}))


class ClientOrderLine(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1)


class ClientOrderBody(BaseModel):
    """One order, as typed at a counter."""

    # Where to collect. Either a shop the client has used before - which is what
    # makes the second order two taps - or a freshly typed address, which is
    # geocoded and remembered as a shop for next time.
    pickup_shop_id: str | None = None
    pickup_address: str | None = Field(default=None, max_length=255)
    pickup_contact_name: str | None = Field(default=None, max_length=120)
    pickup_contact_phone: str | None = Field(default=None, max_length=32)

    # Where it goes. §2.2 principle 2: address first, everything else optional.
    drop_address: str = Field(min_length=1, max_length=255)
    drop_contact_name: str | None = Field(default=None, max_length=120)
    drop_contact_phone: str | None = Field(default=None, max_length=32)
    access_notes: str | None = Field(default=None, max_length=500)

    deadline: DeadlineChoice = "today"

    # The client's own reference, so the order is findable by whatever they call
    # it. Generated for them when absent rather than being required - one more
    # mandatory field is one more reason not to finish the form.
    reference: str | None = Field(default=None, max_length=120)

    line_items: list[ClientOrderLine] = Field(default_factory=list)
    total_weight_units: float = Field(default=1.0, ge=0)

    # How long this order took to type, measured client-side from the first
    # keystroke. §3.4 targets under 30 seconds from the second order onward, and
    # the only honest way to know whether that holds is to measure real entries
    # by real counter staff - a stopwatch in a demo proves nothing.
    #
    # Client-supplied and therefore not trustworthy as an individual number, but
    # that is fine: it is a distribution to watch, not a per-order fact, and
    # nobody has an incentive to lie about it. Bounded anyway so a broken client
    # can't write nonsense into the logs.
    entry_seconds: int | None = Field(default=None, ge=0, le=3600)

    @model_validator(mode="after")
    def _needs_a_pickup(self) -> "ClientOrderBody":
        if self.pickup_shop_id is None and not (self.pickup_address or "").strip():
            raise ValueError("either pickup_shop_id or pickup_address is required")
        return self


class ClientOrderResult(BaseModel):
    """The confirmation.

    §2.2 principle 6: "Confirmation shows the commitment, not a spinner ... That
    is what makes it feel like a carrier."

    Two fields carry that, and they are NOT equally solid, which is why they are
    named differently:

      `collect_by` is a real commitment. It comes from the SLA hold windows in
      app/sla/engine.py, which were verified against the technical design doc
      (E4/E5 corrected them, one by 9x).

      `estimated_delivery_by` is an ESTIMATE and says so. There is no verified
      travel-time model: the real routing integration has still never made a live
      call (E1, blocked on a Google Cloud account), so this is straight-line
      distance at a placeholder average speed - the same constant the gig
      accept-gate uses. Do not put it in front of a customer as a promise until
      E1 is done, and do not let it quietly become one.
    """

    order_id: str
    reference: str
    status: str
    sla_tier: str
    collect_by: datetime
    estimated_delivery_by: datetime | None
    # Null when this client has no rate for the assigned tier. Never zero - a
    # missing price must not read as a free delivery (Order.fee_cents).
    fee_cents: int | None
    # True once both ends have coordinates. An order missing them is still
    # accepted (§2.2 principle 7 - never block on a missing field) but cannot be
    # planned until they resolve.
    dispatchable: bool


# NOTE: there is no shop schema here on purpose. `GET /client/shops` and
# `ClientShopView` already exist (app/schemas/client_auth.py) for the returns
# picker, and they are what backs §2.2 principle 3's remembered-shops behaviour
# for this form too. Adding a second shop view would have meant two endpoints
# listing the same rows with different fields.
