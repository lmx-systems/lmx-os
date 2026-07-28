import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Card } from './ui/Card'
import { api, ApiError } from '../lib/api'
import type { UrgencyRuleView } from '../lib/types'

interface UrgencyRulesPanelProps {
  hubId: string
  onToast: (message: string) => void
}

const SLA_TIERS = ['HOT_SHOT', 'T1', 'T2', 'T3'] as const

/**
 * Orchestrator-editable urgency rules (docs/ROADMAP.md W6). Lets ops author
 * "when an order's <field> equals <value>, force <tier>" rules - e.g.
 * part_category = body_panel -> T3 ("body panels are never urgent") -
 * without a code deploy. Applied at ingestion (app/sla/engine.py). Distinct
 * from the Learning Loop's machine-proposed rules: this is direct human
 * authoring.
 */
export function UrgencyRulesPanel({ hubId, onToast }: UrgencyRulesPanelProps) {
  const [rules, setRules] = useState<UrgencyRuleView[] | null>(null)
  const [matchKey, setMatchKey] = useState('')
  const [matchValue, setMatchValue] = useState('')
  const [tier, setTier] = useState<string>('T3')
  const [submitting, setSubmitting] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)

  const disabled = hubId.length === 0

  const load = useCallback(async () => {
    if (disabled) return
    try {
      setRules(await api.listUrgencyRules(hubId))
    } catch (err) {
      onToast(`Could not load urgency rules: ${err instanceof ApiError ? err.message : String(err)}`)
    }
  }, [hubId, disabled, onToast])

  useEffect(() => {
    setRules(null)
    void load()
  }, [load])

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    try {
      await api.addUrgencyRule(hubId, {
        match_key: matchKey.trim(),
        match_value: matchValue.trim(),
        tier,
      })
      onToast(`Rule added: ${matchKey.trim()} = ${matchValue.trim()} → ${tier}`)
      setMatchKey('')
      setMatchValue('')
      setTier('T3')
      await load()
    } catch (err) {
      onToast(`Could not add rule: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setSubmitting(false)
    }
  }

  async function toggle(rule: UrgencyRuleView) {
    setBusyId(rule.rule_id)
    try {
      await api.setUrgencyRuleEnabled(hubId, rule.rule_id, !rule.enabled)
      await load()
    } catch (err) {
      onToast(`Could not update rule: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setBusyId(null)
    }
  }

  async function remove(rule: UrgencyRuleView) {
    setBusyId(rule.rule_id)
    try {
      await api.removeUrgencyRule(hubId, rule.rule_id)
      await load()
    } catch (err) {
      onToast(`Could not delete rule: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card title="Urgency rules" meta="W6 — part-type → tier">
      {disabled && (
        <p className="text-sm text-[var(--text-muted)]">Select a hub above to manage its urgency rules.</p>
      )}

      {!disabled && (
        <div className="flex flex-col gap-4 text-[13px]">
          <form onSubmit={handleAdd} className="grid grid-cols-[1fr_1fr_auto_auto] items-end gap-2">
            <label className="flex flex-col gap-1 text-[var(--text-secondary)]">
              Order field
              <input
                required
                value={matchKey}
                placeholder="part_category"
                onChange={(e) => setMatchKey(e.target.value)}
                className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[var(--text-secondary)]">
              Equals
              <input
                required
                value={matchValue}
                placeholder="body_panel"
                onChange={(e) => setMatchValue(e.target.value)}
                className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[var(--text-secondary)]">
              Tier
              <select
                value={tier}
                onChange={(e) => setTier(e.target.value)}
                className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              >
                {SLA_TIERS.map((t) => (
                  <option key={t} value={t}>
                    {t === 'HOT_SHOT' ? 'Hot Shot' : t}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              disabled={submitting}
              className="rounded-[var(--radius)] bg-[var(--accent)] px-4 py-1.5 text-[13px] font-medium text-white disabled:opacity-60"
            >
              {submitting ? 'Adding…' : 'Add'}
            </button>
          </form>

          {rules === null ? (
            <p className="text-[var(--text-muted)]">Loading rules…</p>
          ) : rules.length === 0 ? (
            <p className="text-[var(--text-muted)]">
              No urgency rules yet. Orders classify by the default payload-flag heuristic.
            </p>
          ) : (
            <table className="w-full text-left">
              <thead>
                <tr className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                  <th className="py-1.5">Rule</th>
                  <th className="py-1.5">Tier</th>
                  <th className="py-1.5">State</th>
                  <th className="py-1.5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.rule_id} className="border-t border-[var(--border)]">
                    <td className="py-2 font-mono text-[12px] text-[var(--text-primary)]">
                      {rule.match_key} = {rule.match_value}
                    </td>
                    <td className="py-2 text-[var(--text-secondary)]">
                      {rule.tier === 'HOT_SHOT' ? 'Hot Shot' : rule.tier}
                    </td>
                    <td className="py-2">
                      <span className={rule.enabled ? 'text-[var(--green)]' : 'text-[var(--text-muted)]'}>
                        {rule.enabled ? 'Active' : 'Disabled'}
                      </span>
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex justify-end gap-2">
                        <button
                          disabled={busyId === rule.rule_id}
                          onClick={() => toggle(rule)}
                          className="rounded-[var(--radius)] border border-[var(--border-strong)] px-2 py-1 text-[12px] text-[var(--text-secondary)] disabled:opacity-60"
                        >
                          {rule.enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button
                          disabled={busyId === rule.rule_id}
                          onClick={() => remove(rule)}
                          className="rounded-[var(--radius)] border border-[var(--border-strong)] px-2 py-1 text-[12px] text-[var(--text-secondary)] disabled:opacity-60"
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </Card>
  )
}
