import { Moon, Sun } from 'lucide-react'
import { useTheme } from '../lib/theme'

export function ThemeToggle() {
  const [theme, toggle] = useTheme()

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle dark mode"
      className="flex h-7 w-7 items-center justify-center rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-2)] text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--surface-3)]"
    >
      {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
    </button>
  )
}
