import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { OverrideReasonOption, OverrideResult } from '../lib/types'

type OverrideAction = 'release' | 'hold'

const WORDING: Record<OverrideAction, { title: string; button: string; busy: string; done: string }> = {
  release: { title: 'Release early', button: 'Release', busy: 'Releasing…', done: 'Released' },
  hold: { title: 'Put back in the queue', button: 'Put back', busy: 'Putting back…', done: 'Back in the queue' },
}

function doneText(action: OverrideAction, result: OverrideResult): string {
  const done = WORDING[action].done
  if (result.contradicted_the_system) {
    return `${done}. The queue had decided to ${result.system_action} — recorded as a disagreement.`
  }
  if (result.system_decision_known) return `${done}. The queue had reached the same conclusion.`
  return `${done}. No cycle had recorded a decision, so this is not recorded as a disagreement.`
}

/**
 * Overrule the queue on one order, with a reason (docs/ROADMAP_1.5.md CON-2).
 *
 * *"No override completes without a reason."* The submit button stays disabled
 * until a code is chosen, and `other` will not submit without a note — but that
 * is a courtesy, not the guarantee. The server refuses both, and the database
 * refuses a row without a reason underneath that, because a UI rule is only ever
 * a rule about this UI.
 *
 * `release` lets a held order go now (the hold queue's explanation row); `hold`
 * puts a released order back under the queue's rules (the exceptions queue's
 * "nobody has it" row). The server takes `hold` only from `queued` and says so
 * with a 409 otherwise.
 */
export function OverrideForm({
  orderId,
  action,
  warning,
  onDone,
}: {
  orderId: string
  action: OverrideAction
  // Said above the form when the override won't do what it sounds like.
  warning?: string
  onDone?: () => void
}) {
  const [reasons, setReasons] = useState<OverrideReasonOption[] | null>(null)
  const [code, setCode] = useState('')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [refused, setRefused] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api
      .overrideReasons()
      .then((r) => live && setReasons(r))
      .catch(() => live && setReasons([]))
    return () => {
      live = false
    }
  }, [])

  const chosen = reasons?.find((r) => r.code === code)
  const noteMissing = !!chosen?.note_required && note.trim() === ''
  const canSubmit = code !== '' && !noteMissing && !submitting
  const words = WORDING[action]

  async function submit() {
    setSubmitting(true)
    setRefused(null)
    try {
      const result = await api.overrideOrder(orderId, {
        action,
        reason_code: code,
        note: note.trim() || undefined,
      })
      setDone(doneText(action, result))
      onDone?.()
    } catch (e) {
      setRefused(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  if (done) {
    return <p className="mt-2.5 border-t border-[var(--border)] pt-2.5 text-[12px] text-[var(--accent)]">{done}</p>
  }

  return (
    <div className="mt-2.5 border-t border-[var(--border)] pt-2.5">
      {warning && <p className="mb-1.5 text-[11px] text-[var(--amber)]">{warning}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
          {words.title}
        </span>
        <select
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-[12px] text-[var(--text-primary)]"
        >
          <option value="">Choose a reason…</option>
          {(reasons ?? []).map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </select>
        {chosen?.note_required && (
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Say what the reason was"
            className="min-w-[14rem] flex-1 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-[12px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-muted)]"
          />
        )}
        <button
          onClick={submit}
          disabled={!canSubmit}
          className="rounded-full bg-[var(--accent)] px-2.5 py-1 text-[12px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting ? words.busy : words.button}
        </button>
      </div>
      {refused && <p className="mt-1.5 text-[12px] text-[var(--red)]">{refused}</p>}
    </div>
  )
}
