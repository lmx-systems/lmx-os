import { useState } from 'react'
import { api } from '../lib/api'
import type { TermsAcceptanceView } from '../lib/types'

/**
 * Shown when this company must accept updated terms before sending more orders
 * (docs/ROADMAP.md L8).
 *
 * Sits above the tabs rather than inside the order form, because the block applies to
 * every way of placing an order - the form, the paste, the spreadsheet, and their own
 * system over an API key. A prompt only on the form would leave the other three
 * returning a 409 with nothing on screen to explain it.
 *
 * **A member sees the same banner without the button.** They cannot accept - binding the
 * company to a contract is an admin act - but they are the ones most likely to hit the
 * refusal, so telling them what happened and who can fix it is the whole point. A
 * dead-end 409 is what this exists to prevent.
 */
export function TermsBanner({
  state,
  onAccepted,
}: {
  state: TermsAcceptanceView
  onAccepted: (next: TermsAcceptanceView) => void
}) {
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  if (!state.acceptance_required) return null

  async function accept() {
    setSaving(true)
    setFailed(false)
    try {
      onAccepted(await api.acceptTerms())
    } catch {
      setFailed(true)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      role="alert"
      className="border-b border-[var(--border)] bg-[var(--surface-2,#fbf6e8)] px-4 py-3"
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <p className="flex-1 text-[var(--text-primary)]">
          <span className="font-medium">Our terms have been updated.</span>{' '}
          {state.can_accept
            ? 'Please review and accept them to keep sending orders.'
            : 'An administrator on your account needs to accept them before new orders can be sent.'}
        </p>

        <a
          href={state.terms_path}
          target="_blank"
          rel="noreferrer"
          className="font-medium text-[var(--accent)] hover:underline"
        >
          Read the terms
        </a>
        <a
          href={state.privacy_path}
          target="_blank"
          rel="noreferrer"
          className="font-medium text-[var(--accent)] hover:underline"
        >
          Privacy policy
        </a>

        {state.can_accept && (
          <button
            type="button"
            onClick={accept}
            disabled={saving}
            className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          >
            {saving ? 'Accepting…' : 'Accept and continue'}
          </button>
        )}

        {failed && (
          <p className="text-xs text-[var(--danger,#b3261e)]">
            We couldn&rsquo;t record that. Please try again.
          </p>
        )}
      </div>
    </div>
  )
}
