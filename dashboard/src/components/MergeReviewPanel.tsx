import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { MergeProposal } from '../lib/types'

/**
 * Are these two the same place? (docs/ROADMAP_1.5.md IDN-2.)
 *
 * *"Human-confirms the founding ~230, auto-merge after, every merge audited and
 * reversible."* The queue had no producer and no reader, so it was permanently
 * empty and the founding set could not be confirmed
 * (`docs/ROADMAP_AUDIT_2026-09.md`).
 *
 * **Both addresses are shown, never the ids.** The question is whether two
 * records name one physical dock, and nobody can answer that from two UUIDs.
 * The detector's reason is shown too — *why* it thinks so is most of what a
 * reviewer needs to disagree with it.
 *
 * Rejecting is as valuable as confirming and is presented that way. It stops the
 * pair being re-proposed every time the detector runs, and a queue that re-asks
 * a question somebody already answered becomes noise that gets cleared without
 * being read.
 *
 * Admin only for the decisions — confirming rewrites which dock a shop points
 * at and every per-dock statistic moves with it. Anyone may read the queue,
 * because a dispatcher spotting a duplicate is how good proposals get noticed.
 */
export function MergeReviewPanel({ isAdmin }: { isAdmin: boolean }) {
  const [proposals, setProposals] = useState<MergeProposal[] | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  async function load() {
    try {
      setProposals(await api.mergeProposals(true))
    } catch (e) {
      setError(e as Error)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function decide(id: string, decision: 'confirm' | 'reject' | 'revert') {
    setBusy(id)
    try {
      if (decision === 'confirm') await api.confirmMerge(id)
      else if (decision === 'reject') await api.rejectMerge(id)
      else await api.revertMerge(id)
      await load()
    } catch (e) {
      setError(e as Error)
    } finally {
      setBusy(null)
    }
  }

  const pending = (proposals ?? []).filter((p) => p.status === 'proposed')
  const applied = (proposals ?? []).filter((p) => p.status === 'applied')

  if (proposals !== null && pending.length === 0 && applied.length === 0) return null

  return (
    <Card title="Same place?" meta={proposals ? `${pending.length} to judge` : 'loading…'}>
      {error && <p className="text-sm text-[var(--red)]">Couldn't load: {error.message}</p>}

      <p className="mb-2 text-[12px] text-[var(--text-secondary)]">
        Two records that may name one physical dock. Saying they are different is as useful as
        saying they are the same — it stops the pair coming back every night.
      </p>

      <ul className="space-y-2">
        {(proposals ?? []).filter((p) => p.status === 'proposed').map((proposal) => (
          <li
            key={proposal.id}
            className="rounded-[var(--radius)] border border-[var(--border)] px-2.5 py-2"
          >
            <p className="text-[13px] text-[var(--text-primary)]">{proposal.source_address}</p>
            <p className="text-[13px] text-[var(--text-primary)]">{proposal.target_address}</p>
            <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">{proposal.reason}</p>
            {isAdmin && (
              <div className="mt-1.5 flex gap-1.5">
                <button
                  disabled={busy === proposal.id}
                  onClick={() => decide(proposal.id, 'confirm')}
                  className="rounded-md bg-[var(--accent)] px-2 py-0.5 text-[11px] font-medium text-white disabled:opacity-40"
                >
                  Same place
                </button>
                <button
                  disabled={busy === proposal.id}
                  onClick={() => decide(proposal.id, 'reject')}
                  className="rounded-md border border-[var(--border)] px-2 py-0.5 text-[11px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40"
                >
                  Different
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>

      {applied.length > 0 && isAdmin && (
        <section className="mt-3 border-t border-[var(--border)] pt-2.5">
          <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            Recently merged
          </h3>
          {/* The undo list. "Every merge audited and reversible" is a clause of
              the done-when, and the revert existed in code with nothing listing
              an applied merge for anybody to reverse. Capped and recent: undo is
              for the one somebody has just realised was wrong, not a complete
              history. */}
          <ul className="space-y-1">
            {applied.map((merge) => (
              <li
                key={merge.id}
                className="flex items-start justify-between gap-3 text-[12px]"
              >
                <span className="text-[var(--text-secondary)]">
                  {merge.source_address} → {merge.target_address}
                </span>
                <button
                  disabled={busy === merge.id}
                  onClick={() => decide(merge.id, 'revert')}
                  className="flex-shrink-0 rounded-md px-1.5 py-0.5 text-[11px] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)] disabled:opacity-40"
                >
                  Undo
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Card>
  )
}
