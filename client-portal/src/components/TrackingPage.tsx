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
 *
 * **It loads only when the reader asks.** The iframe sends OpenStreetMap the
 * driver's exact position and the reader's IP address, a transfer to a third
 * party that loading it on sight would make for everyone who opens a forwarded
 * link. Until they tap, nothing leaves this page, and the button says what
 * tapping shares.
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
        <h1 className="text-[32px] font-extrabold leading-[1.1] tracking-[-0.04em] text-[var(--text-primary)]">
          We couldn&rsquo;t find that delivery
        </h1>
        <p className="mt-3 text-[var(--text-secondary)]">
          This tracking link may have expired. Tracking links stay active for a day
          after a delivery is completed.
        </p>
        <p className="mt-6 text-sm text-[var(--text-muted)]">
          If you&rsquo;re still waiting on a part, the shop you ordered from can help.
        </p>
      </Shell>
    )
  }

  if (view === null) {
    return (
      <Shell>
        <p className="text-[var(--text-muted)]">Loading your delivery&hellip;</p>
      </Shell>
    )
  }

  return (
    <Shell>
      <h1 className="text-[40px] font-extrabold leading-[1.05] tracking-[-0.045em] text-[var(--text-primary)] [text-wrap:balance]">
        {view.headline}
      </h1>
      <p className="mt-2 text-[16px] text-[var(--text-secondary)]">{view.detail}</p>

      {view.destination_hint && (
        <p className="mt-4 text-sm text-[var(--text-muted)]">
          Delivering to <span className="font-medium text-[var(--text-primary)]">{view.destination_hint}</span>
        </p>
      )}

      <Arrival view={view} />

      {view.delivery_pin && (
        <div className="mt-6 rounded-[22px] bg-[var(--lime)] px-5 py-4">
          <p className="text-sm font-semibold text-[var(--lime-ink)]">Your delivery code</p>
          <p className="mt-1 font-mono text-[40px] font-bold leading-tight tracking-[0.3em] text-[var(--text-primary)]">{view.delivery_pin}</p>
          <p className="mt-1 text-xs text-[var(--lime-ink)]">
            Give this to your driver when they hand over the delivery. It proves it reached you.
          </p>
        </div>
      )}

      <RatingPrompt token={token} view={view} onRated={setView} />

      {view.driver_position ? (
        <DriverMap position={view.driver_position} />
      ) : (
        view.is_live && (
          <p className="mt-6 rounded-[16px] bg-[var(--surface-2)] px-4 py-3 text-sm text-[var(--text-secondary)]">
            {/* Said plainly rather than left as an empty space where a map should
                be. The map appears when the driver is on their way to this
                address - not while they're completing someone else's delivery. */}
            Live driver location appears here once your driver is on the way to you.
          </p>
        )
      )}

      {offline && (
        <p className="mt-4 text-xs text-[var(--amber)]">
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
    <div className="mt-8 border-t border-[var(--border)] pt-6">
      <p className="text-sm font-medium text-[var(--text-primary)]">
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
            className={`h-11 w-11 rounded-full border text-base font-semibold transition-colors disabled:opacity-50 ${
              score !== null && value <= score
                ? 'border-[var(--accent)] bg-[var(--accent)] text-white'
                : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:border-[var(--accent)]'
            }`}
          >
            {value}
          </button>
        ))}
      </div>

      {score !== null && (
        <div className="mt-4">
          <label htmlFor="rating-comment" className="text-xs text-[var(--text-muted)]">
            Anything you want us to know? (optional)
          </label>
          <textarea
            id="rating-comment"
            value={draftComment}
            maxLength={500}
            rows={3}
            onChange={(e) => setDraftComment(e.target.value)}
            placeholder="Driver couldn't find the loading dock…"
            className="mt-1 w-full rounded-[16px] border border-[var(--border)] p-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
          />
          <button
            type="button"
            disabled={saving || draftComment.trim() === (comment ?? '')}
            onClick={() => submit(score, draftComment)}
            className="mt-2 rounded-full bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
          >
            {saving ? 'Saving…' : comment ? 'Update' : 'Send'}
          </button>
        </div>
      )}

      {failed && (
        <p role="alert" className="mt-2 text-xs text-[var(--amber)]">
          We couldn&rsquo;t save that just now. Your delivery is unaffected.
        </p>
      )}
    </div>
  )
}

/**
 * The proof the driver captured, shown to the person it is proof for.
 *
 * **It was captured and shown to nobody.** `Stop.pod_photo_url` and
 * `pod_signature_url` have both been written since the app had a camera and a
 * signature pad, and the only thing in the backend that read either was an
 * idempotency comparison — so proof of delivery existed as a row and never as
 * something a human could look at.
 *
 * Here first, rather than in the ops console, because the recipient is who the
 * proof is *for*. It discloses nothing the delivery did not: a photo of their
 * own doorstep or the signature they gave, shown to the holder of a link scoped
 * to that one delivery.
 *
 * **Both, because both are proof**, and which one exists is a property of the
 * stop rather than of this page. `CompleteStopBody` also accepts a PIN and a
 * left-with note, neither of which is an image — those render nothing, because
 * an empty frame captioned "no photo" reads as a failure rather than as a
 * different method.
 */
function ProofOfDelivery({
  photoUrl,
  signatureUrl,
}: {
  photoUrl: string | null
  signatureUrl: string | null
}) {
  // A `local-capture://` marker is the stub backend saying nothing was stored.
  // It is not a URL a browser can load, and rendering it gives a broken image
  // where the proof should be.
  const usable = (url: string | null) => (url && /^https?:\/\//i.test(url) ? url : null)
  const photo = usable(photoUrl)
  const signature = usable(signatureUrl)
  if (!photo && !signature) return null

  return (
    <div className="mt-4 space-y-4">
      {photo && (
        <figure>
          <img
            src={photo}
            alt="Photo taken at the delivery"
            loading="lazy"
            className="w-full max-w-sm rounded-[16px] border border-[var(--border)]"
          />
          <figcaption className="mt-1.5 text-xs text-[var(--text-muted)]">
            Taken by the driver at the door.
          </figcaption>
        </figure>
      )}
      {signature && (
        <figure>
          <img
            src={signature}
            alt="Signature captured at the delivery"
            loading="lazy"
            // White ground: a signature is dark ink on transparency, which
            // disappears entirely if the page ever renders dark.
            className="w-full max-w-sm rounded-[16px] border border-[var(--border)] bg-[var(--surface)]"
          />
          <figcaption className="mt-1.5 text-xs text-[var(--text-muted)]">
            Signed for at the door.
          </figcaption>
        </figure>
      )}
    </div>
  )
}

function Arrival({ view }: { view: TrackingView }) {
  if (view.delivered_at) {
    return (
      <>
        <p className="mt-6 text-lg text-[var(--text-primary)]">
          Delivered at{' '}
          <span className="font-semibold">{formatTime(view.delivered_at)}</span>
        </p>
        <ProofOfDelivery
          photoUrl={view.pod_photo_url}
          signatureUrl={view.pod_signature_url}
        />
      </>
    )
  }
  if (!view.estimated_arrival) return null
  return (
    <div className="mt-6">
      <p className="text-sm text-[var(--text-muted)]">Estimated arrival</p>
      <p className="text-2xl font-semibold text-[var(--text-primary)]">
        {formatTime(view.estimated_arrival)}
      </p>
      {/* Labelled an estimate on the page, not just in the field name. There is
          still no verified travel-time model (E1), so presenting this as a
          promise would be overstating what we know. */}
      <p className="mt-1 text-xs text-[var(--text-muted)]">An estimate, not a guaranteed time.</p>
    </div>
  )
}

function DriverMap({ position }: { position: NonNullable<TrackingView['driver_position']> }) {
  // Kept for the life of the page, so a poll that moves the marker doesn't put
  // the button back.
  const [shown, setShown] = useState(false)
  const { lat, lng } = position
  const span = 0.012
  const bbox = [lng - span, lat - span / 2, lng + span, lat + span / 2].join('%2C')
  if (!shown) {
    return (
      <div className="mt-6 rounded-[20px] border border-[var(--border)] px-4 py-3">
        <p className="text-sm text-[var(--text-primary)]">Your driver is on the way to you.</p>
        <button
          type="button"
          onClick={() => setShown(true)}
          className="mt-2 rounded-[16px] border border-[var(--border-strong)] px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] focus-visible:outline focus-visible:outline-2"
        >
          Show the driver on a map
        </button>
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          The map comes from OpenStreetMap, which will see your IP address and the
          driver&rsquo;s current position.
        </p>
      </div>
    )
  }
  return (
    <div className="mt-6">
      <div className="overflow-hidden rounded-[20px] border border-[var(--border)]">
        <iframe
          title="Driver location"
          className="h-64 w-full"
          loading="lazy"
          referrerPolicy="no-referrer"
          src={`https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${lat}%2C${lng}`}
        />
      </div>
      <p className="mt-2 text-xs text-[var(--text-muted)]">
        Driver location updated {relativeTime(position.recorded_at)}. Map &copy;
        OpenStreetMap contributors.
      </p>
    </div>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[var(--bg-page)] px-4 py-8">
      <div className="mx-auto flex max-w-md flex-col gap-4">
        <div className="flex items-center gap-2 px-1 text-xl font-extrabold tracking-[-0.04em] text-[var(--text-primary)]">
          <img src="/lmx-mark.png" alt="" className="h-[26px] w-[26px] rounded-[8px]" />
          lmx
          <span className="ml-1 text-[13px] font-medium tracking-normal text-[var(--text-secondary)]">delivery tracking</span>
        </div>
        <main className="rounded-[var(--radius-lg)] bg-[var(--surface)] p-7">{children}</main>
      </div>
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
