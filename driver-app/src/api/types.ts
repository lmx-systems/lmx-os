/**
 * Mirrors app/schemas/driver_auth.py and app/schemas/driver_app.py on the
 * backend (LMX OS). Keep these two in sync by hand for now - no shared
 * schema-codegen step exists yet.
 */

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
  // Where the warehouse is, so the app can put a fence round it (DRV-3).
  // Nullable: a hub created before coordinates were required has none, and a
  // fence at (0, 0) is in the Gulf of Guinea.
  hub_lat: number | null;
  hub_lng: number | null;
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
  // A bonus on top, owed once delivered (DEC-10). Absent or null for most offers.
  bonus_cents?: number | null;
}

// `visit`: a technician at a site with no delivery, capturing its door (DE-6).
export type StopType = 'pickup' | 'dropoff' | 'visit';
export type StopStatus = 'pending' | 'en_route' | 'arrived' | 'completed' | 'failed';

export type ItemSizeClass = 'envelope' | 'small' | 'medium' | 'large' | 'oversize';

/** Mirrors app/schemas/driver_app.py ItemFactsBody (DE-5). Omitted = unchanged. */
export interface ItemFactsBody {
  item_description?: string;
  item_weight_kg?: number;
  item_size_class?: ItemSizeClass;
  item_hazmat_or_liquid?: boolean;
}

// DE-7: a delivery made for a gig platform, logged as a golden record.
// Mirrors app/schemas/gig.py and app/schemas/off_platform.py.
export type GigPlatform = 'curri' | 'dispatch' | 'roadie';

/** The job as typed on the form - GigJobIntake. */
export interface GigJobIntake {
  source_platform: GigPlatform;
  platform_job_ref: string;
  pickup_address: string;
  dropoff_address: string;
  pickup_window_open: string;
  pickup_window_close: string;
  dropoff_window_close?: string;
  pay_cents: number;
  intake_source?: 'manual';
}

/** A job already in the store - the fields the form needs from GigJobView. */
export interface GigJob {
  gig_job_id: string;
  source_platform: GigPlatform;
  platform_job_ref: string;
  pickup_address: string;
  dropoff_address: string | null;
  status: 'offered' | 'accepted' | 'picked_up' | 'delivered' | 'declined' | 'cancelled';
  is_logged_as_record: boolean;
  record_bonus_cents: number | null;
}

export interface PinBody {
  lat: number;
  lng: number;
  accuracy_m?: number | null;
}

/** One end of the job: where the vehicle stopped, the counter or door, when. */
export interface OffPlatformEnd {
  stop_pin?: PinBody;
  door_pin?: PinBody;
  at?: string;
}

/** Sent at the counter and again at the door; each send writes what it carries. */
export interface OffPlatformRecordBody {
  pickup_stop_id: string;
  gig_job_id?: string;
  job?: GigJobIntake;
  item?: ItemFactsBody;
  pickup?: OffPlatformEnd;
  dropoff?: OffPlatformEnd;
}

export interface OffPlatformRecordView {
  record_id: string;
  pickup_stop_id: string;
  order_id: string;
  gig_job_id: string;
  gig_job_status: string;
  picked_up_at: string | null;
  delivered_at: string | null;
  tier: string | null;
  elements_missing: number | null;
  record_bonus_cents: number | null;
  // False when the pickup address named no place: no counter to capture.
  counter_resolved: boolean;
}

/** Mirrors app/schemas/driver_app.py OrderItemView. Null = not known yet. */
export interface OrderItemView {
  order_id: string;
  reference: string;
  item_description: string | null;
  item_weight_kg: number | null;
  item_size_class: ItemSizeClass | null;
  item_hazmat_or_liquid: boolean | null;
}

/** Mirrors app/schemas/driver_app.py ParcelView. */
export interface ParcelView {
  barcode: string;
  scanned: boolean;
  // False for a barcode LMX minted, which no label carries yet (DE-5).
  from_source: boolean;
}

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
  // Cores to collect or drop here, if any (docs/ROADMAP.md W1). Null for the
  // overwhelming majority. Sent with the stop for the same reason `cod` is: a
  // driver who learns at the counter that there is a core to collect has
  // already put the box down and said goodbye.
  returns: StopReturns | null;
  // Whether to ask the eight dock questions here (docs/ROADMAP.md DRV-7).
  // Decided by the server from the rules in `app/identity/dock_survey.py` - the
  // app renders what it is told rather than restating a condition another
  // module owns, which is what produced both bugs in THE_DRIVER_APP.md §6.
  dock_needs_survey: boolean;
  // The site's place facts and which of them the survey should ask (DE-15).
  // Present when `dock_needs_survey` is; carried with the stop so the questions
  // work without signal.
  site_facts?: SiteFacts | null;
}

/** A site's kind, setting and shared-address answer as known now, and which the
 *  door survey should ask: unknown, or only the desk's guess
 *  (app/identity/site_facts.py). */
export interface SiteFacts {
  kind_of_site: string | null;
  setting: string | null;
  shares_address: boolean | null;
  ask: ('kind_of_site' | 'setting' | 'shares_address')[];
}

/** The site facts alone (DE-15), after a drop-off at a door already captured.
 *  "Not sure" is left out. */
export interface SiteFactsBody {
  kind_of_site?: string | null;
  setting?: string | null;
  shares_address?: boolean | null;
}

/** The site's facts after an answer, and which answers disagreed with a
 *  person's and were kept rather than applied. */
export interface SiteFactsAnswered extends SiteFacts {
  disputed: string[];
}

/** One core, as the three W1 endpoints hand it back. */
export interface ReturnItem {
  return_id: string;
  origin_order_ref: string;
  shop_name: string | null;
  manifest: string;
  status: string;
  created_at: string;
  age_hours: number;
  collected_at: string | null;
  returned_at: string | null;
}

export interface StopReturns {
  // Cores this delivery was expected to bring back. Dropoff stops only.
  expected_manifests: string[];
  // Cores already collected and bound for THIS shop. Pickup stops only.
  to_drop_manifests: string[];
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

// `handoff`: one of the four guided photos of the handoff point (DE-4).
export type UploadKind = 'photo' | 'signature' | 'scan' | 'handoff';
export type HandoffPhotoSlot = 'approach' | 'stop_point' | 'path' | 'handoff';
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
  // Offer bonuses owed for orders delivered this period (DEC-10): recorded,
  // not yet paid, and not in estimated_pay_cents.
  bonuses_owed_cents?: number;
}

export interface TripSummary {
  route_id: string;
  completed_at: string;
  stop_count: number;
  hours: number;
}

// Phase 3 (screens 1p/1q) - the support thread with the hub's dispatchers,
// answered in the ops console. outbound is the driver's message, inbound a
// dispatcher's reply.
export type MessageChannel = 'support';
export type MessageDirection = 'outbound' | 'inbound';

export interface Message {
  message_id: string;
  channel: MessageChannel;
  direction: MessageDirection;
  body: string;
  created_at: string;
  stop_id: string | null;
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

// Why a driver turned an offer down (docs/ROADMAP.md I1, I4). Mirrors DECLINE_REASONS in
// app/schemas/driver_app.py - short on purpose, because this is asked at the one moment
// a driver is under time pressure.
export type DeclineReason =
  | 'too_far'
  | 'pay_too_low'
  | 'vehicle_unsuitable'
  | 'ending_shift'
  | 'other';


/** Eight taps at the door (docs/ROADMAP.md DRV-7). Every field optional: a
 *  measurement may fail, a delivery may not, so every question has a skip. */
export interface DockSurveyBody {
  stop_point?: string | null;
  curb_access?: string | null;
  walk_distance_band?: string | null;
  door_path?: string | null;
  obstruction?: string | null;
  who_receives?: string | null;
  landing_surface?: string | null;
  appointment_required?: boolean | null;
  photo_url?: string | null;
  // The golden handoff capture (DE-4). Vocabularies: app/models/handoff_point.py.
  handoff_type?: string | null;
  handoff_stop_type?: string | null;
  stop_legal?: boolean | null;
  steps_or_ramps?: string | null;
  continuous_sidewalk?: boolean | null;
  // An empty list is "looked, nothing there"; left out is unknown.
  obstructions?: string[] | null;
  overhead?: string[] | null;
  drone_open_ground?: boolean | null;
  // The site's place facts (DE-15). A confirm sends the value already there.
  // Vocabularies: app/models/location.py.
  kind_of_site?: string | null;
  setting?: string | null;
  shares_address?: boolean | null;
}

export interface DockSurveyResult {
  location_id: string;
  is_surveyed: boolean;
  surveyed_at: string | null;
  surveys_remaining_today: number;
}

/**
 * One phone this driver is signed in on (`docs/ROADMAP.md` S1).
 *
 * `is_current` is decided by the server from the device id inside the token,
 * not by the app comparing ids. The app knows its own installation id, but a
 * token minted for a different device would still look "current" to it, and
 * "which session am I actually using" is the one question this screen exists
 * to answer.
 */
export interface DriverDevice {
  device_id: string;
  device_name: string | null;
  last_seen_at: string;
  is_current: boolean;
}
