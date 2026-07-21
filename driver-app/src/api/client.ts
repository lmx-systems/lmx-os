import Constants from 'expo-constants';

import type {
  AuthToken,
  DocType,
  DriverDocument,
  DriverProfile,
  Earnings,
  FlagReasonCode,
  JobOffer,
  Message,
  PodMethod,
  RequestOtpResult,
  Route,
  TripSummary,
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

  updateDocument: (docType: DocType, body: { expires_at: string; file_url?: string }) =>
    request<DriverDocument>(`/driver/me/documents/${docType}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  getMyOffers: () => request<JobOffer[]>('/driver/me/offers'),

  acceptOffer: (offerId: string) => request<Route>(`/driver/offers/${offerId}/accept`, { method: 'POST' }),

  declineOffer: (offerId: string) =>
    request<{ ok: boolean }>(`/driver/offers/${offerId}/decline`, { method: 'POST' }),

  getMyRoute: () => request<Route | null>('/driver/me/route'),

  arriveAtStop: (stopId: string) => request<Route['stops'][number]>(`/driver/stops/${stopId}/arrive`, { method: 'POST' }),

  scanParcels: (stopId: string, scannedCount: number) =>
    request<Route['stops'][number]>(`/driver/stops/${stopId}/scan`, {
      method: 'POST',
      body: JSON.stringify({ scanned_count: scannedCount }),
    }),

  completeStop: (
    stopId: string,
    body: { method: PodMethod; photo_url?: string; signature_url?: string; pin?: string; left_at?: string },
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

  getTrips: () => request<TripSummary[]>('/driver/me/trips'),

  messageCustomer: (stopId: string, body: string) =>
    request<Message>(`/driver/stops/${stopId}/message-customer`, { method: 'POST', body: JSON.stringify({ body }) }),

  getCustomerMessages: (stopId: string) => request<Message[]>(`/driver/stops/${stopId}/messages`),

  messageSupport: (body: string) =>
    request<Message>('/driver/me/messages', { method: 'POST', body: JSON.stringify({ body }) }),

  getSupportMessages: () => request<Message[]>('/driver/me/messages'),
};

export { ApiError };
