import { clearToken, getToken } from './auth'
import type {
  ClientOnboardingBody,
  ClientOnboardingResult,
  DriverDocumentReviewBody,
  DriverDocumentReviewResult,
  DriverState,
  HeldOrderView,
  HubSummary,
  LastCycleSnapshot,
  LinkScorecard,
  NightlyJobResult,
  OperationsScorecard,
  OpsAuthToken,
  OpsProfileView,
  OptimizationResult,
  OrderStatusSummary,
  PendingDriverDocumentView,
  PendingSignupView,
  ProposedRuleApprovalResult,
  ProposedRuleView,
  SignupDecisionResult,
  UrgencyRuleBody,
  UrgencyRuleView,
} from './types'

// Real per-account ops auth (docs/ROADMAP.md S1), replacing the old
// shared X-API-Key stopgap - a JWT issued by POST /ops/auth/login,
// mirrors client-portal/src/lib/api.ts's approach exactly.
//
// Read at runtime first (docker/generate-env-config.sh writes
// window.__RUNTIME_CONFIG__ from real container env vars at container
// startup, not Docker image build time - see Dockerfile/docs/ROADMAP.md
// D2), falling back to the Vite build-time value for local `npm run dev`,
// where no entrypoint script ever runs and window.__RUNTIME_CONFIG__ is
// never set.
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

// Same as request() but for 204 No Content responses (e.g. DELETE), which
// have no body to parse.
async function requestVoid(path: string, init?: RequestInit): Promise<void> {
  const token = getToken()
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...init,
  })
  if (response.status === 401) clearToken()
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new ApiError(response.status, body || response.statusText)
  }
}

export const api = {
  login: (email: string, password: string) =>
    request<OpsAuthToken>('/ops/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  myProfile: () => request<OpsProfileView>('/ops/me'),

  listHubs: () => request<HubSummary[]>('/hubs'),

  fleetOverview: (hubId: string) => request<DriverState[]>(`/fleet/${hubId}/drivers`),

  heldOrders: (hubId: string) => request<HeldOrderView[]>(`/batch-queue/${hubId}/held-orders`),

  orderSummary: (hubId: string) => request<OrderStatusSummary>(`/orders/${hubId}/summary`),

  lastCycle: (hubId: string) => request<LastCycleSnapshot | null>(`/optimizer/${hubId}/last-cycle`),

  runOptimizerCycle: (hubId: string) =>
    request<OptimizationResult>(`/optimizer/${hubId}/run-cycle`, { method: 'POST' }),

  // Measurement (docs/ROADMAP.md I4, F7). Ops-admin only, and not hub-scoped: these are
  // fleet-wide distributions over durable rows.
  operationsScorecard: (windowDays = 30) =>
    request<OperationsScorecard>(`/operations/scorecard?window_days=${windowDays}`),

  linkScorecard: () => request<LinkScorecard>('/lmx-link/scorecard'),

  runLearningLoopJob: (hubId: string) =>
    request<NightlyJobResult>(`/learning-loop/${hubId}/run-nightly-job`, { method: 'POST' }),

  // Phase 8 minimal client onboarding (app/api/admin_routes.py) - internal
  // ops action, gated by the same ops-user Bearer token as every other
  // request this file makes (unlike client-portal/'s API, which has its
  // own separate client-JWT auth domain - see that app's lib/api.ts).
  onboardClient: (body: ClientOnboardingBody) =>
    request<ClientOnboardingResult>('/admin/clients', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // Orchestrator-editable urgency rules (docs/ROADMAP.md W6).
  listUrgencyRules: (hubId: string) =>
    request<UrgencyRuleView[]>(`/admin/hubs/${hubId}/urgency-rules`),

  addUrgencyRule: (hubId: string, body: UrgencyRuleBody) =>
    request<UrgencyRuleView>(`/admin/hubs/${hubId}/urgency-rules`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  setUrgencyRuleEnabled: (hubId: string, ruleId: string, enabled: boolean) =>
    request<UrgencyRuleView>(`/admin/hubs/${hubId}/urgency-rules/${ruleId}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    }),

  removeUrgencyRule: (hubId: string, ruleId: string) =>
    requestVoid(`/admin/hubs/${hubId}/urgency-rules/${ruleId}`, { method: 'DELETE' }),

  // Learning-Loop rule review & promotion (docs/ROADMAP.md I2).
  listProposedRules: (hubId: string) =>
    request<ProposedRuleView[]>(`/admin/hubs/${hubId}/proposed-rules`),

  approveProposedRule: (ruleId: string) =>
    request<ProposedRuleApprovalResult>(`/admin/proposed-rules/${ruleId}/approve`, { method: 'POST' }),

  dismissProposedRule: (ruleId: string) =>
    request<ProposedRuleApprovalResult>(`/admin/proposed-rules/${ruleId}/dismiss`, { method: 'POST' }),

  // Public-signup review (docs/LMX_LINK_PLAN.md). Approval is also where a
  // client's per-tier rates get set - which is what guarantees an active
  // client is always billable.
  listSignups: (status = 'pending') =>
    request<PendingSignupView[]>(`/admin/signups?status=${encodeURIComponent(status)}`),

  // Driver compliance review (docs/ROADMAP.md R4). Not hub-scoped: a driver whose
  // license is unreviewed can't work at any hub.
  listPendingDriverDocuments: () =>
    request<PendingDriverDocumentView[]>('/admin/drivers/documents/pending'),

  reviewDriverDocument: (documentId: string, body: DriverDocumentReviewBody) =>
    request<DriverDocumentReviewResult>(`/admin/drivers/documents/${documentId}/review`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  approveSignup: (clientId: string, body: { rates: { sla_tier: string; rate_per_drop_cents: number }[]; hub_id?: string }) =>
    request<SignupDecisionResult>(`/admin/signups/${clientId}/approve`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  rejectSignup: (clientId: string, reason?: string) =>
    request<SignupDecisionResult>(`/admin/signups/${clientId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason ?? null }),
    }),
}
