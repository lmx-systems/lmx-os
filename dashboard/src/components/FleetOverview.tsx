import { api } from '../lib/api'
import { usePolling } from '../hooks/usePolling'
import type { DriverState } from '../lib/types'

const STATUS_STYLES: Record<DriverState['status'], string> = {
  available: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  en_route: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  on_break: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  off_shift: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
}

function StatusBadge({ status }: { status: DriverState['status'] }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}
    >
      {status.replace('_', ' ')}
    </span>
  )
}

interface FleetOverviewProps {
  hubId: string
}

export function FleetOverview({ hubId }: FleetOverviewProps) {
  const { data, error, loading } = usePolling(
    () => api.fleetOverview(hubId),
    5000,
    [hubId],
    hubId.length > 0,
  )

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">Fleet Overview</h2>
        {loading && <span className="text-xs text-slate-500">refreshing…</span>}
      </div>

      {error && (
        <p className="text-sm text-red-400">Couldn't load fleet state: {error.message}</p>
      )}

      {!error && data && data.length === 0 && (
        <p className="text-sm text-slate-500">No drivers registered for this hub yet.</p>
      )}

      {!error && data && data.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
              <th className="py-2 pr-4 font-medium">Driver</th>
              <th className="py-2 pr-4 font-medium">Status</th>
              <th className="py-2 pr-4 font-medium">Load / Capacity</th>
              <th className="py-2 pr-4 font-medium">Current Route</th>
            </tr>
          </thead>
          <tbody>
            {data.map((driver) => (
              <tr key={driver.driver_id} className="border-b border-slate-800/60">
                <td className="py-2 pr-4 font-mono text-xs text-slate-300">{driver.driver_id}</td>
                <td className="py-2 pr-4">
                  <StatusBadge status={driver.status} />
                </td>
                <td className="py-2 pr-4 text-slate-300">
                  {driver.load_units} / {driver.capacity_units}
                </td>
                <td className="py-2 pr-4 font-mono text-xs text-slate-500">
                  {driver.current_route_id ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
