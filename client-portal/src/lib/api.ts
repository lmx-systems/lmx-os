import { clearToken, getToken } from './auth'
import type {
  ClientAuthToken,
  ClientOrderDetailView,
  ClientOrderSummaryView,
  ClientProfileView,
  StatementView,
} from './types'

// Runtime config first (roadmap item D2 - injected by the Docker image's
// entrypoint from env vars, so one built image can point at any API), then
// the build-time VITE_* var for local dev, then localhost.
declare global {
  interface Window {
    __LMX_RUNTIME_CONFIG__?: { API_BASE_URL?: string }
  }
}
const runtimeConfig = window.__LMX_RUNTIME_CONFIG__ ?? {}

// /client/* is exempt from the internal shared-secret stopgap
// (app/security.py's EXEMPT_PREFIXES) - it has its own real per-client JWT
// auth instead (app/client_auth/), unlike dashboard/'s API_SHARED_SECRET
// approach. No shared secret to configure here.
const API_BASE_URL =
  runtimeConfig.API_BASE_URL || import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

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

  myStatement: (year: number, month: number) =>
    request<StatementView>(`/client/billing/statements/${year}/${month}`),
}

// PDF download is a raw-bytes fetch, not JSON - kept out of request<T>().
export async function downloadInvoicePdf(year: number, month: number): Promise<void> {
  const token = getToken()
  const response = await fetch(`${API_BASE_URL}/client/billing/statements/${year}/${month}/invoice.pdf`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `lmx-invoice-${year}-${String(month).padStart(2, '0')}.pdf`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
