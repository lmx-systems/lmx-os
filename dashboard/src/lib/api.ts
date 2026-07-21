import type {
  ClientOnboardingBody,
  ClientOnboardingResult,
  DriverState,
  HeldOrderView,
  HubView,
  LastCycleSnapshot,
  NightlyJobResult,
  OpsAuthToken,
  OptimizationResult,
  OrderStatusSummary,
} from './types'
import { clearOpsToken, getOpsToken } from './auth'

// Runtime config first (roadmap item D2 - injected by the Docker image's
// entrypoint from env vars, so one built image can point at any API), then
// the build-time VITE_* vars for local dev, then localhost.
declare global {
  interface Window {
    __LMX_RUNTIME_CONFIG__?: { API_BASE_URL?: string; API_SHARED_SECRET?: string }
  }
}
const runtimeConfig = window.__LMX_RUNTIME_CONFIG__ ?? {}

// Only a shared-secret stopgap exists on the backend (see
// docs/ARCHITECTURE.md item 0), not real per-user auth. When no shared
// secret is configured, this sends no credentials at all, same as before
// this existed - accurate for a backend with API_SHARED_SECRET unset too.
const API_BASE_URL =
  runtimeConfig.API_BASE_URL || import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const API_SHARED_SECRET = runtimeConfig.API_SHARED_SECRET || import.meta.env.VITE_API_SHARED_SECRET

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Per-user ops token (roadmap item S1) wins over the legacy shared
  // secret when both exist - mirrors the backend middleware's ordering
  // (app/security.py). A 401 clears the stale token so App falls back to
  // the login screen instead of looping.
  const opsToken = getOpsToken()
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(opsToken
        ? { Authorization: `Bearer ${opsToken}` }
        : API_SHARED_SECRET
          ? { 'X-API-Key': API_SHARED_SECRET }
          : {}),
    },
    ...init,
  })
  if (response.status === 401 && opsToken) {
    clearOpsToken()
  }
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new ApiError(response.status, body || response.statusText)
  }
  return response.json() as Promise<T>
}

export const api = {
  opsLogin: (email: string, password: string) =>
    request<OpsAuthToken>('/ops/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  listHubs: () => request<HubView[]>('/hubs'),

  fleetOverview: (hubId: string) => request<DriverState[]>(`/fleet/${hubId}/drivers`),

  heldOrders: (hubId: string) => request<HeldOrderView[]>(`/batch-queue/${hubId}/held-orders`),

  orderSummary: (hubId: string) => request<OrderStatusSummary>(`/orders/${hubId}/summary`),

  lastCycle: (hubId: string) => request<LastCycleSnapshot | null>(`/optimizer/${hubId}/last-cycle`),

  runOptimizerCycle: (hubId: string) =>
    request<OptimizationResult>(`/optimizer/${hubId}/run-cycle`, { method: 'POST' }),

  runLearningLoopJob: (hubId: string) =>
    request<NightlyJobResult>(`/learning-loop/${hubId}/run-nightly-job`, { method: 'POST' }),

  // Phase 8 minimal client onboarding (app/api/admin_routes.py) - internal
  // ops action, gated by the same API_SHARED_SECRET as every other request
  // this file makes (unlike client-portal/'s API, which never touches this
  // shared secret at all - see that app's lib/api.ts).
  onboardClient: (body: ClientOnboardingBody) =>
    request<ClientOnboardingResult>('/admin/clients', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}
