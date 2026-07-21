import { useEffect, useState } from 'react'
import { formatSecondsAgo } from '../lib/format'
import { ThemeToggle } from './ui/ThemeToggle'

interface TopBarProps {
  hubId: string
  onChangeHubId: (hubId: string) => void
  lastUpdatedAt: number | null
}

/**
 * There's still no "list hubs" endpoint on the backend (Hub rows exist in
 * Postgres, nothing exposes them - see docs/NEXT_STEPS.md), so hub
 * selection stays a text input rather than a dropdown. Restyled to look
 * like part of the console instead of a bare form field.
 */
export function TopBar({ hubId, onChangeHubId, lastUpdatedAt }: TopBarProps) {
  const [secondsAgo, setSecondsAgo] = useState(0)

  useEffect(() => {
    if (lastUpdatedAt === null) return
    setSecondsAgo(Math.round((Date.now() - lastUpdatedAt) / 1000))
    const id = setInterval(() => {
      setSecondsAgo(Math.round((Date.now() - lastUpdatedAt) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [lastUpdatedAt])

  return (
    <div className="mb-5 flex items-center gap-4 border-b border-[var(--border)] pb-4.5">
      <div className="flex items-center gap-2.5 text-[15px] font-medium">
        <img src="/lmx-mark.png" alt="LMX" className="h-[26px] w-[26px] rounded-[7px]" />
        LMX OS
      </div>
      <span className="rounded-full border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-0.5 text-[11.5px] font-medium text-[var(--text-secondary)]">
        Orchestrator console
      </span>

      <input
        id="hub-id"
        type="text"
        aria-label="Hub ID"
        value={hubId}
        onChange={(e) => onChangeHubId(e.target.value)}
        placeholder="Paste a hub UUID"
        className="w-72 rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-2)] px-3 py-1.5 text-[13.5px] font-medium text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
      />

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
