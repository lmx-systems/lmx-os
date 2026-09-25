import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { DockLogSubmission } from '../lib/types'

/**
 * The public Dock Log's review queue (docs/ROADMAP.md DRV-7).
 *
 * **This panel is the reason the staging table is allowed to exist.**
 * `ROADMAP_1.5.md`'s `DRV-7` row requires that a stranger's submission never
 * writes to `receiver_profiles` directly — that table is the layer `M5` trains
 * on. So submissions land inert in `dock_log_submissions`, and this is the only
 * thing that moves one into a dock's profile. Without it the staging table
 * would be write-only and the public form would be a way to fill a table
 * nobody reads, which is a worse defect than the one the staging table
 * prevents.
 *
 * ## The dock is chosen here, never inferred
 *
 * `candidates` are docks within about 150 m of the submitted coordinates, and
 * they are a **shortlist to save a search, not a match.** A submitter chooses
 * their own coordinates as freely as their own business name, so automatic
 * matching would let anybody attach answers to a dock of their choosing. The
 * operator picks, and the endpoint records who picked.
 *
 * ## What the buttons actually do
 *
 * **Import** copies the answers onto the dock's profile as `stated`, not
 * `surveyed`, and deliberately does **not** stamp `surveyed_at` — so
 * `dock_needs_survey` will still put the survey in front of the next driver of
 * ours who delivers there. Somebody told us this; we have not seen it.
 *
 * **Dismiss** keeps the row with a reason rather than deleting it. A pattern of
 * plausible junk from one address is the only evidence this surface is being
 * abused, and it is invisible if each row disappears as it is waved away.
 *
 * A dock one of our own drivers has already surveyed refuses the import with a
 * 409 rather than skipping quietly — a skip that returned success would be
 * indistinguishable from an import that worked.
 */

// The server sends only the answers that were given, so labels are looked up
// rather than enumerated. An unknown key renders its raw name instead of being
// dropped: a question added to the form and not here should look untidy, not
// invisible.
const ANSWER_LABELS: Record<string, string> = {
  stop_point: 'Stopping place',
  curb_access: 'Kerb access',
  walk_distance_band: 'Walk distance',
  door_path: 'Way in',
  obstruction: 'Obstruction',
  who_receives: 'Who receives',
  landing_surface: 'Open ground',
  appointment_required: 'Appointment',
}

function answerText(value: string | boolean): string {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return value.replace(/_/g, ' ')
}

export function DockLogReviewPanel({ onToast }: { onToast: (message: string) => void }) {
  const [submissions, setSubmissions] = useState<DockLogSubmission[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [chosen, setChosen] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)

  async function load() {
    try {
      setSubmissions(await api.listDockLogSubmissions())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function importOne(submission: DockLogSubmission) {
    const locationId = chosen[submission.submission_id]
    if (!locationId) return
    setBusy(submission.submission_id)
    setError(null)
    try {
      await api.importDockLogSubmission(submission.submission_id, locationId)
      await load()
      onToast('Imported. Our own drivers will still be asked to survey this dock.')
    } catch (e) {
      const message = (e as Error).message
      setError(
        /409|already surveyed/i.test(message)
          ? 'One of our drivers has already surveyed that dock — a public submission does not overwrite it.'
          : message,
      )
    } finally {
      setBusy(null)
    }
  }

  async function reject(submission: DockLogSubmission) {
    const reason = window.prompt('Why is this being dismissed?')
    // Cancelled, or an empty reason. The endpoint requires one for the same
    // reason CON-2's override does, and sending a blank would just 422.
    if (reason === null || reason.trim() === '') return
    setBusy(submission.submission_id)
    setError(null)
    try {
      await api.rejectDockLogSubmission(submission.submission_id, reason.trim())
      await load()
      onToast('Dismissed. The row is kept — a pattern of junk is evidence.')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card
      title="Dock Log submissions"
      meta={submissions ? `${submissions.length} awaiting review` : 'loading…'}
    >
      {error && <p className="mb-2 text-sm text-[var(--red)]">{error}</p>}

      {submissions !== null && submissions.length === 0 && (
        <p className="text-[12.5px] text-[var(--text-muted)]">
          Nothing waiting. Submissions arrive from the public Dock Log page and change
          nothing until they are matched to a dock here.
        </p>
      )}

      <div className="flex flex-col gap-3">
        {(submissions ?? []).map((submission) => (
          <div
            key={submission.submission_id}
            className="border-t border-[var(--border)] pt-2 first:border-t-0 first:pt-0"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-[13px] font-medium text-[var(--text-primary)]">
                {submission.business_name || '(no name given)'}
              </span>
              <span className="text-[11px] text-[var(--text-muted)]">
                {submission.answers_recorded} answer
                {submission.answers_recorded === 1 ? '' : 's'} ·{' '}
                {submission.created_at.slice(0, 10)}
              </span>
            </div>

            <p className="text-[12px] text-[var(--text-secondary)]">
              {submission.submitted_address || 'No address given'}
              {submission.lat !== null && submission.lng !== null
                ? ` · ${submission.lat.toFixed(5)}, ${submission.lng.toFixed(5)}`
                : ' · no coordinates'}
            </p>

            <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5">
              {Object.entries(submission.answers).map(([key, value]) => (
                <div key={key} className="flex gap-1 text-[12px]">
                  <dt className="text-[var(--text-muted)]">{ANSWER_LABELS[key] ?? key}:</dt>
                  <dd className="text-[var(--text-primary)]">{answerText(value)}</dd>
                </div>
              ))}
            </dl>

            <div className="mt-2 flex flex-wrap items-center gap-2">
              <select
                value={chosen[submission.submission_id] ?? ''}
                onChange={(e) =>
                  setChosen({ ...chosen, [submission.submission_id]: e.target.value })
                }
                className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-[12px] text-[var(--text-primary)]"
              >
                <option value="">
                  {submission.candidates.length === 0
                    ? 'No dock within 150 m — match by hand'
                    : 'Which dock is this?'}
                </option>
                {submission.candidates.map((candidate) => (
                  <option key={candidate.location_id} value={candidate.location_id}>
                    {candidate.address}
                  </option>
                ))}
              </select>
              <button
                disabled={busy !== null || !chosen[submission.submission_id]}
                onClick={() => importOne(submission)}
                className="rounded-md bg-[var(--accent)] px-2 py-1 text-[11px] font-medium text-white disabled:opacity-40"
              >
                Import
              </button>
              <button
                disabled={busy !== null}
                onClick={() => reject(submission)}
                className="text-[11px] text-[var(--text-muted)] underline disabled:opacity-40"
              >
                Dismiss
              </button>
            </div>
          </div>
        ))}
      </div>

      {submissions !== null && submissions.length > 0 && (
        <p className="mt-3 text-[11px] text-[var(--text-muted)]">
          The shortlist is docks near the submitted coordinates — a suggestion, not a
          match. Importing records the answers as <strong>stated</strong>, not surveyed,
          and our own drivers are still asked to survey the dock when they next deliver
          there.
        </p>
      )}
    </Card>
  )
}
