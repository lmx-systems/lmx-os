import Constants from 'expo-constants';

import type {
  AuthToken,
  DriverProfile,
  JobOffer,
  PodMethod,
  RequestOtpResult,
  Route,
} from './types';

// app.json's extra.apiBaseUrl is the dev default (local backend). Point
// this at the real LMX OS deployment for anything beyond a simulator
// pointed at localhost - see driver-app/README.md.
const API_BASE_URL: string =
  (Constants.expoConfig?.extra?.apiBaseUrl as string | undefined) ?? 'http://localhost:8000';

let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
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

  verifyOtp: (phone: string, code: string) =>
    request<AuthToken>('/driver/auth/verify-otp', {
      method: 'POST',
      body: JSON.stringify({ phone, code }),
    }),

  getMyProfile: () => request<DriverProfile>('/driver/me'),

  updateMyProfile: (body: { vehicle_type: string; plate_number: string; delivery_zone: string }) =>
    request<DriverProfile>('/driver/me', { method: 'PUT', body: JSON.stringify(body) }),

  setAvailability: (status: string) =>
    request<{ ok: boolean }>('/driver/me/state', { method: 'POST', body: JSON.stringify({ status }) }),

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
    body: { method: PodMethod; photo_url?: string; signature_url?: string; pin?: string },
  ) =>
    request<Route['stops'][number]>(`/driver/stops/${stopId}/complete`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
};

export { ApiError };
