// Same shape as dashboard/src/components/ui/Badge.tsx's TierBadge, plus
// HOT_SHOT (Phase 8) - a distinct, purple, "premium" style rather than
// falling back to T2's amber, since it's the highest-urgency tier, not a
// middling one.
const TIER_STYLES: Record<string, string> = {
  HOT_SHOT: 'bg-[#f1e8fd] text-[#7c3aed]',
  T1: 'bg-[var(--red-dim)] text-[var(--red)]',
  T2: 'bg-[var(--amber-dim)] text-[var(--amber)]',
  T3: 'bg-[var(--surface-2)] text-[var(--gray)]',
}

const TIER_LABEL: Record<string, string> = {
  HOT_SHOT: 'Hot Shot',
}

export function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return <span className="text-xs text-[var(--text-muted)]">—</span>
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium ${
        TIER_STYLES[tier] ?? TIER_STYLES.T2
      }`}
    >
      {TIER_LABEL[tier] ?? tier}
    </span>
  )
}
