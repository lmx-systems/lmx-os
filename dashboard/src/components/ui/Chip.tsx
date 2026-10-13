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
      aria-pressed={active}
      className={`whitespace-nowrap rounded-full px-3 py-1.5 text-[12.5px] font-semibold transition-colors duration-150 ${
        active
          ? 'bg-[var(--accent)] text-white'
          : 'bg-[var(--surface-2)] text-[var(--text-secondary)] shadow-[inset_0_0_0_1px_var(--border)] hover:text-[var(--text-primary)]'
      }`}
    >
      {label}
    </button>
  )
}
