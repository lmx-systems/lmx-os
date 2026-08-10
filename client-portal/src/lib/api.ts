import { clearToken, getToken } from './auth'
import type {
  ClientAuthToken,
  ClientOrderDetailView,
  ClientOrderBatchBody,
  ClientOrderBatchResult,
  ClientOrderBody,
  ClientOrderResult,
  ClientOrderSummaryView,
  ClientProfileView,
  ClientSignupBody,
  ClientSignupResult,
  ClientShopView,
  ClientUserView,
  InvoiceDetailView,
  InvoiceSummaryView,
  ReturnItemView,
  TrackingView,
} from './types'

// /client/* is exempt from the ops-dashboard auth gate
// (app/ops_auth/middleware.py's EXEMPT_PREFIXES) - it has its own real
// per-client JWT auth instead (app/client_auth/), a separate auth domain
// from dashboard/'s ops-user login. No ops credentials to configure here.
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

  myInvoices: () => request<InvoiceSummaryView[]>('/client/invoices'),

  myInvoice: (invoiceId: string) => request<InvoiceDetailView>(`/client/invoices/${invoiceId}`),

  // Server-side PDF (docs/ROADMAP.md C3). A plain <a href> can't carry the
  // Bearer token, so fetch the bytes with auth and trigger a client-side
  // download from the blob. Returns nothing; throws ApiError on failure.
  downloadInvoicePdf: async (invoiceId: string, invoiceNumber: number): Promise<void> => {
    const token = getToken()
    const response = await fetch(`${API_BASE_URL}/client/invoices/${invoiceId}/pdf`, {
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    })
    if (response.status === 401) clearToken()
    if (!response.ok) {
      const body = await response.text().catch(() => '')
      throw new ApiError(response.status, body || response.statusText)
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `lmx-invoice-${invoiceNumber}.pdf`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  // User management (admin only, docs/ROADMAP.md C4) - the API 403s a
  // member, so the Team tab is only shown to an admin in the first place.
  listUsers: () => request<ClientUserView[]>('/client/users'),

  createUser: (body: { email: string; name: string; password: string; role: string }) =>
    request<ClientUserView>('/client/users', { method: 'POST', body: JSON.stringify(body) }),

  updateUser: (
    userId: string,
    body: { role?: string; is_active?: boolean; new_password?: string },
  ) => request<ClientUserView>(`/client/users/${userId}`, { method: 'PATCH', body: JSON.stringify(body) }),

  // Returns & core pickups (docs/ROADMAP.md W1). `awaiting` narrows to the
  // cores still waiting on a pickup - the counter's working list.
  myReturns: (awaiting = false) =>
    request<ReturnItemView[]>(`/client/returns${awaiting ? '?awaiting=true' : ''}`),

  myShops: () => request<ClientShopView[]>('/client/shops'),

  flagShopReturns: (shopId: string, manifest: string) =>
    request<ReturnItemView>(`/client/shops/${shopId}/returns`, {
      method: 'POST',
      body: JSON.stringify({ manifest }),
    }),

  // Placing an order (LMX_LINK_PLAN.md §2.2).
  submitOrder: (body: ClientOrderBody) =>
    request<ClientOrderResult>('/client/orders', { method: 'POST', body: JSON.stringify(body) }),

  // Public signup - the only call here made with no token at all. The
  // endpoint is exempt from ops auth (app/ops_auth/middleware.py) and
  // rate-limited by IP.
  signup: (body: ClientSignupBody) =>
    request<ClientSignupResult>('/public/signup', { method: 'POST', body: JSON.stringify(body) }),

  // Public delivery tracking (docs/ROADMAP.md F3). No auth header - the token in
  // the path IS the credential, which is why the backend rate-limits this and
  // answers 404 identically for an unknown and an expired token.
  trackDelivery: (token: string) =>
    request<TrackingView>(`/public/track/${encodeURIComponent(token)}`),

  // Bulk paste (§2.2 principle 5). Deliberately not all-or-nothing - the
  // response reports per line, because one unfindable address among six must
  // not discard the five that were fine.
  submitOrdersBatch: (body: ClientOrderBatchBody) =>
    request<ClientOrderBatchResult>('/client/orders/batch', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // Password reset (docs/ROADMAP.md L14). Both are unauthenticated - a
  // locked-out user has no session by definition. The request endpoint answers
  // identically whether or not the address exists, so nothing here should try
  // to infer anything from a success.
  requestPasswordReset: (email: string) =>
    request<{ message: string }>('/public/password-reset/request', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),

  confirmPasswordReset: (token: string, newPassword: string) =>
    request<{ message: string }>('/public/password-reset/confirm', {
      method: 'POST',
      body: JSON.stringify({ token, new_password: newPassword }),
    }),
}
