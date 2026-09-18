import { clearToken, getToken } from './auth'
import type {
  ClientOnboardingBody,
  ClientOnboardingResult,
  CreditExposure,
  DriverDocumentReviewBody,
  DriverDocumentReviewResult,
  DriverState,
  HeldOrderView,
  HubSummary,
  LastCycleSnapshot,
  LinkScorecard,
  NightlyJobResult,
  OperationsScorecard,
  ConsequenceOption,
  LateOrder,
  LinkageFlag,
  OrderExplanation,
  MergeProposal,
  RecordHealth,
  OverrideReasonOption,
  OverrideResult,
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
  ExceptionQueue,
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

  // What to look at before the phone rings (docs/ROADMAP_1.5.md CON-4). Any ops
  // session, not admin-only: a dispatcher on a viewer account who cannot see
  // their own exceptions cannot run a day.
  operationsExceptions: (hubId?: string) =>
    request<ExceptionQueue>(
      hubId ? `/operations/exceptions?hub_id=${hubId}` : '/operations/exceptions',
    ),

  // Why this order is where it is (docs/ROADMAP_1.5.md AGT-4). Fetched on
  // demand rather than polled: it is a reading of the decision log, which only
  // changes when a cycle runs, and one row's history is not worth a request
  // every tick for every held order.
  orderExplanation: (orderId: string) =>
    request<OrderExplanation>(`/orders/${orderId}/explanation`),

  // The reason vocabulary (docs/ROADMAP_1.5.md CON-2). Fetched rather than
  // hardcoded so the list offered here cannot drift from the CHECK constraint
  // that accepts it.
  overrideReasons: () => request<OverrideReasonOption[]>('/operations/override-reasons'),

  // Overrule the queue on one order, with a reason (CON-2, CON-3). A 409 here
  // is a sentence for the dispatcher, not an error to swallow - usually that
  // the order moved since the screen was loaded.
  overrideOrder: (orderId: string, body: { action: string; reason_code: string; note?: string }) =>
    request<OverrideResult>(`/orders/${orderId}/override`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // Late deliveries nobody has judged (docs/ROADMAP_1.5.md REC-2). The worklist
  // the consequence label depends on - without it the only way to record what
  // happened is to already know which deliveries were late, which nobody does
  // fourteen days later.
  lateOrders: (hubId: string) => request<LateOrder[]>(`/operations/late-orders?hub_id=${hubId}`),

  consequenceKinds: () => request<ConsequenceOption[]>('/operations/consequence-kinds'),

  recordConsequence: (orderId: string, body: { kind: string; detail?: string }) =>
    request<{ outcome_id: string; consequence: string }>(`/orders/${orderId}/consequence`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // The open questions the detectors raised (REC-4).
  linkageFlags: (hubId: string) =>
    request<LinkageFlag[]>(`/operations/linkage-flags?hub_id=${hubId}`),

  // Recorded rather than deleted: a dismissed flag is evidence that a person
  // considered the case, and deleting it would let the detector raise the same
  // question again tomorrow.
  resolveLinkageFlag: (flagId: string, note?: string) =>
    request<LinkageFlag>(
      `/operations/linkage-flags/${flagId}/resolve${note ? `?note=${encodeURIComponent(note)}` : ''}`,
      { method: 'POST' },
    ),

  // Is the record being written? (docs/ROADMAP_1.5.md REC-1..REC-4.) Every
  // writer in the record layer was wired recently, and one that silently stops
  // looks exactly like a quiet week.
  recordHealth: (hubId: string, windowDays = 30) =>
    request<RecordHealth>(`/operations/record-health?hub_id=${hubId}&window_days=${windowDays}`),

  // Dock pairs waiting to be judged (docs/ROADMAP_1.5.md IDN-2). Not hub-scoped,
  // because the queue is not: the same physical dock can be reached from two
  // hubs, and that pair is the most valuable merge to catch.
  mergeProposals: () => request<MergeProposal[]>('/operations/merge-proposals'),

  // Admin only. Confirming rewrites which dock a shop points at, and every
  // per-dock statistic moves with it.
  confirmMerge: (id: string) =>
    request<MergeProposal>(`/operations/merge-proposals/${id}/confirm`, { method: 'POST' }),

  rejectMerge: (id: string) =>
    request<MergeProposal>(`/operations/merge-proposals/${id}/reject`, { method: 'POST' }),

  // What the SLA credits are costing (docs/ROADMAP.md W3, E11).
  creditExposure: (windowDays = 30) =>
    request<CreditExposure>(`/operations/credit-exposure?window_days=${windowDays}`),

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
