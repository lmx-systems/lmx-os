import { useState } from 'react'
import { api, ApiError } from '../lib/api'

/**
 * The public Dock Log (docs/ROADMAP.md DRV-7).
 *
 * **Our own drivers do not use this.** They answer the same eight questions in
 * `DockSurveyModal`, after a delivery completes, with the stop already known.
 * This is the other half: the page for mapping docks we do not yet serve, used
 * by couriers who work for somebody else and are standing at a door we have
 * never sent a van to.
 *
 * That difference decides the whole design. A driver of ours is mid-shift and
 * the survey must cost them nothing; a volunteer here has chosen to be on this
 * page, so the screen can ask for the one thing our own app never has to —
 * **which dock this is.**
 *
 * ## What the submitter is told, and what they are not
 *
 * They are told their answers are recorded. They are **not** told whether we
 * already hold this dock, which one it matched, or anything else that varies
 * by address. A response that differed would make this form a way to enumerate
 * our customers' docks one guess at a time, so the endpoint returns 202 and a
 * count, and the page says exactly that much.
 *
 * They are also told, plainly, that a person reads it before it changes
 * anything. That is true — a submission lands in a staging table and reaches a
 * dock profile only through an operator — and saying so is the honest answer
 * to "why did nothing happen when I submitted this".
 *
 * ## Location is asked for, never taken
 *
 * `navigator.geolocation` prompts, and a refusal is a supported path rather
 * than a dead end: an address alone is matchable by the person reviewing it.
 * The page says which of the two it has before the button is pressed, because
 * "submit failed" after filling in eight questions is a worse way to learn the
 * requirement.
 *
 * No login, no navigation, no account. Same audience posture as `TrackingPage`
 * and reached the same way — a path this app checks before every auth branch.
 */

interface Question {
  key: string
  prompt: string
  options: { value: string; label: string }[]
}

// The server's vocabulary, not a parallel one. `app/identity/dock_log.py`
// validates every value against the same tuples `DockSurveyModal` uses, and a
// mismatch is a 422 naming the field rather than a stored surprise - these
// become M5's labels and a stray value fails months later, not today.
const QUESTIONS: Question[] = [
  {
    key: 'stop_point',
    prompt: 'Where could a van stop?',
    options: [
      { value: 'loading_dock', label: 'Loading dock' },
      { value: 'marked_bay', label: 'Marked bay' },
      { value: 'lot', label: 'Car park' },
      { value: 'street_legal', label: 'Street, legally' },
      { value: 'double_parked', label: 'Only by double-parking' },
      { value: 'none_legal', label: 'Nowhere legal' },
    ],
  },
  {
    key: 'curb_access',
    prompt: 'Could a vehicle pull up at the kerb by the door?',
    options: [
      { value: 'direct', label: 'Right at the door' },
      { value: 'short_walk', label: 'Short walk' },
      { value: 'long_walk', label: 'Long walk' },
      { value: 'no_curb', label: 'No kerb access' },
    ],
  },
  {
    key: 'walk_distance_band',
    prompt: 'How far from the vehicle to the handover?',
    options: [
      { value: 'at_vehicle', label: 'At the vehicle' },
      { value: 'under_20m', label: 'Under 20 m' },
      { value: 'under_100m', label: 'Under 100 m' },
      { value: 'over_100m', label: 'Over 100 m' },
    ],
  },
  {
    key: 'door_path',
    prompt: 'The way in to the door',
    options: [
      { value: 'ground_level', label: 'Ground level' },
      { value: 'steps', label: 'Steps' },
      { value: 'ramp', label: 'Ramp' },
      { value: 'loading_dock', label: 'Loading dock' },
      { value: 'freight_lift', label: 'Freight lift' },
    ],
  },
  {
    key: 'obstruction',
    prompt: 'Anything in the way?',
    options: [
      { value: 'none', label: 'Nothing' },
      { value: 'gate', label: 'Gate' },
      { value: 'security_desk', label: 'Security desk' },
      { value: 'narrow_access', label: 'Narrow access' },
      { value: 'overhead_limit', label: 'Height limit' },
    ],
  },
  {
    key: 'who_receives',
    prompt: 'Who takes a delivery here?',
    options: [
      { value: 'anyone', label: 'Anyone there' },
      { value: 'named_person', label: 'A named person' },
      { value: 'counter_staff', label: 'Counter staff' },
      { value: 'dock_crew', label: 'Dock crew' },
      { value: 'unattended_ok', label: 'Can be left' },
    ],
  },
  {
    key: 'landing_surface',
    prompt: 'Open ground nearby something could set down on?',
    options: [
      { value: 'paved_lot', label: 'Paved area' },
      { value: 'gravel', label: 'Gravel' },
      { value: 'grass', label: 'Grass' },
      { value: 'street_only', label: 'Street only' },
      { value: 'rooftop', label: 'Rooftop' },
      { value: 'none', label: 'Nothing open' },
    ],
  },
]

type Answers = Record<string, string | boolean | null>

export function DockLogPage() {
  const [businessName, setBusinessName] = useState('')
  const [address, setAddress] = useState('')
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null)
  const [locating, setLocating] = useState(false)
  const [locationRefused, setLocationRefused] = useState(false)
  const [answers, setAnswers] = useState<Answers>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [recorded, setRecorded] = useState<number | null>(null)

  function choose(key: string, value: string | boolean) {
    setAnswers((prev) => ({ ...prev, [key]: prev[key] === value ? null : value }))
  }

  function askForLocation() {
    if (!navigator.geolocation) {
      setLocationRefused(true)
      return
    }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setCoords({ lat: position.coords.latitude, lng: position.coords.longitude })
        setLocationRefused(false)
        setLocating(false)
      },
      () => {
        // A refusal is a supported path, not an error. The address field is
        // enough for the person who reviews this.
        setLocationRefused(true)
        setLocating(false)
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    )
  }

  const answered = Object.values(answers).filter((v) => v !== null && v !== undefined).length
  const identifiable = coords !== null || address.trim() !== ''
  const canSubmit = businessName.trim() !== '' && identifiable && answered > 0 && !busy

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const result = await api.submitDockLog({
        business_name: businessName.trim(),
        submitted_address: address.trim() || null,
        lat: coords?.lat ?? null,
        lng: coords?.lng ?? null,
        ...answers,
      })
      setRecorded(result.answers_recorded)
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 429
          ? 'That is a lot of submissions from one connection. Try again in an hour.'
          : "Couldn't send that — check your connection and try again.",
      )
    } finally {
      setBusy(false)
    }
  }

  if (recorded !== null) {
    return (
      <main className="mx-auto max-w-xl px-4 py-12">
        <h1 className="text-2xl font-semibold text-[var(--text-primary)]">Thank you</h1>
        <p className="mt-3 text-[15px] text-[var(--text-secondary)]">
          {recorded} answer{recorded === 1 ? '' : 's'} recorded for{' '}
          <strong>{businessName.trim()}</strong>.
        </p>
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">
          Someone at LMX reads every submission before it changes anything. That is
          deliberate — this page is open to anyone, and what it feeds is used to plan real
          deliveries.
        </p>
        <button
          onClick={() => {
            setRecorded(null)
            setAnswers({})
            setBusinessName('')
            setAddress('')
          }}
          className="mt-6 rounded-md border border-[var(--border)] px-3 py-1.5 text-[13px] text-[var(--text-primary)]"
        >
          Map another dock
        </button>
      </main>
    )
  }

  return (
    <main className="mx-auto max-w-xl px-4 py-10">
      <h1 className="text-2xl font-semibold text-[var(--text-primary)]">Dock Log</h1>
      <p className="mt-2 text-[15px] text-[var(--text-secondary)]">
        Standing at a delivery door? Tell us what it is like. Eight questions, skip any of
        them.
      </p>
      <p className="mt-2 text-[13px] text-[var(--text-muted)]">
        This is for couriers mapping docks we do not serve yet. A person at LMX reads
        every submission before it changes anything.
      </p>

      {error && <p className="mt-4 text-[13px] text-[var(--red)]">{error}</p>}

      <section className="mt-6 flex flex-col gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-[13px] font-medium text-[var(--text-primary)]">
            What is this place called?
          </span>
          <input
            value={businessName}
            onChange={(e) => setBusinessName(e.target.value)}
            placeholder="The name on the door"
            className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-[14px] text-[var(--text-primary)]"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-[13px] font-medium text-[var(--text-primary)]">
            Address <span className="font-normal text-[var(--text-muted)]">(or use your location)</span>
          </span>
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="Street and town"
            className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-[14px] text-[var(--text-primary)]"
          />
        </label>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={askForLocation}
            disabled={locating}
            className="rounded-md border border-[var(--border)] px-2.5 py-1 text-[13px] text-[var(--text-primary)] disabled:opacity-40"
          >
            {locating ? 'Locating…' : coords ? 'Update my location' : 'Use my location'}
          </button>
          {coords && (
            <span className="text-[12px] text-[var(--text-muted)]">
              Pinned to {coords.lat.toFixed(5)}, {coords.lng.toFixed(5)}
            </span>
          )}
          {locationRefused && !coords && (
            <span className="text-[12px] text-[var(--text-muted)]">
              No location — the address is enough.
            </span>
          )}
        </div>
      </section>

      <section className="mt-8 flex flex-col gap-6">
        {QUESTIONS.map((question) => (
          <div key={question.key} className="flex flex-col gap-2">
            <span className="text-[13px] font-medium text-[var(--text-primary)]">
              {question.prompt}
            </span>
            <div className="flex flex-wrap gap-2">
              {question.options.map((option) => {
                const chosen = answers[question.key] === option.value
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => choose(question.key, option.value)}
                    className={
                      chosen
                        ? 'rounded-md border border-[var(--accent)] bg-[var(--accent)] px-2.5 py-1 text-[13px] text-white'
                        : 'rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 text-[13px] text-[var(--text-primary)]'
                    }
                  >
                    {option.label}
                  </button>
                )
              })}
            </div>
          </div>
        ))}

        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-medium text-[var(--text-primary)]">
            Do deliveries need an appointment or booking?
          </span>
          <div className="flex flex-wrap gap-2">
            {[
              { value: true, label: 'Yes' },
              { value: false, label: 'No' },
            ].map((option) => {
              const chosen = answers.appointment_required === option.value
              return (
                <button
                  key={String(option.value)}
                  type="button"
                  onClick={() => choose('appointment_required', option.value)}
                  className={
                    chosen
                      ? 'rounded-md border border-[var(--accent)] bg-[var(--accent)] px-2.5 py-1 text-[13px] text-white'
                      : 'rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 text-[13px] text-[var(--text-primary)]'
                  }
                >
                  {option.label}
                </button>
              )
            })}
          </div>
        </div>
      </section>

      <div className="mt-8 flex flex-col gap-2 border-t border-[var(--border)] pt-4">
        {/* Said before the button, not after. Learning the requirement from a
            failed submit, having answered eight questions, is a worse way to
            find out. */}
        {!businessName.trim() && (
          <span className="text-[12px] text-[var(--text-muted)]">
            A name is needed so somebody can tell which dock this is.
          </span>
        )}
        {businessName.trim() && !identifiable && (
          <span className="text-[12px] text-[var(--text-muted)]">
            Add an address or allow location.
          </span>
        )}
        <button
          onClick={submit}
          disabled={!canSubmit}
          className="rounded-md bg-[var(--accent)] px-3 py-2 text-[14px] font-medium text-white disabled:opacity-40"
        >
          {busy ? 'Sending…' : answered === 0 ? 'Answer at least one question' : `Send ${answered} answer${answered === 1 ? '' : 's'}`}
        </button>
      </div>
    </main>
  )
}
