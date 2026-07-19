import type { ReactNode } from 'react'

interface KpiCardProps {
  label: string
  value: ReactNode
  sub?: ReactNode
  risk?: boolean
  stale?: boolean
  children?: ReactNode
}

export function KpiCard({ label, value, sub, risk = false, stale = false, children }: KpiCardProps) {
  return (
    <div
      className={`rounded-[var(--radius-lg)] border bg-[var(--surface)] p-3.5 ${
        risk ? 'border-[#f0c4c6]' : 'border-[var(--border)]'
      }`}
    >
      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)]">
        {label}
        {stale && (
          <span
            className="text-[var(--amber)]"
            title="Last refresh failed - showing the most recent value we successfully loaded"
          >
            ⚠
          </span>
        )}
      </div>
      <div className={`text-[26px] font-medium tracking-tight ${risk ? 'text-[var(--red)]' : ''}`}>
        {value}
      </div>
      {sub && <div className="mt-1 text-xs text-[var(--text-muted)]">{sub}</div>}
      {children}
    </div>
  )
}
