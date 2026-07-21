import { useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../lib/api'
import { setOpsToken } from '../lib/auth'

interface OpsLoginPageProps {
  onLoggedIn: () => void
}

/**
 * Per-user ops sign-in (roadmap item S1). Shown only when the backend
 * rejects requests with 401 - a backend running in open mode (no
 * API_SHARED_SECRET, no ops users yet) never triggers it, so local dev
 * is unchanged.
 */
export function OpsLoginPage({ onLoggedIn }: OpsLoginPageProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const token = await api.opsLogin(email, password)
      setOpsToken(token.access_token)
      onLoggedIn()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Invalid email or password.')
      } else if (err instanceof ApiError && err.status === 429) {
        setError('Too many attempts - try again in a few minutes.')
      } else {
        setError('Something went wrong. Please try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6">
        <div className="mb-6 flex items-center gap-3">
          <img src="/lmx-stamp.png" alt="LMX" className="h-10 w-auto" />
          <div>
            <div className="text-[15px] font-semibold text-[var(--text-primary)]">LMX OS</div>
            <div className="text-xs text-[var(--text-muted)]">Orchestrator console — sign in</div>
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
              className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              placeholder="you@lmx.example"
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
              className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              placeholder="••••••••••••"
            />
          </label>

          {error && <div className="text-xs font-medium text-[var(--red)]">{error}</div>}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 rounded-[var(--radius)] bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="mt-4 text-center text-xs text-[var(--text-muted)]">
          Internal use only. Ask an admin to create your account.
        </p>
      </div>
    </div>
  )
}
