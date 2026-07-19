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
}

export interface Route {
  route_id: string;
  status: string;
  plan_version: number;
  stops: Stop[];
}

export type PodMethod = 'photo' | 'signature' | 'pin';
