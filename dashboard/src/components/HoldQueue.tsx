import { api } from '../lib/api'
import { usePolling } from '../hooks/usePolling'

const TIER_STYLES: Record<string, string> = {
  T1: 'bg-red-500/15 text-red-400 border-red-500/30',
  T2: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  T3: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
}

function formatTimeRemaining(holdDeadline: string): string {
  const remainingMs = new Date(holdDeadline).getTime() - Date.now()
  if (remainingMs <= 0) return 'past deadline'
  const minutes = Math.round(remainingMs / 60_000)
  return minutes < 1 ? '<1 min' : `${minutes} min`
}

interface HoldQueueProps {
  hubId: string
}

export function HoldQueue({ hubId }: HoldQueueProps) {
  const { data, error, loading } = usePolling(
    () => api.heldOrders(hubId),
    5000,
    [hubId],
    hubId.length > 0,
  )

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">Batch-Hold Queue</h2>
        {loading && <span className="text-xs text-slate-500">refreshing…</span>}
      </div>

      {error && <p className="text-sm text-red-400">Couldn't load hold queue: {error.message}</p>}

      {!error && data && data.length === 0 && (
        <p className="text-sm text-slate-500">Nothing currently held for this hub.</p>
      )}

      {!error && data && data.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
              <th className="py-2 pr-4 font-medium">Order</th>
              <th className="py-2 pr-4 font-medium">Tier</th>
              <th className="py-2 pr-4 font-medium">Held Since</th>
              <th className="py-2 pr-4 font-medium">Releases In</th>
            </tr>
          </thead>
          <tbody>
            {data.map((order) => (
              <tr key={order.order_id} className="border-b border-slate-800/60">
                <td className="py-2 pr-4 font-mono text-xs text-slate-300">{order.order_id}</td>
                <td className="py-2 pr-4">
                  <span
                    className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
                      TIER_STYLES[order.sla_tier] ?? TIER_STYLES.T2
                    }`}
                  >
                    {order.sla_tier}
                  </span>
                </td>
                <td className="py-2 pr-4 text-slate-400">
                  {new Date(order.held_since).toLocaleTimeString()}
                </td>
                <td className="py-2 pr-4 text-slate-300">
                  {formatTimeRemaining(order.hold_deadline)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
