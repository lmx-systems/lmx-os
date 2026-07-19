import type { DriverState } from '../../lib/types'

const TIER_STYLES: Record<string, string> = {
  T1: 'bg-[var(--red-dim)] text-[var(--red)]',
  T2: 'bg-[var(--amber-dim)] text-[var(--amber)]',
  T3: 'bg-[var(--surface-2)] text-[var(--gray)]',
}

export function TierBadge({ tier }: { tier: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium ${
        TIER_STYLES[tier] ?? TIER_STYLES.T2
      }`}
    >
      {tier}
    </span>
  )
}

const STATUS_DOT_COLOR: Record<DriverState['status'], string> = {
  available: 'bg-[var(--green)]',
  offered: 'bg-[var(--amber)]',
  en_route: 'bg-[var(--blue)]',
  on_break: 'bg-[var(--amber)]',
  off_shift: 'bg-[var(--gray)]',
}

const STATUS_LABEL: Record<DriverState['status'], string> = {
  available: 'Available',
  offered: 'Offer pending',
  en_route: 'En route',
  on_break: 'On break',
  off_shift: 'Off shift',
}

export function StatusLabel({ status }: { status: DriverState['status'] }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-[var(--text-primary)]">
      <span className={`h-[7px] w-[7px] rounded-full ${STATUS_DOT_COLOR[status]}`} />
      {STATUS_LABEL[status]}
    </span>
  )
}
