import { useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../lib/api'
import { setToken } from '../lib/auth'

interface LoginPageProps {
  onLoggedIn: () => void
}

export function LoginPage({ onLoggedIn }: LoginPageProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // 'forgot' swaps this card for the reset-request form rather than navigating
  // away - someone who mistyped their password and someone who forgot it are the
  // same person moments apart, so making them lose the page would be unkind.
  const [mode, setMode] = useState<'signin' | 'forgot' | 'requested'>('signin')

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

  async function handleForgot(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await api.requestPasswordReset(email)
      // Always lands here, whether or not the address has an account - the
      // server answers identically on purpose, so the UI must not imply it
      // learned anything either.
      setMode('requested')
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? 'Too many attempts from this network. Please try again later.'
          : 'Something went wrong. Please try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (mode === 'requested') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--bg-page)] px-4">
        <div className="w-full max-w-sm rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 text-center shadow-[var(--shadow-md)]">
          <img src="/lmx-mark.png" alt="LMX" className="mx-auto mb-4 h-8 w-8 rounded-[var(--radius)]" />
          <div className="text-[15px] font-semibold text-[var(--text-primary)]">Check your email</div>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            If that address has an LMX account, we've sent a link to reset the password. It's valid
            for one hour.
          </p>
          <button
            onClick={() => setMode('signin')}
            className="mt-4 text-sm font-medium text-[var(--accent)] hover:underline"
          >
            Back to sign in
          </button>
        </div>
      </div>
    )
  }

  if (mode === 'forgot') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--bg-page)] px-4">
        <div className="w-full max-w-sm rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 shadow-[var(--shadow-md)]">
          <div className="mb-5 flex items-center gap-2">
            <img src="/lmx-mark.png" alt="LMX" className="h-8 w-8 rounded-[var(--radius)]" />
            <div>
              <div className="text-[15px] font-semibold text-[var(--text-primary)]">Reset your password</div>
              <div className="text-xs text-[var(--text-muted)]">We'll email you a link</div>
            </div>
          </div>

          <form onSubmit={handleForgot} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
              Email
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                placeholder="you@yourcompany.com"
              />
            </label>

            {error && <div className="text-xs font-medium text-[var(--red)]">{error}</div>}

            <button
              type="submit"
              disabled={submitting}
              className="mt-1 rounded-[var(--radius)] bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {submitting ? 'Sending…' : 'Send reset link'}
            </button>
            <button
              type="button"
              onClick={() => setMode('signin')}
              className="text-center text-xs font-medium text-[var(--accent)] hover:underline"
            >
              Back to sign in
            </button>
          </form>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-page)] px-4">
      <div className="w-full max-w-sm rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 shadow-[var(--shadow-md)]">
        <div className="mb-6 flex items-center gap-2">
          <img src="/lmx-mark.png" alt="LMX" className="h-8 w-8 rounded-[var(--radius)]" />
          <div>
            <div className="text-[15px] font-semibold text-[var(--text-primary)]">LMX Client Portal</div>
            <div className="text-xs text-[var(--text-muted)]">Sign in to your account</div>
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
              placeholder="you@yourcompany.com"
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

          <button
            type="button"
            onClick={() => setMode('forgot')}
            className="text-center text-xs font-medium text-[var(--accent)] hover:underline"
          >
            Forgot your password?
          </button>
        </form>

        {/* Copy corrected: this used to read "one login per company account",
            which stopped being true when C4 made portal logins per-user. It also
            said to contact support for help, which was the only recovery path
            before this reset flow existed. */}
        <p className="mt-4 text-center text-xs text-[var(--text-muted)]">
          Your company can have as many logins as it needs — an admin on your
          account can add them.
        </p>
      </div>
    </div>
  )
}
