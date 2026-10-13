import type { ClientProfileView } from '../lib/types'

export interface NavItem {
  id: string
  label: string
  active: boolean
  onSelect: () => void
}

interface TopBarProps {
  profile: ClientProfileView
  onLogout: () => void
  nav: NavItem[]
}

/**
 * The portal's one bar (docs/DESIGN_SYSTEM.md): the mark, the sections as a
 * pill group with the open one in ink, then who is signed in.
 */
export function TopBar({ profile, onLogout, nav }: TopBarProps) {
  return (
    <header className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-5 gap-y-3 px-4 pt-6 sm:px-8 print:hidden">
      <div className="flex items-center gap-2 text-xl font-extrabold tracking-[-0.04em]">
        <img src="/lmx-mark.png" alt="" className="h-[26px] w-[26px] rounded-[8px]" />
        lmx
      </div>
      <nav aria-label="Portal" className="flex flex-wrap gap-1 rounded-full bg-[var(--surface)] p-1">
        {nav.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-current={item.active ? 'page' : undefined}
            onClick={item.onSelect}
            className={`rounded-full px-4 py-1.5 text-[13.5px] transition-colors ${
              item.active
                ? 'bg-[var(--accent)] font-semibold text-white'
                : 'font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-2 rounded-full bg-[var(--surface)] py-1 pl-1 pr-1 text-[13px] font-semibold">
        <span
          aria-hidden="true"
          className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--lime)] text-[11px] font-bold text-[var(--lime-ink)]"
        >
          {profile.name.trim().charAt(0).toUpperCase() || '?'}
        </span>
        <span className="flex flex-col leading-tight">
          <span>{profile.name}</span>
          <span className="text-[11.5px] font-medium text-[var(--text-muted)]">
            {profile.email}
            {profile.role === 'admin' && ' · Admin'}
          </span>
        </span>
        <button
          onClick={onLogout}
          className="ml-1 rounded-full px-3 py-1 text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
        >
          Sign out
        </button>
      </div>
    </header>
  )
}
