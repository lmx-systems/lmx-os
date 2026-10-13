import { AlertTriangle } from 'lucide-react'
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
      className={`rounded-[24px] bg-[var(--surface)] px-6 py-5 ${
        risk ? 'shadow-[inset_0_0_0_1.5px_var(--needs-dim)]' : ''
      }`}
    >
      <div className="mb-1 flex items-center gap-1.5 text-[13px] font-medium text-[var(--text-secondary)]">
        {label}
        {stale && (
          <AlertTriangle
            size={14}
            className="text-[var(--amber)]"
            aria-label="Last refresh failed - showing the most recent value we successfully loaded"
          />
        )}
      </div>
      <div className={`text-[30px] font-extrabold leading-tight tracking-[-0.04em] tabular-nums ${risk ? 'text-[var(--needs)]' : ''}`}>
        {value}
      </div>
      {sub && <div className="mt-1 text-xs text-[var(--text-secondary)]">{sub}</div>}
      {children}
    </div>
  )
}
