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

// Mirrors app/schemas/returns.py's ReturnItemView (docs/ROADMAP.md W1).
export interface ReturnItemView {
  return_id: string
  // Empty for a standalone (shop-flagged) return with no originating order.
  origin_order_ref: string
  shop_name: string | null
  manifest: string
  status: string
  created_at: string
  // Hours since the return appeared - drives the "age" column so a stale
  // core is obvious on the counter (docs/ROADMAP.md W1 slice 4).
  age_hours: number
  collected_at: string | null
  returned_at: string | null
}

// Mirrors app/schemas/client_auth.py's ClientShopView - the flag-cores
// picker's options.
export interface ClientShopView {
  shop_id: string
  name: string
  external_ref: string | null
  // Shops auto-created from a typed pickup address are named after that
  // address; a registered one may be called "Midtown", so the counter needs
  // to see where it actually is before picking it.
  address: string | null
}

// Placing an order (LMX_LINK_PLAN.md §2.2). Mirrors
// app/schemas/client_order.py.
export type DeadlineChoice = 'now' | 'within_the_hour' | 'today' | 'tomorrow'

export interface ClientOrderBody {
  pickup_shop_id?: string | null
  pickup_address?: string | null
  pickup_contact_name?: string | null
  pickup_contact_phone?: string | null
  drop_address: string
  drop_contact_name?: string | null
  drop_contact_phone?: string | null
  access_notes?: string | null
  deadline: DeadlineChoice
  reference?: string | null
  line_items?: { description: string; quantity: number }[]
  total_weight_units?: number
  // How long this order took to enter, measured from first keystroke.
  // §3.4 targets under 30 seconds from the second order onward, and the
  // only honest way to know is to measure real entries.
  entry_seconds?: number | null
}

export interface ClientOrderResult {
  order_id: string
  reference: string
  status: string
  sla_tier: string
  collect_by: string
  // An ESTIMATE, not a commitment - see app/schemas/client_order.py. Never
  // show it with the same certainty as collect_by.
  estimated_delivery_by: string | null
  fee_cents: number | null
  dispatchable: boolean
}

// Public signup (LMX_LINK_PLAN.md). Mirrors app/schemas/signup.py.
export interface ClientSignupBody {
  company_name: string
  contact_name: string
  contact_email: string
  contact_phone?: string | null
  service_area: string
  password: string
  terms_version: string
  accepted_terms: boolean
}

export interface ClientSignupResult {
  status: string
  message: string
}

// Bulk paste (§2.2 principle 5). Mirrors app/schemas/client_order.py.
export interface ClientOrderBatchRow {
  drop_address: string
  reference?: string
  drop_contact_name?: string
}

export interface ClientOrderBatchBody {
  pickup_shop_id?: string | null
  pickup_address?: string | null
  deadline: DeadlineChoice
  rows: ClientOrderBatchRow[]
  entry_seconds?: number | null
}

export interface ClientOrderBatchRowResult {
  index: number
  drop_address: string
  // Exactly one of these is set. Partial success is the normal case.
  order: ClientOrderResult | null
  error: string | null
}

export interface ClientOrderBatchResult {
  accepted: number
  failed: number
  results: ClientOrderBatchRowResult[]
}

// The public tracking page's payload (docs/ROADMAP.md F3, app/schemas/tracking.py).
//
// This mirrors a privacy boundary rather than a convenience DTO: the backend
// schema is the exhaustive list of what an unauthenticated caller holding a
// tracking URL can learn. No driver name or phone, no other stops, no client
// identity, no internal ids.
export interface DriverPositionView {
  lat: number
  lng: number
  // Shown as "updated Ns ago". A dot with no timestamp reads as live even when
  // it's stale, which is worse than showing no dot at all.
  recorded_at: string
}

export interface TrackingView {
  status: string
  // The two strings a person actually reads. `status` is for our own logic -
  // "en_route_drop" is a sink's vocabulary, not a customer's.
  headline: string
  detail: string
  destination_hint: string | null
  estimated_arrival: string | null
  delivered_at: string | null
  // Present only while this recipient's drop is the driver's CURRENT stop.
  driver_position: DriverPositionView | null
  // False on a finished delivery, so a forgotten open tab stops polling.
  is_live: boolean
}
