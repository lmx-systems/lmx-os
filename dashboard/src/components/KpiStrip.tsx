import { KpiCard } from './ui/KpiCard'
import { minutesUntil } from '../lib/format'
import type { DriverState, HeldOrderView, LastCycleInfo, OrderStatusSummary } from '../lib/types'

interface KpiStripProps {
  fleet: DriverState[] | null
  held: HeldOrderView[] | null
  summary: OrderStatusSummary | null
  lastCycle: LastCycleInfo | null
}

const AT_RISK_MINUTES = 5

export function KpiStrip({ fleet, held, summary, lastCycle }: KpiStripProps) {
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
      />
      <KpiCard
        label="Orders in flight"
        value={summary === null ? '—' : inFlight}
        sub={
          summary === null
            ? undefined
            : `${counts.held ?? 0} held · ${counts.queued ?? 0} queued · ${counts.assigned ?? 0} assigned`
        }
      />
      <KpiCard
        label={`At risk (<${AT_RISK_MINUTES}m to deadline)`}
        value={held === null ? '—' : atRisk.length}
        risk={atRisk.length > 0}
        sub={
          atRisk.length === 0
            ? 'None currently'
            : atRisk.map((o) => o.order_id.slice(0, 8)).join(', ')
        }
      />
      <KpiCard
        label="Dispatched (assigned + delivered)"
        value={summary === null ? '—' : dispatched}
        sub={summary === null ? undefined : `${counts.delivered ?? 0} delivered`}
      />
      <KpiCard
        label="Last dispatch cycle"
        value={lastCycle ? relativeTime(lastCycle.at) : 'Not run this session'}
        sub={
          lastCycle
            ? `${Math.round(lastCycle.result.duration_seconds * 1000)}ms · ${lastCycle.result.engine} · ${lastCycle.result.assignments.length} assigned`
            : 'Dispatch also runs automatically on order/driver events'
        }
      />
    </div>
  )
}

function relativeTime(at: number): string {
  const seconds = Math.round((Date.now() - at) / 1000)
  if (seconds < 60) return `${seconds}s ago`
  return `${Math.floor(seconds / 60)}m ago`
}
