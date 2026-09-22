// Mirrors the pydantic response models in app/schemas/*.py on the backend.
// Kept as plain interfaces (no runtime validation) - this is an internal
// tool reading data the backend already validated, not an untrusted input
// boundary. If that assumption stops holding (e.g. this dashboard starts
// taking write-heavy user input), reach for zod instead of hand-rolled types.

export interface DriverState {
  driver_id: string
  hub_id: string
  // 'offered' = has a pending job offer (app/optimizer/service.py) - kept
  // out of the assignable pool until the driver accepts/declines/it expires.
  status: 'off_shift' | 'available' | 'offered' | 'en_route' | 'on_break'
  capacity_units: number
  load_units: number
  current_route_id: string | null
  // Populated by GET /fleet/{hub_id}/drivers via a Postgres join - null if
  // the Redis fleet-state entry has no matching Driver row (shouldn't
  // happen in practice, but the backend doesn't assume it can't).
  name: string | null
  // Last reported position (docs/ROADMAP.md F1). All three are null together
  // for a driver whose app has never reported one - which is also exactly why
  // the optimizer would skip them, so the map treats it as a real state to
  // surface rather than a row to hide.
  lat: number | null
  lng: number | null
  location_recorded_at: string | null
}

export interface HeldOrderView {
  order_id: string
  shop_lat: number
  shop_lng: number
  sla_tier: string
  hold_deadline: string
  held_since: string
  shop_name: string
  // Computed fresh per request against the other rows in the same
  // response - see app/api/routes.py's list_held_orders.
  cluster_mate_ids: string[]
}

export interface OrderStatusSummary {
  hub_id: string
  counts: Record<string, number>
}

export interface RouteAssignment {
  driver_id: string
  stop_ids: string[]
}

export interface OptimizationResult {
  hub_id: string
  assignments: RouteAssignment[]
  unassigned_stop_ids: string[]
  engine: string
  duration_seconds: number
  over_budget: boolean
}

// The most recently completed Dispatch Optimizer cycle for a hub, however
// it was triggered (manual or automatic) - GET /optimizer/{hub_id}/last-cycle.
// Unlike OptimizationResult (only ever returned to whoever called
// run-cycle), this is queryable by anyone, which is what lets the KPI
// strip reflect automatic cycles it didn't trigger itself.
export interface LastCycleSnapshot {
  hub_id: string
  at: string
  engine: string
  duration_seconds: number
  assigned_count: number
  unassigned_count: number
  over_budget: boolean
}

export interface ProposedRuleSummary {
  proposed_rule_id: string
  shop_id: string
  rule_type: string
  proposed_change: Record<string, number>
  confidence: number
  supporting_annotation_count: number
}

export interface NightlyJobResult {
  hub_id: string
  proposals_created: ProposedRuleSummary[]
}

// Phase 8 minimal client onboarding - mirrors app/schemas/admin.py.
export interface ShopOnboardingInput {
  name: string
  address: string
  lat: number
  lng: number
  external_ref: string
  phone?: string
}

export interface RateOnboardingInput {
  sla_tier: string
  rate_per_drop_cents: number
}

export interface ClientOnboardingBody {
  hub_id: string
  name: string
  pos_system: string
  shops: ShopOnboardingInput[]
  rates: RateOnboardingInput[]
  portal_email: string
  portal_password: string
}

export interface ClientOnboardingResult {
  client_id: string
  shop_ids: string[]
}

export interface HubSummary {
  hub_id: string
  name: string
}

export interface OpsAuthToken {
  access_token: string
  token_type: string
}

export interface OpsProfileView {
  ops_user_id: string
  email: string
  name: string
  role: 'admin' | 'viewer'
}

// --- UI-local types below - no backend equivalent, not response mirrors ---

export interface RunLogEntry {
  at: number
  kind: 'optimizer' | 'learning_loop'
  summary: string
}

// Orchestrator-editable urgency rules (docs/ROADMAP.md W6) - mirrors
// app/schemas/admin.py's UrgencyRuleView / UrgencyRuleBody.
export interface UrgencyRuleView {
  rule_id: string
  match_key: string
  match_value: string
  tier: string
  enabled: boolean
}

export interface UrgencyRuleBody {
  match_key: string
  match_value: string
  tier: string
}

// Learning-Loop proposals awaiting review (docs/ROADMAP.md I2) - mirrors
// app/schemas/admin.py's ProposedRuleView / ProposedRuleApprovalResult.
export interface ProposedRuleView {
  rule_id: string
  rule_type: string
  scope: Record<string, unknown>
  proposed_change: Record<string, unknown>
  confidence: number
  supporting_annotation_count: number
  status: string
  created_at: string
}

export interface ProposedRuleApprovalResult {
  proposed_rule_id: string
  status: string
  active_rule_id: string | null
}

// Public client signup review (docs/LMX_LINK_PLAN.md). Anyone can apply;
// nobody dispatches an LMX van until someone here approves them.
export interface PendingSignupView {
  client_id: string
  company_name: string
  service_area: string | null
  contact_name: string | null
  contact_email: string | null
  contact_phone: string | null
  terms_version: string | null
  terms_accepted_at: string | null
  signup_status: string
  submitted_at: string
  hub_id: string
}

export interface SignupDecisionResult {
  client_id: string
  signup_status: string
  rates_created: number | null
}

// Driver compliance document review (docs/ROADMAP.md R4).
export interface PendingDriverDocumentView {
  document_id: string
  driver_id: string
  driver_name: string
  doc_type: string
  // What the DRIVER says. Shown next to an empty field for what the document
  // actually says, because the review is that comparison.
  claimed_expires_at: string
  file_url: string | null
  review_status: string
  uploaded_at: string
}

export interface DriverDocumentReviewBody {
  decision: 'verify' | 'reject'
  // Required to verify, and deliberately NOT defaulted to claimed_expires_at -
  // see DriverDocumentsPanel on why prefilling would make this a rubber stamp.
  verified_expires_at?: string
  rejection_reason?: string
}

export interface DriverDocumentReviewResult {
  document_id: string
  doc_type: string
  review_status: string
  verified_expires_at: string | null
  driver_can_go_on_shift: boolean
  outstanding_problems: string[]
}

// The measurement endpoints (docs/ROADMAP.md I4, L17 §3.4). Both have existed with no
// consumer - the data layer was built twice and nothing surfaced it.
export interface MeasurementView {
  name: string
  target: string
  unit: string
  median: number | null
  p90: number | null
  sample_size: number
  not_measured: string | null
}

export interface RateView {
  name: string
  target: string
  numerator: number
  denominator: number
  percentage: number | null
  // True when the denominator is too small for the percentage to mean much. Shown as a
  // caveat rather than hidden, because "100%" on one delivery reads as a result.
  is_thin: boolean
  not_measured: string | null
}

export interface OperationsScorecard {
  generated_at: string
  window_days: number
  window_start: string
  measurements: MeasurementView[]
  rates: RateView[]
}

export interface LinkScorecard {
  generated_at: string
  measurements: MeasurementView[]
}

// What the service-level credits are costing (docs/ROADMAP.md W3, E11). `accruing` is the
// half that matters: delivered work not yet invoiced that would breach if it were.
export interface TierExposureView {
  sla_tier: string
  // Null when clients disagree about it - the report declines to pick one.
  credit_percent: number | null
  credit_cents: number
  breach_count: number
  delivered_count: number
  breach_rate_percent: number | null
}

export interface ClientExposureView {
  client_id: string
  client_name: string
  issued_cents: number
  accruing_cents: number
  total_cents: number
}

export interface CreditExposure {
  generated_at: string
  window_days: number
  window_start: string
  issued_cents: number
  accruing_cents: number
  total_cents: number
  by_tier: TierExposureView[]
  by_client: ClientExposureView[]
  unassessable_orders: number
  unpriced_orders: number
}

/** One thing worth a dispatcher's attention (docs/ROADMAP_1.5.md CON-4). */
export interface ExceptionItem {
  kind: 'flagged_by_driver' | 'delivery_failed' | 'past_promise' | 'released_but_unplaced'
  order_id: string
  client_id: string | null
  external_ref: string
  sla_tier: string | null
  /**
   * Minutes, not a score. The model that would rank these properly is `M2` -
   * P(a consequence | this order is late) - and it needs hundreds of observed
   * consequences that do not exist yet, so the UI must not dress this up as a
   * prediction it is standing in for.
   */
  minutes_waiting: number
  promised_at: string | null
  detail: string
  next_action: string
}

export interface ExceptionQueue {
  generated_at: string
  counts: Record<string, number>
  worst_wait_minutes: number
  items: ExceptionItem[]
}

/** One thing the decision log says, and the row it says it in (AGT-4). */
export interface DecisionFact {
  at: string
  statement: string
  snapshot_id: string
  engine: string
}

/**
 * Why an order is where it is (docs/ROADMAP_1.5.md AGT-4).
 *
 * `is_explained: false` is an answer, not an error. The record being silent
 * about an order is a different thing from the order having no reason, and the
 * console must show the difference rather than filling the gap with something
 * that sounds right.
 */
export interface OrderExplanation {
  order_id: string
  is_explained: boolean
  facts: DecisionFact[]
  unexplained: string | null
}

/** One reason an override may carry (docs/ROADMAP_1.5.md CON-2). */
export interface OverrideReasonOption {
  code: string
  label: string
  note_required: boolean
}

/**
 * A recorded override (CON-2, CON-3).
 *
 * `contradicted_the_system` comes from the server rather than being computed
 * here: an override of a decision nobody recorded contradicts nothing, and a
 * client doing the subtraction itself would read the missing side as a mismatch.
 */
export interface OverrideResult {
  id: string
  order_id: string
  overridden_at: string
  action: string
  reason_code: string
  reason_label: string
  note: string | null
  by: string
  system_action: string | null
  system_reason: string | null
  system_decision_known: boolean
  contradicted_the_system: boolean
  order_status_before: string
  order_status_after: string
}

/** A late delivery nobody has judged yet (docs/ROADMAP_1.5.md REC-2). */
export interface LateOrder {
  order_id: string
  external_ref: string
  client_id: string | null
  delivered_at: string | null
  minutes_late: number | null
  sla_tier: string | null
}

/** One of the six consequences the brief names. */
export interface ConsequenceOption {
  code: string
  label: string
}

/**
 * A question the linkage detectors raised (REC-4).
 *
 * A flag is a question, not a finding — "a return has been waiting at this dock
 * since Tuesday", not an accusation. A dispatcher who reads them as accusations
 * stops reading them, which is why `detail` is rendered rather than `kind`.
 */
export interface LinkageFlag {
  id: string
  kind: string
  subjects: Record<string, unknown>
  detail: string
  detected_at: string
  resolved_at: string | null
  resolution_note: string | null
}

/** Whether one of the record's writers is producing anything (REC-1..REC-4). */
export interface WriterHealth {
  name: string
  rows_in_window: number
  last_written_at: string | null
  note: string
}

/**
 * The record layer reported on itself (docs/ROADMAP_1.5.md REC-1..REC-4).
 *
 * Not a KPI and not the savings statement — those are claims about the
 * business. This answers "is the record being written", which is a question
 * about us, and a writer that silently stops looks exactly like a quiet week.
 */
export interface RecordHealth {
  window_days: number
  on_time_percentage: number | null
  on_time_numerator: number
  on_time_denominator: number
  on_time_interval: [number, number] | null
  on_time_not_measured: string | null
  on_time_is_thin: boolean
  decisions_recorded: number
  outcomes_recorded: number
  outcomes_linked_to_a_decision: number
  decision_link_percentage: number | null
  open_flags: number
  // Whose dwell observation each dock has, if any (IDN-4). Three counts
  // rather than a percentage: an inherited figure is a different kind of
  // answer from one of our own and should never be summed with it.
  dwell_docks: number
  dwell_from_our_own: number
  dwell_inherited: number
  dwell_unknown: number
  dwell_thin: number
  // DRV-1's fence against the driver's own taps. The radius is 75m and 75
  // was a guess; this is what keeps it from staying one.
  geofence_coverage: number | null
  geofence_lead_p50_seconds: number | null
  geofence_comparable_stops: number
  writers: WriterHealth[]
  labels: {
    observed_consequences: number
    silences: number
    labelled_total: number
    by_type: Record<string, number>
    required_range_for_m2: [number, number]
    at_band_minimum: boolean
    at_band_target: boolean
    shortfall_to_minimum: number
    shortfall_to_target: number
  }
}

/**
 * A pair of docks somebody is being asked to judge (docs/ROADMAP_1.5.md IDN-2).
 *
 * Both addresses are carried, not just ids. The reviewer's question is "are
 * these the same place", and two UUIDs cannot be answered.
 */
export interface MergeProposal {
  id: string
  status: string
  reason: string
  source_location_id: string
  source_address: string
  target_location_id: string
  target_address: string
  proposed_at: string
  decided_at: string | null
  decision_source: string | null
}

/** A dock nobody has classified yet (docs/ROADMAP_1.5.md IDN-3). */
export interface UnlabelledDock {
  location_id: string
  address: string
  shop_names: string[]
  inherited_dwell_sample_count: number | null
  inherited_dwell_p50_seconds: number | null
}

/** How far IDN-3 is from its done-when. */
export interface ClassificationCoverage {
  shops: number
  classified: number
  without_dock: number
  unlabelled: number
  unlabelled_percent: number | null
  meets_target: boolean
}

/**
 * The seven classes PRD-1 groups by. Not free text: an eighth appearing would
 * split a group without anyone noticing.
 */
export const NODE_CLASSES = [
  { code: 'shop', label: 'Repair shop' },
  { code: 'parts_store', label: 'Parts store' },
  { code: 'dealer', label: 'Dealership' },
  { code: 'body_shop', label: 'Body shop' },
  { code: 'warehouse', label: 'Warehouse' },
  { code: 'transfer', label: 'Transfer / terminal' },
  { code: 'municipal', label: 'Municipal fleet' },
] as const

/**
 * How much work is waiting for a person (docs/ROADMAP_1.5.md CON-1).
 *
 * Counts only, in one call. The panels behind the "To record" tab load their
 * own detail when somebody opens it — before this they all loaded on every page
 * view whether or not anybody looked.
 */
export interface AttentionCounts {
  late_orders: number
  merge_proposals: number
  unlabelled_docks: number
}

/** One order as a dispatcher on the phone needs to see it (CON-1). */
export interface OrderLookupRow {
  order_id: string
  external_ref: string
  shop_name: string | null
  status: string
  sla_tier: string | null
  requested_at: string
  promised_at: string | null
  delivered_at: string | null
  minutes_late: number | null
}

export interface OrderLookupPage {
  items: OrderLookupRow[]
  total: number
  limit: number
}

/**
 * Provision a driver (docs/ROADMAP_AUDIT_2026-09.md).
 *
 * `vehicle_capacity_units` is required here though the column defaults to 1:
 * the optimizer's capacity check reads it, so a driver provisioned without
 * thinking about it gets one order at a time — the safe direction and the wrong
 * answer. Asking makes it a decision.
 */
export interface DriverOnboardingBody {
  hub_id: string
  name: string
  phone: string
  vehicle_capacity_units: number
  employment_type: string
  vehicle_type?: string | null
  plate_number?: string | null
  hourly_rate_cents?: number | null
}

export interface DriverOnboardingResult {
  driver_id: string
  name: string
  phone: string
  employment_type: string
  vehicle_capacity_units: number
  hourly_rate_is_placeholder: boolean
}

export const EMPLOYMENT_TYPES = [
  { code: 'w2', label: 'Employee (W2)' },
  { code: 'contractor_1099', label: 'Contractor (1099)' },
  { code: 'gig', label: 'Gig' },
] as const

export const VEHICLE_TYPES = ['car', 'van', 'bike'] as const

/**
 * One hub's settings, and which overtime rule its drivers get
 * (docs/ROADMAP_AUDIT_2026-09.md).
 *
 * `overtime_rule` is reported because "no state set" and "a state with no rule
 * registered" produce identical payroll, and only one of them is somebody's
 * oversight.
 */
export interface HubSettings {
  id: string
  name: string
  timezone: string
  lat: number
  lng: number
  state_code: string | null
  active: boolean
  overtime_rule: string
}

export const US_STATE_CODES = (
  'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN ' +
  'MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA ' +
  'WV WI WY'
).split(' ')
