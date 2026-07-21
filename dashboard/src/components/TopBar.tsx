import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatSecondsAgo } from '../lib/format'
import type { HubSummary } from '../lib/types'
import { ThemeToggle } from './ui/ThemeToggle'

interface TopBarProps {
  hubId: string
  onChangeHubId: (hubId: string) => void
  lastUpdatedAt: number | null
}

/**
 * GET /hubs (docs/ROADMAP.md D1) backs a real dropdown now. Falls back to
 * the old raw-UUID text input if the list ever fails to load or comes
 * back empty (e.g. no hubs seeded yet) - ops shouldn't be blocked from
 * targeting a hub just because this convenience lookup had a bad moment.
 */
export function TopBar({ hubId, onChangeHubId, lastUpdatedAt }: TopBarProps) {
  const [secondsAgo, setSecondsAgo] = useState(0)
  const [hubs, setHubs] = useState<HubSummary[] | null>(null)

  useEffect(() => {
    (async () => {
      try {
        setHubs(await api.listHubs())
      } catch {
        setHubs([])
      }
    })()
  }, [])

  useEffect(() => {
    if (lastUpdatedAt === null) return
    setSecondsAgo(Math.round((Date.now() - lastUpdatedAt) / 1000))
    const id = setInterval(() => {
      setSecondsAgo(Math.round((Date.now() - lastUpdatedAt) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [lastUpdatedAt])

  const useDropdown = hubs !== null && hubs.length > 0

  return (
    <div className="mb-5 flex items-center gap-4 border-b border-[var(--border)] pb-4.5">
      <div className="flex items-center gap-2.5 text-[15px] font-medium">
        <img src="/lmx-mark.png" alt="LMX" className="h-[26px] w-[26px] rounded-[7px]" />
        LMX OS
      </div>
      <span className="rounded-full border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-0.5 text-[11.5px] font-medium text-[var(--text-secondary)]">
        Orchestrator console
      </span>

      {useDropdown ? (
        <select
          id="hub-id"
          aria-label="Hub"
          value={hubId}
          onChange={(e) => onChangeHubId(e.target.value)}
          className="w-72 rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-2)] px-3 py-1.5 text-[13.5px] font-medium text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
        >
          <option value="">Select a hub…</option>
          {hubs!.map((hub) => (
            <option key={hub.hub_id} value={hub.hub_id}>
              {hub.name}
            </option>
          ))}
        </select>
      ) : (
        <input
          id="hub-id"
          type="text"
          aria-label="Hub ID"
          value={hubId}
          onChange={(e) => onChangeHubId(e.target.value)}
          placeholder="Paste a hub UUID"
          className="w-72 rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-2)] px-3 py-1.5 text-[13.5px] font-medium text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
        />
      )}

      <div className="flex-1" />

      {lastUpdatedAt !== null && (
        <div className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
          <span className="h-[7px] w-[7px] rounded-full bg-[var(--green)] shadow-[0_0_0_3px_var(--green-dim)]" />
          Live · updated {formatSecondsAgo(secondsAgo)}
        </div>
      )}

      <ThemeToggle />
    </div>
  )
}
