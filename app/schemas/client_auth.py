from pydantic import BaseModel


class ClientLoginBody(BaseModel):
    email: str
    password: str


class ClientAuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ClientProfileView(BaseModel):
    client_id: str
    name: str
    portal_email: str


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
