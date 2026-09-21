export interface TabSpec {
  id: string
  label: string
  /** Shown as a count beside the label. Omit or 0 to show nothing. */
  badge?: number
}

/**
 * Two or three views of the same column, so a dispatcher's working surface is
 * not a scroll past work that can wait (docs/ROADMAP_1.5.md CON-1).
 *
 * **The badge is the point, not decoration.** Separating "what to do now" from
 * "what to record about what already happened" makes the board workable and
 * would, on its own, mean the second tab is never opened — trading a cluttered
 * board for work that silently stops getting done. The count is what keeps the
 * hidden half honest.
 */
export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: TabSpec[]
  active: string
  onChange: (id: string) => void
}) {
  // The bar only. The caller renders each tab's content conditionally, which is
  // what makes the hidden panels lazy: they mount when the tab opens, and each
  // fetches on mount.
  return (
    <div
      role="tablist"
      className="flex gap-1 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-2)] p-1"
    >
      {tabs.map((tab) => {
        const selected = tab.id === active
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab.id)}
            className={`flex-1 rounded-[calc(var(--radius)-2px)] px-3 py-1.5 text-[13px] font-medium transition-colors ${
              selected
                ? "bg-[var(--surface)] text-[var(--text-primary)] shadow-[var(--shadow-sm)]"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            {tab.label}
            {tab.badge ? (
              <span
                className={`ml-1.5 rounded-full px-1.5 py-0.5 text-[10.5px] tabular-nums ${
                  selected
                    ? "bg-[var(--accent-dim)] text-[var(--accent)]"
                    : "bg-[var(--border)] text-[var(--text-secondary)]"
                }`}
              >
                {tab.badge}
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}
