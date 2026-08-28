/**
 * Mirrors app/schemas/driver_auth.py and app/schemas/driver_app.py on the
 * backend (LMX OS). Keep these two in sync by hand for now - no shared
 * schema-codegen step exists yet.
 */

export interface RequestOtpResult {
  ok: boolean;
  debug_code: string | null;
}

export interface AuthToken {
  access_token: string;
  token_type: string;
}

export interface DriverProfile {
  driver_id: string;
  hub_id: string;
  name: string;
  phone: string;
  status: string;
  vehicle_type: string | null;
  plate_number: string | null;
  delivery_zone: string | null;
  payment_bank_last4: string | null;
  // Real, computed from completed Route rows - no star rating anywhere in
  // this app (no rating-submission system exists, so there's nothing real
  // to show; see app/schemas/driver_app.py's DriverProfileView docstring).
  trip_count: number;
}

export type DocType = 'license' | 'insurance';

export interface DriverDocument {
  doc_type: DocType;
  // What the DRIVER typed. Recorded as a claim and read by no gate - see
  // app/models/driver_document.py. Kept visible so a rejection is legible
  // ("you told us March, the card says January").
  claimed_expires_at: string;
  // What an LMX reviewer read off the document. Null until reviewed, and the
  // only date that opens the go-online gate.
  verified_expires_at: string | null;
  review_status: 'pending' | 'verified' | 'rejected';
  rejection_reason: string | null;
  file_url: string | null;
  // Whether this document currently supports going on shift. Computed by the
  // server so the app cannot arrive at a different answer than the gate does.
  is_usable: boolean;
}

// Why the "go online" toggle is refusing, in the driver's language. Same
// computation the gate itself uses, so the two can never disagree.
export interface DriverComplianceProblem {
  doc_type: string;
  // missing | awaiting_review | rejected | expired - branch on this rather than
  // parsing the sentence.
  reason: 'missing' | 'awaiting_review' | 'rejected' | 'expired';
  detail: string;
}

export interface DriverCompliance {
  can_go_on_shift: boolean;
  problems: DriverComplianceProblem[];
}

export interface OfferStopSummary {
  order_id: string;
  lat: number;
  lng: number;
  sla_tier: string;
  shop_name: string;
}

export interface JobOffer {
  offer_id: string;
  hub_id: string;
  expires_at: string;
  stops: OfferStopSummary[];
  // Real per-delivery pay estimate (docs/ROADMAP.md A11) - only set for a
  // gig-classified driver; null for w2/1099, paid hourly/monthly instead.
  estimated_pay_cents: number | null;
}

export type StopType = 'pickup' | 'dropoff';
export type StopStatus = 'pending' | 'en_route' | 'arrived' | 'completed' | 'failed';

export interface Stop {
  stop_id: string;
  sequence: number;
  stop_type: StopType;
  status: StopStatus;
  lat: number;
  lng: number;
  shop_name: string | null;
  address: string | null;
  contact_name: string | null;
  contact_phone: string | null;
  notes: string | null;
  parcel_count: number;
  scanned_count: number;
  order_ids: string[];
  eta: string | null;
  completed_at: string | null;
  left_at: string | null;
  failure_reason: FlagReasonCode | null;
  flag_note: string | null;
  // What proof THIS stop needs (docs/LMX_LINK_PLAN.md §1.2). Sent with the stop
  // rather than discovered on rejection: a driver who finds out at the door that
  // this client wanted four photos has already put the box down.
  proof: StopProofRequirement | null;
  // Money owed here, if any. Empty for the overwhelming majority of stops.
  cod: CodObligation[];
}

export interface StopProofRequirement {
  photo_count_required: number;
  // The whole reason a count above one exists - "four photos" without saying of
  // what produces four pictures of a doorstep.
  photo_subjects: string[];
  signature_required: boolean;
}

export interface CodObligation {
  order_id: string;
  amount_due_cents: number;
  // Both a collection and a dispute settle it: the rule is "keep moving", so a
  // driver is not held at a door by an unresolved dispute.
  settled: boolean;
  outcome: 'collected' | 'disputed' | null;
}

export type CodMethod = 'cash' | 'check';

// Mirrors app/schemas/driver_app.py's StopFailureReason enum.
export type FlagReasonCode = 'SHOP_CLOSED' | 'ACCESS_ISSUE' | 'COD_DISPUTE' | 'PARTS_MISSING' | 'REFUSED';

export interface Route {
  route_id: string;
  status: string;
  plan_version: number;
  stops: Stop[];
}

export type PodMethod = 'photo' | 'signature' | 'pin';

export type UploadKind = 'photo' | 'signature' | 'scan';
export type UploadContentType = 'image/jpeg' | 'image/png' | 'image/webp';

export interface UploadUrlResult {
  upload_url: string;
  final_url: string;
  requires_upload: boolean;
}

// Phase 3 (screens 1n/1o) - see EarningsView's docstring in
// app/schemas/driver_app.py for why is_placeholder is always true today.
export interface Earnings {
  period_start: string;
  period_end: string;
  hours_worked: number;
  overtime_hours: number;
  hourly_rate_cents: number;
  estimated_pay_cents: number;
  is_placeholder: boolean;
  // Real per-delivery pay for gig, not hourly (docs/ROADMAP.md A11) -
  // lets EarningsScreen render the two differently.
  employment_type: string;
  note: string;
}

export interface TripSummary {
  route_id: string;
  completed_at: string;
  stop_count: number;
  hours: number;
}

// Phase 3 (screens 1p/1q) - masked SMS. Deliberately has no phone number
// field - see MessageView's docstring in app/schemas/driver_app.py.
export type MessageChannel = 'customer' | 'support';
export type MessageDirection = 'outbound' | 'inbound';

export interface Message {
  message_id: string;
  channel: MessageChannel;
  direction: MessageDirection;
  body: string;
  created_at: string;
  stop_id: string | null;
}

// Masked voice calling (docs/ROADMAP.md A7) - same no-phone-number rule as
// Message, see CallView's docstring in app/schemas/driver_app.py. The
// driver's own phone rings via a real carrier call; this is just the
// resulting log entry, not anything used to place the call client-side.
export type CallStatus = 'initiated' | 'connected' | 'completed' | 'failed' | 'no-answer';

export interface Call {
  call_id: string;
  status: CallStatus;
  created_at: string;
  duration_seconds: number | null;
}

// The driver's own numbers beside their hub's (docs/ROADMAP.md W4). `fleet_median` is
// null when there are too few colleagues on shift for a team median to point at anyone
// but one person - the driver's own figures still show.
export interface ScorecardMetric {
  name: string;
  unit: string;
  own_median: number | null;
  own_p90: number | null;
  own_sample_size: number;
  fleet_median: number | null;
  not_measured: string | null;
}

export interface DriverScorecard {
  window_days: number;
  generated_at: string;
  metrics: ScorecardMetric[];
  comparison_withheld: string | null;
}

// The driver's own numbers beside their hub's (docs/ROADMAP.md W4). `fleet_median` is
// null when there are too few colleagues on shift for a team median to point at anyone
// but one person - the driver's own figures still show either way.
export interface ScorecardMetric {
  name: string;
  unit: string;
  own_median: number | null;
  own_p90: number | null;
  own_sample_size: number;
  fleet_median: number | null;
  not_measured: string | null;
}

export interface DriverScorecard {
  window_days: number;
  generated_at: string;
  metrics: ScorecardMetric[];
  comparison_withheld: string | null;
}
