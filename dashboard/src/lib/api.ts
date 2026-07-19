import type {
  ClientOnboardingBody,
  ClientOnboardingResult,
  DriverState,
  HeldOrderView,
  LastCycleSnapshot,
  NightlyJobResult,
  OptimizationResult,
  OrderStatusSummary,
} from './types'

// Only a shared-secret stopgap exists on the backend (see
// docs/ARCHITECTURE.md item 0), not real per-user auth. When
// VITE_API_SHARED_SECRET isn't set, this sends no credentials at all,
// same as before this existed - accurate for a backend with
// API_SHARED_SECRET unset too.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
const API_SHARED_SECRET = import.meta.env.VITE_API_SHARED_SECRET

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(API_SHARED_SECRET ? { 'X-API-Key': API_SHARED_SECRET } : {}),
    },
    ...init,
  })
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new ApiError(response.status, body || response.statusText)
  }
  return response.json() as Promise<T>
}

export const api = {
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
