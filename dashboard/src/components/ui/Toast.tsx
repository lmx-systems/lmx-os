export function Toast({ message }: { message: string | null }) {
  return (
    <div
      className={`fixed bottom-6 left-1/2 z-[60] -translate-x-1/2 rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-3)] px-4.5 py-2.5 text-[13px] font-medium text-[var(--text-primary)] shadow-[var(--shadow-lg)] transition-all duration-200 ${
        message ? 'translate-y-0 opacity-100' : 'translate-y-3 opacity-0'
      }`}
    >
      {message}
    </div>
  )
}
