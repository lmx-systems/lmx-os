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
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(20,23,28,0.45)]"
      onClick={busy ? undefined : onCancel}
    >
      <div
        className="w-[420px] max-w-[90vw] rounded-[var(--radius-lg)] border border-[var(--border-strong)] bg-[var(--surface)] p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-2 text-[15px] font-medium text-[var(--text-primary)]">{title}</h3>
        <p className="mb-4.5 text-[13px] text-[var(--text-secondary)]">{description}</p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={busy}
            className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-2)] px-3.5 py-2 text-[13px] font-medium text-[var(--text-primary)] disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className="rounded-[var(--radius)] border border-[var(--accent)] bg-[var(--accent)] px-3.5 py-2 text-[13px] font-medium text-white disabled:opacity-50"
          >
            {busy ? 'Running…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
