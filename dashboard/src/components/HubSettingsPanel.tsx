import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import { US_STATE_CODES } from '../lib/types'
import type { HubSettings } from '../lib/types'

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
      </div>
    </Card>
  )
}
