import { useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../lib/api'

interface ResetPasswordPageProps {
  token: string
  onDone: () => void
}

/**
 * Set a new password from an emailed link (docs/ROADMAP.md L14).
 *
 * Reached at /reset-password?token=… with no session, because a locked-out user
 * has none by definition. Same public-route treatment as /signup - nginx.conf's
 * SPA fallback already serves index.html for it.
 *
 * The failure copy deliberately does not distinguish an expired link from a used
 * one or a wrong one: the server doesn't tell us, on purpose, and there is
 * nothing a legitimate user does differently on knowing. Either way they need
 * another link, so that is what the message says.
 */
export function ResetPasswordPage({ token, onDone }: ResetPasswordPageProps) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    // Checked here rather than server-side: a typo in the confirmation is a
    // client-side mistake, and round-tripping it would consume the single-use
    // token for nothing - leaving them to request a whole new link because they
    // mistyped twice.
    if (password !== confirm) {
      setError("Those don't match — please retype them.")
      return
    }

    setSubmitting(true)
    try {
      await api.confirmPasswordReset(token, password)
      setDone(true)
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 400
          ? 'That link has expired or already been used. Please request a new one.'
          : 'Something went wrong. Please try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-page)] px-4">
      <div className="w-full max-w-sm rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 shadow-[var(--shadow-md)]">
        <div className="mb-5 flex items-center gap-2">
          <img src="/lmx-mark.png" alt="LMX" className="h-8 w-8 rounded-[var(--radius)]" />
          <div>
            <div className="text-[15px] font-semibold text-[var(--text-primary)]">
              {done ? 'Password changed' : 'Choose a new password'}
            </div>
            {!done && <div className="text-xs text-[var(--text-muted)]">At least 10 characters</div>}
          </div>
        </div>

        {done ? (
          <>
            <p className="text-sm text-[var(--text-secondary)]">
              You can sign in with your new password now.
            </p>
            <button
              onClick={onDone}
              className="mt-4 w-full rounded-[var(--radius)] bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white"
            >
              Sign in
            </button>
          </>
        ) : (
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
              New password
              <input
                type="password"
                required
                minLength={10}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
              Type it again
              <input
                type="password"
                required
                minLength={10}
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              />
            </label>

            {error && (
              <p role="alert" className="text-[13px] text-[var(--danger,#b3261e)]">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="mt-1 rounded-[var(--radius)] bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {submitting ? 'Saving…' : 'Set new password'}
            </button>

            <button
              type="button"
              onClick={onDone}
              className="text-center text-xs font-medium text-[var(--accent)] hover:underline"
            >
              Back to sign in
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
