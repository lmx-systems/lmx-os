interface ConfirmModalProps {
  open: boolean
  title: string
  description: string
  confirmLabel: string
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * The two Operations Panel buttons affect live dispatch, so they get a
 * confirm step rather than firing immediately - a gap the pre-redesign
 * TriggerPanel had (one click, no confirmation).
 */
export function ConfirmModal({
  open,
  title,
  description,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)]"
      onClick={busy ? undefined : onCancel}
    >
      <div
        className="w-[440px] max-w-[90vw] rounded-[var(--radius-lg)] bg-[var(--surface)] p-7 shadow-[var(--shadow-lg)]"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-2 text-lg font-extrabold tracking-[-0.02em] text-[var(--text-primary)]">{title}</h3>
        <p className="mb-4.5 text-[13px] text-[var(--text-secondary)]">{description}</p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={busy}
            className="rounded-full bg-[var(--surface)] px-4 py-2.5 text-[13.5px] font-bold text-[var(--text-primary)] shadow-[inset_0_0_0_1.5px_var(--border-strong)] disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className="rounded-full bg-[var(--accent)] px-4 py-2.5 text-[13.5px] font-bold text-white transition-colors duration-150 hover:bg-[var(--accent-hover)] disabled:opacity-50"
          >
            {busy ? 'Running…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
