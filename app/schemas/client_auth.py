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


class ClientOrderDetailView(ClientOrderSummaryView):
    delivery_address: str | None
    delivery_contact_name: str | None
