interface ChipProps {
  label: string
  active: boolean
  onClick: () => void
}

export function Chip({ label, active, onClick }: ChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium ${
        active
          ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
          : 'border-[var(--border-strong)] bg-[var(--surface-2)] text-[var(--text-secondary)]'
      }`}
    >
      {label}
    </button>
  )
}
