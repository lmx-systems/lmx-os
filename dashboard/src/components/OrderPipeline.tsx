import { Card } from './ui/Card'
import type { OrderStatusSummary } from '../lib/types'

const STAGES: { key: string; label: string; color: string }[] = [
  { key: 'received', label: 'Received', color: 'bg-[var(--gray)]' },
  { key: 'classified', label: 'Classified', color: 'bg-[var(--gray)]' },
  { key: 'held', label: 'Held', color: 'bg-[var(--red)]' },
  { key: 'queued', label: 'Queued', color: 'bg-[var(--amber)]' },
  { key: 'assigned', label: 'Assigned', color: 'bg-[var(--blue)]' },
  { key: 'delivered', label: 'Delivered', color: 'bg-[var(--green)]' },
  { key: 'cancelled', label: 'Cancelled', color: 'bg-[var(--border-strong)]' },
]

interface OrderPipelineProps {
  summary: OrderStatusSummary | null
  error: Error | null
  loading: boolean
}

export function OrderPipeline({ summary, error, loading }: OrderPipelineProps) {
  const counts = summary?.counts ?? {}
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0)

  return (
    <Card title="Order pipeline" meta={loading ? 'refreshing…' : summary ? `${total} orders` : undefined}>
      {error && <p className="text-sm text-[var(--red)]">Couldn't load order summary: {error.message}</p>}
      {!error && !summary && <p className="text-sm text-[var(--text-muted)]">Loading…</p>}
      {!error && summary && (
        <div className="flex flex-col gap-2">
          {STAGES.map((stage) => {
            const count = counts[stage.key] ?? 0
            const pct = total > 0 ? Math.max((count / total) * 100, count > 0 ? 3 : 0) : 0
            return (
              <div key={stage.key} className="grid grid-cols-[86px_1fr_36px] items-center gap-2.5 text-[12.5px]">
                <span className="text-[var(--text-secondary)]">{stage.label}</span>
                <div className="h-4 overflow-hidden rounded-md bg-[var(--surface-2)]">
                  <div className={`h-full rounded-md ${stage.color}`} style={{ width: `${pct}%` }} />
                </div>
                <span className="text-right font-medium tabular-nums text-[var(--text-primary)]">{count}</span>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
