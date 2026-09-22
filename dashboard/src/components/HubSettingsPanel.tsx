import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import { US_STATE_CODES } from '../lib/types'
import type { HubClosure, HubSettings } from '../lib/types'

/**
 * Which state is this hub in? (docs/ROADMAP_AUDIT_2026-09.md.)
 *
 * `Hub.state_code` selects a driver's overtime rule and was set by **nothing** —
 * not the app, not a script, not a test. The column's own comment said why:
 * *"no Hub creation/edit API or UI exists yet"*. This is that UI.
 *
 * It shows the rule as well as the code, because **"no state set" and "a state
 * with no rule registered" produce identical payroll** and only one of them is
 * somebody's oversight. Without the rule on screen, setting the state would look
 * like it had done nothing.
 *
 * Deliberately does not offer to register a rule. That needs a written legal
 * opinion and a business decision — see `docs/PAYROLL_STATE_OT_RESEARCH.md` —
 * and a dropdown that implied otherwise would invite somebody to guess at
 * overtime law.
 *
 * **Closures live here too** rather than in a panel of their own. Both are
 * standing facts about one hub that somebody sets once and rarely revisits, and
 * `POST/GET/DELETE /admin/hubs/{id}/closures` had existed since `R6` with
 * nothing calling any of them (`docs/ROADMAP_AUDIT_2026-09.md`). A closed day
 * makes the optimizer skip dispatch and the nightly job skip the day, so it is
 * the same kind of switch as the state code: invisible until it matters, and
 * then it matters a lot.
 */
export function HubSettingsPanel({
  hubId,
  isAdmin,
  onToast,
}: {
  hubId: string
  isAdmin: boolean
  onToast: (message: string) => void
}) {
  const [hub, setHub] = useState<HubSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let live = true
    if (!hubId) return
    api
      .hubSettings(hubId)
      .then((h) => live && setHub(h))
      .catch((e) => live && setError((e as Error).message))
    return () => {
      live = false
    }
  }, [hubId])

  async function setState(code: string) {
    setSaving(true)
    setError(null)
    try {
      const updated = await api.updateHub(hubId, { state_code: code || null })
      setHub(updated)
      onToast(
        code
          ? `${updated.name} is in ${code}. Overtime rule: ${updated.overtime_rule}.`
          : `${updated.name} has no state set. Federal overtime applies.`,
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (error && !hub) {
    return (
      <Card title="Hub settings">
        <p className="text-sm text-[var(--red)]">Couldn't load: {error}</p>
      </Card>
    )
  }
  if (!hub) return null

  return (
    <Card title="Hub settings" meta={hub.name}>
      <div className="space-y-2">
        <label className="block">
          <span className="mb-0.5 block text-[11px] font-medium text-[var(--text-muted)]">
            State
          </span>
          <select
            value={hub.state_code ?? ''}
            disabled={!isAdmin || saving}
            onChange={(e) => setState(e.target.value)}
            className="w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[13px] text-[var(--text-primary)] disabled:opacity-60"
          >
            <option value="">Not set</option>
            {US_STATE_CODES.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </label>

        <p className="text-[11px] text-[var(--text-muted)]">
          Overtime rule in force:{' '}
          <span className="font-medium text-[var(--text-secondary)]">
            {hub.overtime_rule === 'FederalWeeklyOvertimeRule'
              ? 'federal only (1.5× past 40 hours a week)'
              : hub.overtime_rule}
          </span>
          {hub.state_code && hub.overtime_rule === 'FederalWeeklyOvertimeRule' && (
            <>
              {' '}
              — no rule has been registered for {hub.state_code} yet, so setting the state
              changes nothing on its own. It is what a rule would attach to.
            </>
          )}
        </p>

        {error && <p className="text-[12px] text-[var(--red)]">{error}</p>}
        {!isAdmin && (
          <p className="text-[11px] text-[var(--text-muted)]">
            An admin can change this.
          </p>
        )}

        <ClosedDays hubId={hubId} isAdmin={isAdmin} onToast={onToast} />
      </div>
    </Card>
  )
}

/**
 * Days this hub is not operating (docs/ROADMAP.md R6).
 *
 * **A local calendar date, not an instant.** A hub closes for a day; it does not
 * close for 24 hours from midnight UTC. The input is a plain `date` for the
 * same reason — offering a time would invite somebody to set one.
 *
 * Past closures are kept and shown dimmed rather than hidden. They are why a
 * day in the record has no dispatch on it, and a reader looking at a quiet
 * Tuesday should be able to find that out here rather than concluding the
 * optimizer failed.
 */
function ClosedDays({
  hubId,
  isAdmin,
  onToast,
}: {
  hubId: string
  isAdmin: boolean
  onToast: (message: string) => void
}) {
  const [closures, setClosures] = useState<HubClosure[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [date, setDate] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      setClosures(await api.listHubClosures(hubId))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    if (hubId) void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hubId])

  async function add() {
    if (!date) return
    setBusy(true)
    setError(null)
    try {
      await api.addHubClosure(hubId, { closure_date: date, reason: reason.trim() || null })
      setDate('')
      setReason('')
      await load()
      onToast(`Closed ${date}. The optimizer will skip dispatch that day.`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(closureDate: string) {
    setBusy(true)
    setError(null)
    try {
      await api.removeHubClosure(hubId, closureDate)
      await load()
      onToast(`${closureDate} is open again.`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const today = new Date().toISOString().slice(0, 10)

  return (
    <div className="mt-3 border-t border-[var(--border)] pt-3">
      <p className="mb-1.5 text-[11px] font-medium text-[var(--text-muted)]">
        Closed days
      </p>

      {closures === null && !error && (
        <p className="text-[12px] text-[var(--text-muted)]">loading…</p>
      )}
      {closures?.length === 0 && (
        <p className="text-[12px] text-[var(--text-muted)]">
          None. Every day is a dispatching day.
        </p>
      )}

      {closures && closures.length > 0 && (
        <ul className="mb-2 space-y-1">
          {closures.map((closure) => (
            <li
              key={closure.closure_date}
              className={`flex items-baseline gap-2 text-[12.5px] ${
                closure.closure_date < today ? 'text-[var(--text-muted)]' : 'text-[var(--text-primary)]'
              }`}
            >
              <span className="font-medium tabular-nums">{closure.closure_date}</span>
              <span className="min-w-0 flex-1 truncate text-[var(--text-secondary)]">
                {closure.reason ?? '—'}
              </span>
              {isAdmin && (
                <button
                  disabled={busy}
                  onClick={() => remove(closure.closure_date)}
                  className="shrink-0 text-[11px] text-[var(--text-muted)] underline disabled:opacity-40"
                >
                  reopen
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {isAdmin && (
        <div className="flex flex-wrap items-end gap-1.5">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-[12.5px] text-[var(--text-primary)]"
          />
          <input
            type="text"
            value={reason}
            placeholder="Reason (optional)"
            onChange={(e) => setReason(e.target.value)}
            className="min-w-0 flex-1 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-[12.5px] text-[var(--text-primary)]"
          />
          <button
            disabled={busy || !date}
            onClick={add}
            className="rounded-md bg-[var(--accent)] px-2.5 py-1 text-[11.5px] font-medium text-white disabled:opacity-40"
          >
            Close this day
          </button>
        </div>
      )}

      {error && <p className="mt-1 text-[12px] text-[var(--red)]">{error}</p>}
    </div>
  )
}
