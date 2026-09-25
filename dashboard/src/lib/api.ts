import { clearToken, getToken } from './auth'
import type {
  AdminClient,
  ClientOnboardingBody,
  ClientRate,
  ClientSlaTerm,
  ClientOnboardingResult,
  CodDisputeReport,
  DriverDevice,
  GigDensityReport,
  GigJob,
  HubClosure,
  ReturnItem,
  DriverOnboardingBody,
  DriverOnboardingResult,
  HubSettings,
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
  OrderLookupPage,
  MergeProposal,
  AttentionCounts,
  ClassificationCoverage,
  RecordHealth,
  UnlabelledDock,
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
  mergeProposals: (includeApplied = false) =>
    request<MergeProposal[]>(
      `/operations/merge-proposals?include_applied=${includeApplied}`,
    ),

  // Undo a merge that went through. "Every merge audited and reversible" is a
  // clause of IDN-2's done-when, and nothing listed an applied merge for
  // anybody to reverse until this.
  revertMerge: (id: string) =>
    request<MergeProposal>(`/operations/merges/${id}/revert`, { method: 'POST' }),

  // Admin only. Confirming rewrites which dock a shop points at, and every
  // per-dock statistic moves with it.
  confirmMerge: (id: string) =>
    request<MergeProposal>(`/operations/merge-proposals/${id}/confirm`, { method: 'POST' }),

  rejectMerge: (id: string) =>
    request<MergeProposal>(`/operations/merge-proposals/${id}/reject`, { method: 'POST' }),

  // Docks nobody has classified (docs/ROADMAP_1.5.md IDN-3). Ordered by how busy
  // the dock looks, because the tail is long and an arbitrary order gets worked
  // from the top until somebody stops.
  unlabelledDocks: (limit = 50) =>
    request<UnlabelledDock[]>(`/operations/unlabelled-docks?limit=${limit}`),

  labelDock: (locationId: string, nodeClass: string) =>
    request<UnlabelledDock>(`/operations/docks/${locationId}/node-class`, {
      method: 'POST',
      body: JSON.stringify({ node_class: nodeClass }),
    }),

  classificationCoverage: () =>
    request<ClassificationCoverage>('/operations/classification-coverage'),

  // Find an order (CON-1). The hold queue's search only ever filtered the held
  // list, so an order already released or assigned was unfindable - while the
  // customer phoning about it could search their own orders all along.
  lookupOrders: (hubId: string, q: string) =>
    request<OrderLookupPage>(
      `/operations/orders?hub_id=${hubId}&q=${encodeURIComponent(q)}`,
    ),

  // How much is waiting for a person, without loading any of it (CON-1).
  attentionCounts: (hubId: string) =>
    request<AttentionCounts>(`/operations/attention-counts?hub_id=${hubId}`),

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

  // Provision a driver. Nothing created one before this - every row was a
  // hand-written insert, while the OTP path's own comment says drivers are
  // "provisioned by ops, not self-registered".
  onboardDriver: (body: DriverOnboardingBody) =>
    request<DriverOnboardingResult>('/admin/drivers', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // One hub's settings, including which overtime rule applies. Any ops session:
  // somebody wondering why a driver's overtime looks wrong should not need an
  // admin to find out.
  hubSettings: (hubId: string) => request<HubSettings>(`/admin/hubs/${hubId}`),

  // Admin. `Hub.state_code` selects a driver's overtime rule and was set by
  // nothing until this existed (docs/ROADMAP_AUDIT_2026-09.md).
  updateHub: (hubId: string, body: { state_code?: string | null; name?: string }) =>
    request<HubSettings>(`/admin/hubs/${hubId}`, {
      method: 'PATCH',
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

  // Days the hub is not operating (docs/ROADMAP.md R6). The optimizer skips
  // dispatch and the nightly job skips the day; all three endpoints existed
  // with nothing calling them (docs/ROADMAP_AUDIT_2026-09.md).
  listHubClosures: (hubId: string) =>
    request<HubClosure[]>(`/admin/hubs/${hubId}/closures`),

  addHubClosure: (hubId: string, body: { closure_date: string; reason: string | null }) =>
    request<HubClosure>(`/admin/hubs/${hubId}/closures`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  removeHubClosure: (hubId: string, closureDate: string) =>
    requestVoid(`/admin/hubs/${hubId}/closures/${closureDate}`, { method: 'DELETE' }),

  // A driver's devices, and revoking one on their behalf. The revocation
  // endpoint existed; nothing could list what to revoke, so the "driver lost
  // their phone and rings dispatch" path it was written for could not be
  // walked (docs/ROADMAP_AUDIT_2026-09.md).
  listDriverDevices: (driverId: string) =>
    request<DriverDevice[]>(`/admin/drivers/${driverId}/devices`),

  revokeDriverDevice: (driverId: string, deviceId: string) =>
    requestVoid(`/admin/drivers/${driverId}/devices/${deviceId}`, { method: 'DELETE' }),

  // Repeat COD disputes per account (docs/ROADMAP.md W2).
  codDisputes: (hubId: string, windowDays = 30) =>
    request<CodDisputeReport>(`/admin/hubs/${hubId}/cod-disputes?window_days=${windowDays}`),

  // Clients on a hub. Nothing listed them until this: four endpoints take a
  // client_id and the only way to get one was to read the database
  // (docs/ROADMAP_AUDIT_2026-09.md).
  listClients: (hubId: string) =>
    request<AdminClient[]>(`/admin/hubs/${hubId}/clients`),

  // Rate cards (docs/ROADMAP.md F5). The GET returns the rate in force now, one
  // per tier - not the version history, which since migration 0045 every edit
  // adds to.
  listClientRates: (clientId: string) =>
    request<ClientRate[]>(`/admin/clients/${clientId}/rates`),

  upsertClientRate: (clientId: string, body: Omit<ClientRate, 'rate_id'>) =>
    request<ClientRate>(`/admin/clients/${clientId}/rates`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  // The other half of the contract (docs/ROADMAP.md W3): what we promised and
  // what missing it costs. Both endpoints existed and neither had a caller -
  // an operator could set a term with scripts/set_client_sla_terms.py and then
  // had no way to read back what they had agreed to, which is the half that
  // matters when a client disputes a credit.
  listClientSlaTerms: (clientId: string) =>
    request<ClientSlaTerm[]>(`/admin/clients/${clientId}/sla-terms`),

  upsertClientSlaTerm: (clientId: string, body: Omit<ClientSlaTerm, 'term_id'>) =>
    request<ClientSlaTerm>(`/admin/clients/${clientId}/sla-terms`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  // The reverse leg (docs/ROADMAP.md W1). `awaiting=true` is the counter-facing
  // cut: everything still waiting on a pickup, oldest first.
  listReturns: (hubId: string, awaiting = false) =>
    request<ReturnItem[]>(
      `/admin/hubs/${hubId}/returns${awaiting ? '?awaiting=true' : ''}`,
    ),

  markReturnReturned: (returnId: string) =>
    request<ReturnItem>(`/admin/returns/${returnId}/mark-returned`, { method: 'POST' }),

  rescheduleReturn: (returnId: string) =>
    request<ReturnItem>(`/admin/returns/${returnId}/reschedule`, { method: 'POST' }),

  // The gig path (docs/ROADMAP.md G3, G12).
  listGigJobs: (hubId: string, status?: string) =>
    request<GigJob[]>(
      `/admin/hubs/${hubId}/gig-jobs${status ? `?status=${encodeURIComponent(status)}` : ''}`,
    ),

  gigDensity: (hubId: string, days = 14) =>
    request<GigDensityReport>(`/admin/hubs/${hubId}/gig-density?days=${days}`),
}
