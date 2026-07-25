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


class ClientOrderDetailView(ClientOrderSummaryView):
    delivery_address: str | None
    delivery_contact_name: str | None
