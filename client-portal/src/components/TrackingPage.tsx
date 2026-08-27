import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { TrackingView } from '../lib/types'

interface TrackingPageProps {
  token: string
}

// How often to re-ask while a delivery is live. Slow enough that a page left open
// on a phone isn't a battery or data problem, fast enough that a moving marker
// looks alive. The backend's rate limit is set well above this so a family
// refreshing on two devices never trips it.
const POLL_INTERVAL_MS = 20_000

/**
 * The public delivery-tracking page (docs/ROADMAP.md F3).
 *
 * The audience is the person waiting for a part, reached by SMS, almost certainly
 * on a phone, and with no account and no idea what LMX is. So: one screen, no
 * navigation, no login, largest type on the thing they actually want to know.
 *
 * **What is deliberately absent is the design.** No driver name or photo, no
 * "your driver is 3 stops away", no full street address. The backend won't send
 * those (`app/schemas/tracking.py` is the boundary), and the page shouldn't imply
 * they exist. A tracking link gets forwarded and screenshotted, and the only
 * credential protecting it is in the URL bar.
 *
 * The map is a plain OpenStreetMap-tiled iframe rather than a JS mapping library:
 * it needs no key, adds no bundle weight to a page loaded once over mobile data,
 * and this page shows exactly one marker. If tracking ever needs a route line or
 * clustering, that's the point to reach for a real map.
 */
export function TrackingPage({ token }: TrackingPageProps) {
  const [view, setView] = useState<TrackingView | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [offline, setOffline] = useState(false)
  // Kept in a ref so the polling effect doesn't restart on every tick.
  const isLive = useRef(true)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    const load = async () => {
      try {
        const result = await api.trackDelivery(token)
        if (cancelled) return
        setView(result)
        setOffline(false)
        isLive.current = result.is_live
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          // Unknown or expired - the backend deliberately doesn't distinguish,
          // so neither does this.
          setNotFound(true)
          isLive.current = false
          return
        }
        // A network blip or a 429. Keep whatever we last showed rather than
        // blanking a page someone is watching, and say so quietly.
        setOffline(true)
      }
      if (!cancelled && isLive.current) {
        timer = window.setTimeout(load, POLL_INTERVAL_MS)
      }
    }

    load()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [token])

  if (notFound) {
    return (
      <Shell>
        <h1 className="text-2xl font-semibold text-slate-900">
          We couldn&rsquo;t find that delivery
        </h1>
        <p className="mt-3 text-slate-600">
          This tracking link may have expired. Tracking links stay active for a day
          after a delivery is completed.
        </p>
        <p className="mt-6 text-sm text-slate-500">
          If you&rsquo;re still waiting on a part, the shop you ordered from can help.
        </p>
      </Shell>
    )
  }

  if (view === null) {
    return (
      <Shell>
        <p className="text-slate-500">Loading your delivery&hellip;</p>
      </Shell>
    )
  }

  return (
    <Shell>
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">
        LMX Delivery
      </p>
      <h1 className="mt-2 text-3xl font-semibold text-slate-900">{view.headline}</h1>
      <p className="mt-2 text-slate-600">{view.detail}</p>

      {view.destination_hint && (
        <p className="mt-4 text-sm text-slate-500">
          Delivering to <span className="font-medium text-slate-700">{view.destination_hint}</span>
        </p>
      )}

      <Arrival view={view} />

      <RatingPrompt token={token} view={view} onRated={setView} />

      {view.driver_position ? (
        <DriverMap position={view.driver_position} />
      ) : (
        view.is_live && (
          <p className="mt-6 rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-600">
            {/* Said plainly rather than left as an empty space where a map should
                be. The map appears when the driver is on their way to this
                address - not while they're completing someone else's delivery. */}
            Live driver location appears here once your driver is on the way to you.
          </p>
        )
      )}

      {offline && (
        <p className="mt-4 text-xs text-amber-700">
          Having trouble refreshing &mdash; showing the last update.
        </p>
      )}
    </Shell>
  )
}

/**
 * One tap to rate the delivery, and a sentence if they want to (docs/ROADMAP.md F13).
 *
 * Only appears once the delivery has landed - `can_rate` comes from the server, so the
 * page never has to reason about which statuses qualify. It stays visible after
 * submission with the score selected, because a recipient who taps four stars and then
 * wants to explain why should not have to hunt for a way back in.
 *
 * The comment box is revealed by choosing a score rather than shown alongside it. The
 * ask is one tap; presenting an empty textarea up front makes it look like homework and
 * costs the response rate the whole feature depends on.
 */
function RatingPrompt({
  token,
  view,
  onRated,
}: {
  token: string
  view: TrackingView
  onRated: (view: TrackingView) => void
}) {
  const { can_rate, score, comment } = view.rating
  const [draftComment, setDraftComment] = useState(comment ?? '')
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  if (!can_rate) return null

  async function submit(nextScore: number, nextComment?: string) {
    setSaving(true)
    setFailed(false)
    try {
      onRated(await api.rateDelivery(token, nextScore, nextComment))
    } catch {
      // The rating is worth far less than the tracking page it sits on, so a failure
      // here says so and changes nothing else.
      setFailed(true)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-8 border-t border-slate-200 pt-6">
      <p className="text-sm font-medium text-slate-900">
        {score ? 'Thanks for the feedback.' : 'How was this delivery?'}
      </p>

      <div className="mt-3 flex gap-1.5" role="group" aria-label="Rate this delivery">
        {[1, 2, 3, 4, 5].map((value) => (
          <button
            key={value}
            type="button"
            disabled={saving}
            aria-label={`${value} out of 5`}
            aria-pressed={score === value}
            onClick={() => submit(value, draftComment)}
            className={`h-10 w-10 rounded-lg border text-base transition-colors disabled:opacity-50 ${
              score !== null && value <= score
                ? 'border-slate-900 bg-slate-900 text-white'
                : 'border-slate-200 bg-white text-slate-400 hover:border-slate-400'
            }`}
          >
            {value}
          </button>
        ))}
      </div>

      {score !== null && (
        <div className="mt-4">
          <label htmlFor="rating-comment" className="text-xs text-slate-500">
            Anything you want us to know? (optional)
          </label>
          <textarea
            id="rating-comment"
            value={draftComment}
            maxLength={500}
            rows={3}
            onChange={(e) => setDraftComment(e.target.value)}
            placeholder="Driver couldn't find the loading dock…"
            className="mt-1 w-full rounded-lg border border-slate-200 p-2 text-sm text-slate-900 placeholder:text-slate-400"
          />
          <button
            type="button"
            disabled={saving || draftComment.trim() === (comment ?? '')}
            onClick={() => submit(score, draftComment)}
            className="mt-2 rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {saving ? 'Saving…' : comment ? 'Update' : 'Send'}
          </button>
        </div>
      )}

      {failed && (
        <p role="alert" className="mt-2 text-xs text-amber-700">
          We couldn&rsquo;t save that just now. Your delivery is unaffected.
        </p>
      )}
    </div>
  )
}

function Arrival({ view }: { view: TrackingView }) {
  if (view.delivered_at) {
    return (
      <p className="mt-6 text-lg text-slate-900">
        Delivered at{' '}
        <span className="font-semibold">{formatTime(view.delivered_at)}</span>
      </p>
    )
  }
  if (!view.estimated_arrival) return null
  return (
    <div className="mt-6">
      <p className="text-sm text-slate-500">Estimated arrival</p>
      <p className="text-2xl font-semibold text-slate-900">
        {formatTime(view.estimated_arrival)}
      </p>
      {/* Labelled an estimate on the page, not just in the field name. There is
          still no verified travel-time model (E1), so presenting this as a
          promise would be overstating what we know. */}
      <p className="mt-1 text-xs text-slate-500">An estimate, not a guaranteed time.</p>
    </div>
  )
}

function DriverMap({ position }: { position: NonNullable<TrackingView['driver_position']> }) {
  const { lat, lng } = position
  const span = 0.012
  const bbox = [lng - span, lat - span / 2, lng + span, lat + span / 2].join('%2C')
  return (
    <div className="mt-6">
      <div className="overflow-hidden rounded-xl border border-slate-200">
        <iframe
          title="Driver location"
          className="h-64 w-full"
          loading="lazy"
          src={`https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${lat}%2C${lng}`}
        />
      </div>
      <p className="mt-2 text-xs text-slate-500">
        Driver location updated {relativeTime(position.recorded_at)}
      </p>
    </div>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-white px-5 py-10">
      <div className="mx-auto max-w-md">{children}</div>
    </div>
  )
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

function relativeTime(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  return `${Math.round(minutes / 60)}h ago`
}
