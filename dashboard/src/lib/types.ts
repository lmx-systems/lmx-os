// Mirrors the pydantic response models in app/schemas/*.py on the backend.
// Kept as plain interfaces (no runtime validation) - this is an internal
// tool reading data the backend already validated, not an untrusted input
// boundary. If that assumption stops holding (e.g. this dashboard starts
// taking write-heavy user input), reach for zod instead of hand-rolled types.

export interface DriverState {
  driver_id: string
  hub_id: string
  status: 'off_shift' | 'available' | 'en_route' | 'on_break'
  capacity_units: number
  load_units: number
  current_route_id: string | null
  // Populated by GET /fleet/{hub_id}/drivers via a Postgres join - null if
  // the Redis fleet-state entry has no matching Driver row (shouldn't
  // happen in practice, but the backend doesn't assume it can't).
  name: string | null
}

export interface HeldOrderView {
  order_id: string
  shop_lat: number
  shop_lng: number
  sla_tier: string
  hold_deadline: string
  held_since: string
  shop_name: string
  // Computed fresh per request against the other rows in the same
  // response - see app/api/routes.py's list_held_orders.
  cluster_mate_ids: string[]
}

export interface OrderStatusSummary {
  hub_id: string
  counts: Record<string, number>
}

export interface RouteAssignment {
  driver_id: string
  stop_ids: string[]
}

export interface OptimizationResult {
  hub_id: string
  assignments: RouteAssignment[]
  unassigned_stop_ids: string[]
  engine: string
  duration_seconds: number
  over_budget: boolean
}

// The most recently completed Dispatch Optimizer cycle for a hub, however
// it was triggered (manual or automatic) - GET /optimizer/{hub_id}/last-cycle.
// Unlike OptimizationResult (only ever returned to whoever called
// run-cycle), this is queryable by anyone, which is what lets the KPI
// strip reflect automatic cycles it didn't trigger itself.
export interface LastCycleSnapshot {
  hub_id: string
  at: string
  engine: string
  duration_seconds: number
  assigned_count: number
  unassigned_count: number
  over_budget: boolean
}

export interface ProposedRuleSummary {
  proposed_rule_id: string
  shop_id: string
  rule_type: string
  proposed_change: Record<string, number>
  confidence: number
  supporting_annotation_count: number
}

export interface NightlyJobResult {
  hub_id: string
  proposals_created: ProposedRuleSummary[]
}

// --- UI-local types below - no backend equivalent, not response mirrors ---

export interface RunLogEntry {
  at: number
  kind: 'optimizer' | 'learning_loop'
  summary: string
}
