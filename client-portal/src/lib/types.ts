// Mirrors app/schemas/client_auth.py's response models exactly.

export interface ClientAuthToken {
  access_token: string
  token_type: string
}

export interface ClientProfileView {
  client_id: string
  // The company / account name.
  name: string
  // The signed-in user (multi-user client accounts, docs/ROADMAP.md C4).
  email: string
  user_name: string
  role: string
}

export interface ClientUserView {
  client_user_id: string
  email: string
  name: string
  role: string
  is_active: boolean
  created_at: string
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
  // Failed-delivery visibility (docs/ROADMAP.md R5).
  failure_reason: string | null
  delivery_attempts: number
}

export interface ClientOrderDetailView extends ClientOrderSummaryView {
  delivery_address: string | null
  delivery_contact_name: string | null
}

// Mirrors app/schemas/billing.py's response models exactly - shared
// as-is between the admin generation endpoint and this portal's
// read-only view, so the shape is identical either way.
export interface InvoiceSummaryView {
  invoice_id: string
  invoice_number: number
  period_start: string
  period_end: string
  generated_at: string
  total_cents: number
  order_count: number
}

export interface InvoiceLineItem {
  order_id: string
  external_order_ref: string
  shop_name: string | null
  sla_tier: string | null
  delivered_at: string | null
  fee_cents: number
}

export interface InvoiceDetailView extends InvoiceSummaryView {
  line_items: InvoiceLineItem[]
}
