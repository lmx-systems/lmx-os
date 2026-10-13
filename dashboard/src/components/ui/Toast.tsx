export function Toast({ message }: { message: string | null }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={`fixed bottom-6 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-2.5 rounded-full bg-[var(--surface)] py-2.5 pl-2.5 pr-5 text-[13.5px] font-semibold text-[var(--text-primary)] shadow-[var(--shadow-lg)] transition-all duration-200 ${
        message ? 'translate-y-0 opacity-100' : 'pointer-events-none translate-y-3 opacity-0'
      }`}
    >
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--lime)]" aria-hidden="true">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
          <path d="m5 12 4 4 10-10" />
        </svg>
      </span>
      {message}
    </div>
  )
}
