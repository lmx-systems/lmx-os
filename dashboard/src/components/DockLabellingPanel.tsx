import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import { NODE_CLASSES } from '../lib/types'
import type { ClassificationCoverage, UnlabelledDock } from '../lib/types'

/**
 * What kind of place is this? (docs/ROADMAP_1.5.md IDN-3.)
 *
 * **The rules are exhausted.** Measured against the design partner's real
 * account book they leave 28.7% of accounts unlabelled against a target of
 * under 2%, and what remains is family and personal business names carrying no
 * industry word at all — the export has no industry code, so there is nothing
 * else to read. The row has always said the choice is *"a person labels the
 * tail, or the target moves"*. This is the surface that makes the first one
 * possible.
 *
 * **Ordered by how busy the dock looks**, from the inherited dwell sample count
 * — the only evidence of volume we have before delivering there ourselves. The
 * tail is long and an arbitrary order gets worked from the top until somebody
 * stops, so the order decides which docks actually get labelled.
 *
 * Seven buttons, no free text. They are what `PRD-1` groups by, and an eighth
 * appearing would split a group without anyone noticing.
 *
 * Renders nothing when the tail is empty, which is the state IDN-3 is trying to
 * reach.
 */
export function DockLabellingPanel() {
  const [docks, setDocks] = useState<UnlabelledDock[] | null>(null)
  const [coverage, setCoverage] = useState<ClassificationCoverage | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)

  async function load() {
    try {
      const [d, c] = await Promise.all([
        api.unlabelledDocks(25),
        api.classificationCoverage(),
      ])
      setDocks(d)
      setCoverage(c)
    } catch (e) {
      setError(e as Error)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function label(locationId: string, nodeClass: string) {
    setBusy(locationId)
    try {
      await api.labelDock(locationId, nodeClass)
      // Optimistic removal, then a reload for the coverage figure. Leaving the
      // row in place until the round trip finishes makes a reviewer wonder
      // whether the click registered and label it twice.
      setDocks((current) => (current ?? []).filter((d) => d.location_id !== locationId))
      await load()
    } catch (e) {
      setError(e as Error)
    } finally {
      setBusy(null)
    }
  }

  if (docks !== null && docks.length === 0) return null

  return (
    <Card
      title="What kind of place?"
      meta={
        coverage
          ? `${coverage.unlabelled} unlabelled of ${coverage.shops}`
          : 'loading…'
      }
    >
      {error && <p className="text-sm text-[var(--red)]">Couldn't load: {error.message}</p>}

      {coverage && (
        <p className="mb-2 text-[12px] text-[var(--text-secondary)]">
          {coverage.unlabelled_percent}% unlabelled, against a target under 2%.{' '}
          {coverage.without_dock > 0 && (
            <span className="text-[var(--text-muted)]">
              {coverage.without_dock} more have no dock at all and cannot be classified.{' '}
            </span>
          )}
          Naming rules cannot close this — the busiest are listed first.
        </p>
      )}

      <ul className="space-y-2">
        {(docks ?? []).map((dock) => (
          <li
            key={dock.location_id}
            className="rounded-[var(--radius)] border border-[var(--border)] px-2.5 py-2"
          >
            <div className="mb-1 flex items-baseline justify-between gap-3">
              <span className="text-[13px] text-[var(--text-primary)]">
                {dock.shop_names[0] ?? dock.address}
              </span>
              {dock.inherited_dwell_sample_count !== null && (
                <span className="flex-shrink-0 text-[11px] tabular-nums text-[var(--text-muted)]">
                  {dock.inherited_dwell_sample_count} past stops
                  {dock.inherited_dwell_p50_seconds !== null &&
                    ` · ~${dock.inherited_dwell_p50_seconds}s at the door`}
                </span>
              )}
            </div>
            {dock.shop_names.length > 1 && (
              <p className="mb-1 text-[11px] text-[var(--text-muted)]">
                also {dock.shop_names.slice(1).join(', ')}
              </p>
            )}
            <div className="flex flex-wrap gap-1">
              {NODE_CLASSES.map((klass) => (
                <button
                  key={klass.code}
                  disabled={busy === dock.location_id}
                  onClick={() => label(dock.location_id, klass.code)}
                  className="rounded-md border border-[var(--border)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)] disabled:opacity-40"
                >
                  {klass.label}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  )
}
