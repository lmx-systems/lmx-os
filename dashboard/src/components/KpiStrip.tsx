import { KpiCard } from './ui/KpiCard'
import { AT_RISK_MINUTES, formatIsoRelative, minutesUntil } from '../lib/format'
import type { DriverState, HeldOrderView, LastCycleSnapshot, OrderStatusSummary } from '../lib/types'

interface KpiStripProps {
  fleet: DriverState[] | null
  fleetError: Error | null
  held: HeldOrderView[] | null
  heldError: Error | null
  summary: OrderStatusSummary | null
  summaryError: Error | null
  lastCycle: LastCycleSnapshot | null
}

export function KpiStrip({
  fleet,
  fleetError,
  held,
  heldError,
  summary,
  summaryError,
  lastCycle,
}: KpiStripProps) {
  const available = fleet?.filter((d) => d.status === 'available').length ?? 0
  const enRoute = fleet?.filter((d) => d.status === 'en_route').length ?? 0
  const total = fleet?.length ?? 0

  const counts = summary?.counts ?? {}
  const inFlight = (counts.held ?? 0) + (counts.queued ?? 0) + (counts.assigned ?? 0)
  // "assigned + delivered", not "today" - the summary endpoint counts all
  // orders ever ingested for this hub, it isn't date-scoped yet.
  const dispatched = (counts.assigned ?? 0) + (counts.delivered ?? 0)

  const atRisk = held?.filter((o) => minutesUntil(o.hold_deadline) <= AT_RISK_MINUTES) ?? []

  return (
    <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <KpiCard
        label="Fleet"
        value={
          fleet === null ? '—' : (
            <>
              {available} <span className="text-sm font-normal text-[var(--text-muted)]">/ {total}</span>
            </>
          )
        }
        sub={fleet === null ? undefined : `available · ${enRoute} en route`}
        stale={Boolean(fleetError)}
      />
      <KpiCard
        label="Orders in flight"
        value={summary === null ? '—' : inFlight}
        sub={
          summary === null
            ? undefined
            : `${counts.held ?? 0} held · ${counts.queued ?? 0} queued · ${counts.assigned ?? 0} assigned`
        }
        stale={Boolean(summaryError)}
      />
      <KpiCard
        label={`At risk (<${AT_RISK_MINUTES}m to deadline)`}
        value={held === null ? '—' : atRisk.length}
        risk={atRisk.length > 0}
        sub={
          atRisk.length === 0
            ? 'None currently'
            : atRisk.map((o) => o.shop_name || o.order_id.slice(0, 8)).join(', ')
        }
        stale={Boolean(heldError)}
      />
      <KpiCard
        label="Dispatched (assigned + delivered)"
        value={summary === null ? '—' : dispatched}
        sub={summary === null ? undefined : `${counts.delivered ?? 0} delivered`}
        stale={Boolean(summaryError)}
      />
      <KpiCard
        label="Last dispatch cycle"
        value={lastCycle ? formatIsoRelative(lastCycle.at) : 'None yet'}
        sub={
          lastCycle
            ? `${Math.round(lastCycle.duration_seconds * 1000)}ms · ${lastCycle.engine} · ${lastCycle.assigned_count} assigned`
            : 'Runs automatically on order/driver events'
        }
      />
    </div>
  )
}
