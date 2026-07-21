import { clearToken, getToken } from './auth'
import type {
  ClientAuthToken,
  ClientOrderDetailView,
  ClientOrderSummaryView,
  ClientProfileView,
} from './types'

// /client/* is exempt from the internal shared-secret stopgap
// (app/security.py's EXEMPT_PREFIXES) - it has its own real per-client JWT
// auth instead (app/client_auth/), unlike dashboard/'s API_SHARED_SECRET
// approach. No shared secret to configure here.
//
// Read at runtime first (docker/generate-env-config.sh writes
// window.__RUNTIME_CONFIG__ from the real container env at startup, not
// Docker image build time - see Dockerfile/docs/ROADMAP.md D2), falling
// back to the Vite build-time value for local `npm run dev`.
const API_BASE_URL =
  window.__RUNTIME_CONFIG__?.VITE_API_BASE_URL || import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken()
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...init,
  })

  if (response.status === 401) {
    // Expired/invalid session - drop the stale token so the app falls
    // back to the login screen instead of looping on 401s.
    clearToken()
  }

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new ApiError(response.status, body || response.statusText)
  }
  return response.json() as Promise<T>
}

export const api = {
  login: (email: string, password: string) =>
    request<ClientAuthToken>('/client/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  myProfile: () => request<ClientProfileView>('/client/me'),

  myOrders: () => request<ClientOrderSummaryView[]>('/client/orders'),

  myOrder: (orderId: string) => request<ClientOrderDetailView>(`/client/orders/${orderId}`),
}
