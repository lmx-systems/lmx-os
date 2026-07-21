import type { ClientProfileView } from '../lib/types'
import { ThemeToggle } from './ThemeToggle'

interface TopBarProps {
  profile: ClientProfileView
  onLogout: () => void
}

export function TopBar({ profile, onLogout }: TopBarProps) {
  return (
    <header className="flex items-center justify-between border-b border-[var(--border)] bg-[var(--surface)] px-6 py-3">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-[var(--radius)] bg-[var(--accent)] text-sm font-bold text-white">
          LX
        </div>
        <div>
          <div className="text-[14.5px] font-semibold text-[var(--text-primary)]">{profile.name}</div>
          <div className="text-xs text-[var(--text-muted)]">{profile.portal_email}</div>
        </div>
      </div>
      <div className="flex items-center gap-2.5">
        <ThemeToggle />
        <button
          onClick={onLogout}
          className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--surface-2)]"
        >
          Sign out
        </button>
      </div>
    </header>
  )
}
