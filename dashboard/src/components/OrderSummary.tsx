import { api } from '../lib/api'
import { usePolling } from '../hooks/usePolling'

const STATUS_ORDER = ['received', 'classified', 'held', 'queued', 'assigned', 'delivered', 'cancelled']

interface OrderSummaryProps {
  hubId: string
}

export function OrderSummary({ hubId }: OrderSummaryProps) {
  const { data, error, loading } = usePolling(
    () => api.orderSummary(hubId),
    5000,
    [hubId],
    hubId.length > 0,
  )

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">Orders by Status</h2>
        {loading && <span className="text-xs text-slate-500">refreshing…</span>}
      </div>

      {error && <p className="text-sm text-red-400">Couldn't load order summary: {error.message}</p>}

      {!error && data && (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-7">
          {STATUS_ORDER.map((status) => (
            <div key={status} className="rounded border border-slate-800 bg-slate-950/50 p-2 text-center">
              <div className="text-lg font-semibold text-slate-100">{data.counts[status] ?? 0}</div>
              <div className="text-xs capitalize text-slate-500">{status}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
