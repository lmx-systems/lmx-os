import { useMemo, useState } from 'react'
import { Card } from './ui/Card'
import { Chip } from './ui/Chip'
import { StatusLabel } from './ui/Badge'
import { idInitials, nameInitials, truncateId } from '../lib/format'
import type { DriverState } from '../lib/types'

interface FleetRosterProps {
  data: DriverState[] | null
  error: Error | null
  loading: boolean
}

const STATUSES = ['all', 'available', 'offered', 'en_route', 'on_break', 'off_shift'] as const
const STATUS_LABEL: Record<(typeof STATUSES)[number], string> = {
  all: 'All',
  available: 'Available',
  offered: 'Offer pending',
  en_route: 'En route',
  on_break: 'On break',
  off_shift: 'Off shift',
}

export function FleetRoster({ data, error, loading }: FleetRosterProps) {
  const [status, setStatus] = useState<(typeof STATUSES)[number]>('all')

  const rows = useMemo(() => {
    if (!data) return []
    return data.filter((d) => status === 'all' || d.status === status)
  }, [data, status])

  const availableCount = data?.filter((d) => d.status === 'available').length ?? 0

  return (
    <Card
      title="Fleet roster"
      meta={loading ? 'refreshing…' : data ? `${data.length} drivers · ${availableCount} available` : undefined}
    >
      {error && <p className="text-sm text-[var(--red)]">Couldn't load fleet state: {error.message}</p>}

      {!error && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {STATUSES.map((s) => (
              <Chip key={s} label={STATUS_LABEL[s]} active={status === s} onClick={() => setStatus(s)} />
            ))}
          </div>

          {data && data.length === 0 && (
            <p className="py-6 text-center text-sm text-[var(--text-muted)]">
              No drivers registered for this hub yet.
            </p>
          )}

          {data && data.length > 0 && rows.length === 0 && (
            <p className="py-6 text-center text-sm text-[var(--text-muted)]">No drivers match this filter.</p>
          )}

          <div className="flex flex-col">
            {rows.map((driver) => {
              const pct = driver.capacity_units > 0 ? Math.round((driver.load_units / driver.capacity_units) * 100) : 0
              return (
                <div
                  key={driver.driver_id}
                  className="flex items-center gap-2.5 border-t border-[var(--border)] py-2.5 first:border-t-0"
                >
                  <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-[var(--surface-2)] text-[11px] font-semibold text-[var(--text-secondary)]">
                    {driver.name ? nameInitials(driver.name) : idInitials(driver.driver_id)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium text-[var(--text-primary)]" title={driver.driver_id}>
                      {driver.name ?? (
                        <span className="font-mono text-[12px] text-[var(--text-secondary)]">
                          {truncateId(driver.driver_id)}
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-[var(--text-muted)]">
                      <StatusLabel status={driver.status} />
                      {driver.current_route_id && <span>· route {truncateId(driver.current_route_id, 6)}</span>}
                    </div>
                  </div>
                  <div
                    className="h-[5px] w-16 flex-shrink-0 overflow-hidden rounded-full bg-[var(--surface-2)]"
                    title={`${driver.load_units} / ${driver.capacity_units} capacity`}
                  >
                    <div className="h-full bg-[var(--accent)]" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}
    </Card>
  )
}
