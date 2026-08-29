import Constants from 'expo-constants';

import type {
  AuthToken,
  Call,
  CodMethod,
  DocType,
  DriverCompliance,
  DriverDocument,
  DriverProfile,
  DeclineReason,
  DriverScorecard,
  Earnings,
  FlagReasonCode,
  JobOffer,
  Message,
  PodMethod,
  RequestOtpResult,
  Route,
  TripSummary,
  UploadContentType,
  UploadKind,
  UploadUrlResult,
} from './types';

// app.json's extra.apiBaseUrl is the dev default (local backend). Point
// this at the real LMX OS deployment for anything beyond a simulator
// pointed at localhost - see driver-app/README.md.
export const API_BASE_URL: string =
  (Constants.expoConfig?.extra?.apiBaseUrl as string | undefined) ?? 'http://localhost:8000';

let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

// For anything that can't go through the request() wrapper below - e.g.
// realtime/routeEventsClient.ts, which needs to hand its own Authorization
// header to an SSE client library rather than an ordinary fetch() call.
export function getAuthToken(): string | null {
  return authToken;
}

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // Non-JSON error body - fall back to statusText.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  const text = await response.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export const api = {
  requestOtp: (phone: string) =>
    request<RequestOtpResult>('/driver/auth/request-otp', {
      method: 'POST',
      body: JSON.stringify({ phone }),
    }),

  verifyOtp: (phone: string, code: string, deviceId: string, deviceName?: string) =>
    request<AuthToken>('/driver/auth/verify-otp', {
      method: 'POST',
      body: JSON.stringify({ phone, code, device_id: deviceId, device_name: deviceName }),
    }),

  refreshToken: () => request<AuthToken>('/driver/auth/refresh', { method: 'POST' }),

  registerPushToken: (deviceId: string, expoPushToken: string) =>
    request<void>('/driver/me/push-token', {
      method: 'POST',
      body: JSON.stringify({ device_id: deviceId, expo_push_token: expoPushToken }),
    }),

  // Position report from the device itself (docs/ROADMAP.md F1). Deliberately
  // NOT routed through the offline outbox: a stale position flushed on
  // reconnect is worse than no position at all, since the optimizer would
  // route against where the driver was twenty minutes ago. The durable trail
  // (app/models/driver_location_ping.py) accepts gaps for exactly this reason.
  reportLocation: (body: { lat: number; lng: number; recorded_at: string; accuracy_m?: number | null }) =>
    request<void>('/driver/me/location', { method: 'POST', body: JSON.stringify(body) }),

  getMyProfile: () => request<DriverProfile>('/driver/me'),

  updateMyProfile: (body: { vehicle_type: string; plate_number: string; delivery_zone: string }) =>
    request<DriverProfile>('/driver/me', { method: 'PUT', body: JSON.stringify(body) }),

  setAvailability: (status: string) =>
    request<{ ok: boolean }>('/driver/me/state', { method: 'POST', body: JSON.stringify({ status }) }),

  updatePaymentMethod: (bankLast4: string) =>
    request<DriverProfile>('/driver/me/payment-method', {
      method: 'PUT',
      body: JSON.stringify({ bank_last4: bankLast4 }),
    }),

  getMyDocuments: () => request<DriverDocument[]>('/driver/me/documents'),

  // Why the go-online toggle is refusing. Read up front so the app can explain
  // the block instead of letting the driver discover it by tapping and getting a
  // 409 - and it is the SAME computation the gate uses, not the app's guess.
  getMyCompliance: () => request<DriverCompliance>('/driver/me/compliance'),

  // Somewhere to PUT a photo of a licence or insurance card.
  //
  // **The driver no longer names their own file_url**, which is what this
  // replaces: the backend mints the object key and writes the URL itself, so the
  // record can only ever point at something LMX actually holds. Submitting a new
  // upload resets the review - a document that was verified and then replaced is
  // not still verified.
  createDocumentUploadUrl: (
    docType: DocType,
    body: { content_type: UploadContentType; claimed_expires_at: string },
  ) =>
    request<UploadUrlResult>(`/driver/me/documents/${docType}/upload-url`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // Correct the expiry date only. `file_url` is deliberately absent: it used to
  // be settable here, and any string was accepted and stored as a licence scan.
  // Changing the claimed date sends the document back for review.
  updateDocument: (docType: DocType, body: { claimed_expires_at: string }) =>
    request<DriverDocument>(`/driver/me/documents/${docType}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  getMyOffers: () => request<JobOffer[]>('/driver/me/offers'),

  acceptOffer: (offerId: string) => request<Route>(`/driver/offers/${offerId}/accept`, { method: 'POST' }),

  declineOffer: (offerId: string) =>
    request<{ ok: boolean }>(`/driver/offers/${offerId}/decline`, { method: 'POST' }),

  // Attached AFTER the decline, never as part of it. The decline releases the orders
  // immediately; making a driver pick a reason first would leave the work held while they
  // hesitated, and an offer that expires instead of being declined is worse for dispatch
  // than one declined without a reason.
  setDeclineReason: (offerId: string, reason: DeclineReason) =>
    request<void>(`/driver/offers/${offerId}/decline-reason`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),

  getMyRoute: () => request<Route | null>('/driver/me/route'),

  arriveAtStop: (stopId: string) => request<Route['stops'][number]>(`/driver/stops/${stopId}/arrive`, { method: 'POST' }),

  scanParcels: (stopId: string, scannedCount: number) =>
    request<Route['stops'][number]>(`/driver/stops/${stopId}/scan`, {
      method: 'POST',
      body: JSON.stringify({ scanned_count: scannedCount }),
    }),

  createUploadUrl: (stopId: string, kind: UploadKind, contentType: UploadContentType) =>
    request<UploadUrlResult>(`/driver/stops/${stopId}/upload-url`, {
      method: 'POST',
      body: JSON.stringify({ kind, content_type: contentType }),
    }),

  // Cash on delivery (docs/ROADMAP.md W2). **There is no amount parameter, and
  // that absence is the rule**: the figure comes off the order, so "collected"
  // can only mean all of it. The money is the distributor's invoice to their own
  // customer and nobody at LMX has authority to discount it.
  collectCod: (stopId: string, method: CodMethod) =>
    request<Route['stops'][number]>(`/driver/stops/${stopId}/collect-cod`, {
      method: 'POST',
      body: JSON.stringify({ method }),
    }),

  // One tap, the distributor is told, the driver moves on.
  raiseCodDispute: (stopId: string, note?: string) =>
    request<Route['stops'][number]>(`/driver/stops/${stopId}/cod-dispute`, {
      method: 'POST',
      body: JSON.stringify({ note: note ?? null }),
    }),

  completeStop: (
    stopId: string,
    body: {
      method: PodMethod;
      // Several, because an order can require more than one photo with named
      // subjects. `photo_url` is still accepted by the server and folded in as
      // the first photo, but sending the list is what satisfies a count above one.
      photo_urls?: string[];
      photo_url?: string;
      signature_url?: string;
      pin?: string;
      left_at?: string;
    },
  ) =>
    request<Route['stops'][number]>(`/driver/stops/${stopId}/complete`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  flagStop: (stopId: string, body: { reason: FlagReasonCode; note?: string }) =>
    request<Route['stops'][number]>(`/driver/stops/${stopId}/flag`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getEarnings: () => request<Earnings>('/driver/me/earnings'),

  // The driver's own scorecard (docs/ROADMAP.md W4). No driver id in the request - the
  // server takes it from the token, so there is no way to ask for anyone else's.
  getMyScorecard: () => request<DriverScorecard>('/driver/me/scorecard'),

  getTrips: () => request<TripSummary[]>('/driver/me/trips'),

  messageCustomer: (stopId: string, body: string) =>
    request<Message>(`/driver/stops/${stopId}/message-customer`, { method: 'POST', body: JSON.stringify({ body }) }),

  getCustomerMessages: (stopId: string) => request<Message[]>(`/driver/stops/${stopId}/messages`),

  messageSupport: (body: string) =>
    request<Message>('/driver/me/messages', { method: 'POST', body: JSON.stringify({ body }) }),

  getSupportMessages: () => request<Message[]>('/driver/me/messages'),

  callCustomer: (stopId: string) => request<Call>(`/driver/stops/${stopId}/call`, { method: 'POST' }),
};

export { ApiError };
