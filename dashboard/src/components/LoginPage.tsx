import { useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../lib/api'
import { setToken } from '../lib/auth'

interface LoginPageProps {
  onLoggedIn: () => void
}

// Real per-account ops auth (docs/ROADMAP.md S1), replacing the old
// shared X-API-Key stopgap - mirrors client-portal/src/components/
// LoginPage.tsx exactly, since this is the same JWT-session shape.
export function LoginPage({ onLoggedIn }: LoginPageProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const token = await api.login(email, password)
      setToken(token.access_token)
      onLoggedIn()
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'Invalid email or password.' : 'Something went wrong. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-page)] px-4">
      <div className="w-full max-w-sm rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 shadow-[var(--shadow-md)]">
        <div className="mb-6 flex items-center gap-2">
          <img src="/lmx-mark.png" alt="LMX" className="h-8 w-8 rounded-[var(--radius)]" />
          <div>
            <div className="text-[15px] font-semibold text-[var(--text-primary)]">LMX OS</div>
            <div className="text-xs text-[var(--text-muted)]">Sign in to the orchestrator console</div>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
            Email
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              placeholder="you@lmxit.com"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
            Password
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              placeholder="••••••••"
            />
          </label>

          {error && <div className="text-xs font-medium text-[var(--red)]">{error}</div>}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 rounded-[var(--radius)] bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white transition-colors duration-150 hover:bg-[var(--accent-hover)] disabled:opacity-60"
          >
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="mt-4 text-center text-xs text-[var(--text-muted)]">
          Internal LMX ops access only. Contact an existing ops user for an account.
        </p>
      </div>
    </div>
  )
}
