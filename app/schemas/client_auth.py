from datetime import datetime

from pydantic import BaseModel, Field


class ClientLoginBody(BaseModel):
    email: str
    password: str


class ClientAuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ClientProfileView(BaseModel):
    client_id: str
    # The company name, unchanged from before multi-user - what the portal
    # shows as the account it belongs to.
    name: str
    # The signed-in user (multi-user, docs/ROADMAP.md C4) - who is looking,
    # as opposed to which company. `email` replaces the old `portal_email`
    # (which was the company's single shared login, a concept that no
    # longer exists).
    email: str
    user_name: str
    role: str


class ClientShopView(BaseModel):
    """One of the caller company's shops - drives the portal's flag-cores-ready
    picker (docs/ROADMAP.md W1 slice 4) and the New Order form's remembered
    pickup list (LMX_LINK_PLAN.md §2.2 principle 3).

    Includes shops auto-created from a typed pickup address, which is the point
    of that principle: an address typed once never has to be typed again.
    """

    shop_id: str
    name: str
    external_ref: str | None
    # Added for the order form - the name of an auto-created shop is the typed
    # address, but a registered one may be called "Midtown" and a counter person
    # needs to see where that actually is before picking it.
    address: str | None = None


class ClientUserView(BaseModel):
    """One user at the caller's client - the shape /client/users returns.
    Never includes the password hash."""

    client_user_id: str
    email: str
    name: str
    role: str
    is_active: bool
    created_at: str


class ClientUserCreateBody(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=255)
    role: str = Field(default="member")


class ClientUserUpdateBody(BaseModel):
    """All fields optional - a PATCH that sets only what it wants to
    change. role restricted to the real two; a new password is bounded the
    same as at creation."""

    role: str | None = None
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=255)


class ClientRateView(BaseModel):
    sla_tier: str
    rate_per_drop_cents: int


class ClientOrderSummaryView(BaseModel):
    order_id: str
    external_order_ref: str
    sla_tier: str | None
    status: str
    shop_name: str | None
    requested_at: str
    delivered_at: str | None
    fee_cents: int | None
    # Failed-delivery visibility (docs/ROADMAP.md R5) - so a client can see
    # a delivery failed, why, and that a retry is under way, rather than an
    # order silently stuck in `delivery_failed`. failure_reason is null
    # unless the current status reflects a failure.
    failure_reason: str | None = None
    delivery_attempts: int = 1
    # What we committed to collecting by. `Order.hold_deadline` - the same value the
    # confirmation screen showed as "we'll collect by", so a counter person searching for
    # an order sees the promise they read out to their customer.
    collect_by: str | None = None
    # When we now expect it to arrive. **The route-aware number** once the order is on a
    # driver's route (`Stop.eta`, walked along the sequence they will actually drive),
    # falling back to a straight-line estimate from the collection commitment before then.
    #
    # Named `estimated_...` in both cases and never `delivery_by`: collection is a
    # commitment and arrival is not, and the whole point of surfacing one number here is
    # that a driver, a recipient and a counter person are never shown arrival times
    # derived three different ways.
    estimated_delivery_by: str | None = None
    # **The commitment that carries money**, from `app/sla/commitment.py` - the same
    # function `app/billing/credits.py` assesses a breach against. It was previously
    # invisible: a client could be owed an automatic credit and had no way to see the
    # target it was measured from. Null when no term is on file for their tier and we made
    # no explicit promise, because there is genuinely nothing owed to state.
    promised_delivery_by: str | None = None
    # When we actually collected, from the pickup stop. This is what makes `collect_by`
    # checkable rather than merely asserted - and it will show that `collect_by` runs
    # optimistic, since that field is the batch-release moment rather than an arrival
    # (see app/sla/commitment.py's closing note, and E11).
    collected_at: str | None = None


class TermsAcceptanceView(BaseModel):
    """Where this company stands on the current terms (docs/ROADMAP.md L8).

    `can_accept` is on the payload rather than left to the portal to infer from a role
    string, so the button's existence and the endpoint's authorisation are decided by
    the same fact. A portal that guessed would eventually offer a counter person a
    button that 403s.
    """

    current_version: str
    accepted_version: str | None
    accepted_at: datetime | None
    # True when the company must accept before sending more orders.
    acceptance_required: bool
    # Whether *this* signed-in user may do it. Admin only - binding the company to a
    # contract is not the same act as reading an order.
    can_accept: bool
    terms_path: str
    privacy_path: str


class ClientOrderPage(BaseModel):
    """One page of a client's orders.

    An envelope rather than a bare list, because `GET /client/orders` used to return
    **every order the client had ever placed** with no limit - a full scan that grows
    forever, on the screen a counter person would use all day. A page is only honest if
    the reader can tell it is one, so `total` comes back with it.
    """

    items: list[ClientOrderSummaryView]
    total: int
    limit: int
    offset: int


class DeliveryRatingView(BaseModel):
    """What the recipient said about this delivery (docs/ROADMAP.md F13).

    On the detail view rather than the list. A column of scores invites reading the
    list as a scoreboard, and an average per driver or per shop is `I4`'s analytics
    work with the framing conversation `W4` asks for - not something that should fall
    out of showing a client one order.
    """

    score: int
    comment: str | None
    submitted_at: datetime


class ClientOrderDetailView(ClientOrderSummaryView):
    delivery_address: str | None
    delivery_contact_name: str | None
    # What the recipient thought, when they told us. Null when they haven't.
    rating: DeliveryRatingView | None = None


class PerformanceRateView(BaseModel):
    """One on-time rate, with the arithmetic visible.

    `numerator`/`denominator` travel with the percentage because "100% on time" means
    something entirely different at n=1 and n=400, and `is_thin` tells the reader which
    they are looking at instead of leaving them to work it out.
    """

    name: str
    numerator: int
    denominator: int
    percentage: float | None
    is_thin: bool
    not_measured: str | None


class ClientPerformanceView(BaseModel):
    """How LMX has actually performed for this client (docs/ROADMAP.md F7).

    A distributor on per-drop pricing has given up their own view of a fleet they used to
    run, so this is the answer to "how are you doing for me" - computed from the same
    function that produces our internal report and that billing credits a breach against.

    Their own figures only. There is deliberately no benchmark against other clients:
    that would tell a distributor something about our other customers.
    """

    window_days: int
    generated_at: datetime
    delivered_count: int
    hit_rates: list[PerformanceRateView]
