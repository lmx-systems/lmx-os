import { useCallback, useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api, ApiError } from '../lib/api'
import type { ProposedRuleView } from '../lib/types'

interface ProposedRulesPanelProps {
  hubId: string
  onToast: (message: string) => void
}

/**
 * Learning-Loop rule review (docs/ROADMAP.md I2) - the human-approval rung
 * of component 6. The nightly job proposes per-shop SLA tweaks into
 * proposed_rules; this card is where an ops admin approves one (promoting
 * it into active_rules, where ingestion/the SLA engine start applying it)
 * or dismisses it. Without this the proposals just accumulate unreviewed.
 */
export function ProposedRulesPanel({ hubId, onToast }: ProposedRulesPanelProps) {
  const [rules, setRules] = useState<ProposedRuleView[] | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const disabled = hubId.length === 0

  const load = useCallback(async () => {
    if (disabled) return
    try {
      setRules(await api.listProposedRules(hubId))
    } catch (err) {
      onToast(`Could not load proposed rules: ${err instanceof ApiError ? err.message : String(err)}`)
    }
  }, [hubId, disabled, onToast])

  useEffect(() => {
    setRules(null)
    void load()
  }, [load])

  async function decide(rule: ProposedRuleView, action: 'approve' | 'dismiss') {
    setBusyId(rule.rule_id)
    try {
      if (action === 'approve') {
        await api.approveProposedRule(rule.rule_id)
        onToast('Proposal approved — now active on dispatch.')
      } else {
        await api.dismissProposedRule(rule.rule_id)
        onToast('Proposal dismissed.')
      }
      await load()
    } catch (err) {
      onToast(`Could not ${action}: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card title="Proposed rules" meta="I2 — learning-loop review">
      {disabled && (
        <p className="text-sm text-[var(--text-muted)]">Select a hub above to review its proposals.</p>
      )}

      {!disabled &&
        (rules === null ? (
          <p className="text-sm text-[var(--text-muted)]">Loading proposals…</p>
        ) : rules.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">
            No proposals awaiting review. The nightly job adds them as driver-annotation patterns emerge.
          </p>
        ) : (
          <div className="flex flex-col gap-3 text-[13px]">
            {rules.map((rule) => (
              <div
                key={rule.rule_id}
                className="flex items-start justify-between gap-4 rounded-[var(--radius)] border border-[var(--border)] p-3"
              >
                <div className="flex flex-col gap-1">
                  <div className="font-medium text-[var(--text-primary)]">{rule.rule_type}</div>
                  <div className="font-mono text-[12px] text-[var(--text-secondary)]">
                    {JSON.stringify(rule.scope)} → {JSON.stringify(rule.proposed_change)}
                  </div>
                  <div className="text-[12px] text-[var(--text-muted)]">
                    {Math.round(rule.confidence * 100)}% confidence · {rule.supporting_annotation_count} supporting
                    annotation{rule.supporting_annotation_count === 1 ? '' : 's'}
                  </div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    disabled={busyId === rule.rule_id}
                    onClick={() => decide(rule, 'approve')}
                    className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-60"
                  >
                    Approve
                  </button>
                  <button
                    disabled={busyId === rule.rule_id}
                    onClick={() => decide(rule, 'dismiss')}
                    className="rounded-[var(--radius)] border border-[var(--border-strong)] px-3 py-1.5 text-[12px] text-[var(--text-secondary)] disabled:opacity-60"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            ))}
          </div>
        ))}
    </Card>
  )
}
