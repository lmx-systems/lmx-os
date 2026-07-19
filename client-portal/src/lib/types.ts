// Mirrors app/schemas/client_auth.py's response models exactly.

export interface ClientAuthToken {
  access_token: string
  token_type: string
}

export interface ClientProfileView {
  client_id: string
  name: string
  portal_email: string
}

export interface ClientOrderSummaryView {
  order_id: string
  external_order_ref: string
  sla_tier: string | null
  status: string
  shop_name: string | null
  requested_at: string
  delivered_at: string | null
  fee_cents: number | null
}

export interface ClientOrderDetailView extends ClientOrderSummaryView {
  delivery_address: string | null
  delivery_contact_name: string | null
}
