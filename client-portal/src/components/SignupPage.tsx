import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../lib/api'
import type { LegalDocumentsView } from '../lib/types'

interface SignupPageProps {
  onSignedIn: () => void
}

/** What to tell an applicant when a submission fails, by why it failed. */
function messageFor(err: unknown): string {
  if (!(err instanceof ApiError)) {
    return 'Something went wrong. Please check your details and try again.'
  }
  switch (err.status) {
    case 429:
      return 'Too many attempts from this network. Please try again later.'
    case 409:
      // The terms changed while this page was open, so the box they ticked was
      // against text they can no longer be said to have read. A reload re-fetches
      // the version and the links.
      return 'Our terms have been updated. Please reload the page and read them again before continuing.'
    case 503:
      return 'We are not accepting new accounts just now. Please try again shortly.'
    default:
      return 'Something went wrong. Please check your details and try again.'
  }
}


/**
 * The public signup page - this is the URL LMX shares or embeds
 * (docs/LMX_LINK_PLAN.md).
 *
 * Reachable at /signup with no account and no token. It is the front of the
 * funnel, so the field list is deliberately short: everything here is something
 * a distributor knows off the top of their head. Nothing that needs a contract,
 * a rate negotiation, or their IT department - those come later, and asking for
 * them now is where a funnel dies.
 *
 * Submitting does NOT grant access. The application goes to an LMX review queue
 * and nobody can dispatch a van until a human approves it - which is what keeps
 * a self-serve form compatible with LMX being an operator rather than SaaS.
 */
export function SignupPage({ onSignedIn }: SignupPageProps) {
  const [companyName, setCompanyName] = useState('')
  const [contactName, setContactName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [serviceArea, setServiceArea] = useState('')
  const [password, setPassword] = useState('')
  const [acceptedTerms, setAcceptedTerms] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  // Which terms this form is presenting, from the server. This page used to hold
  // the version as a constant and send it up, which meant the record of what an
  // applicant accepted was written by the applicant's browser. Now
  // app/legal/documents.py is the only place a version exists; the form asks, shows
  // the matching links, and echoes the version back so the server can refuse a
  // submission made against text that has since changed.
  const [legal, setLegal] = useState<LegalDocumentsView | null>(null)
  const [legalFailed, setLegalFailed] = useState(false)

  useEffect(() => {
    let live = true
    api
      .legalDocuments()
      .then((d) => {
        if (live) setLegal(d)
      })
      .catch(() => {
        if (live) setLegalFailed(true)
      })
    return () => {
      live = false
    }
  }, [])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    // Cannot happen through the UI - the button is disabled until `legal` loads -
    // but the guard is here rather than in a comment because submitting without it
    // would send an empty version string and get a 409 the applicant cannot act on.
    if (!legal) return
    setError(null)
    setSubmitting(true)
    try {
      await api.signup({
        company_name: companyName,
        contact_name: contactName,
        contact_email: email,
        contact_phone: phone || null,
        service_area: serviceArea,
        password,
        terms_version: legal.terms.version,
        accepted_terms: acceptedTerms,
      })
      setSubmitted(true)
    } catch (err) {
      setError(messageFor(err))
    } finally {
      setSubmitting(false)
    }
  }

  if (submitted) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--bg-page)] px-4">
        <div className="w-full max-w-md rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 text-center shadow-[var(--shadow-md)]">
          <img src="/lmx-mark.png" alt="LMX" className="mx-auto mb-4 h-10 w-10 rounded-[var(--radius)]" />
          <h1 className="text-[17px] font-semibold text-[var(--text-primary)]">Thanks — we've got your details</h1>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            Our team will review your account and be in touch shortly. Once you're approved you'll be able
            to sign in and start sending us deliveries.
          </p>
          <button
            onClick={onSignedIn}
            className="mt-5 text-sm font-medium text-[var(--accent)] hover:underline"
          >
            Already approved? Sign in
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-page)] px-4 py-8">
      <div className="w-full max-w-md rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 shadow-[var(--shadow-md)]">
        <div className="mb-5 flex items-center gap-2">
          <img src="/lmx-mark.png" alt="LMX" className="h-8 w-8 rounded-[var(--radius)]" />
          <div>
            <div className="text-[15px] font-semibold text-[var(--text-primary)]">Send deliveries with LMX</div>
            <div className="text-xs text-[var(--text-muted)]">Tell us about your business — takes a minute</div>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <Field label="Company name" value={companyName} onChange={setCompanyName} required autoComplete="organization" placeholder="Midtown Auto Parts" />
          <Field label="Your name" value={contactName} onChange={setContactName} required autoComplete="name" placeholder="Jordan Rivera" />
          <Field label="Email" value={email} onChange={setEmail} required type="email" autoComplete="email" placeholder="you@yourcompany.com" />
          <Field label="Phone" value={phone} onChange={setPhone} type="tel" autoComplete="tel" placeholder="Optional" />
          <Field
            label="Where do you deliver?"
            value={serviceArea}
            onChange={setServiceArea}
            required
            placeholder="Austin metro"
            hint="Roughly where your deliveries go — a city or area is fine."
          />
          <Field
            label="Choose a password"
            value={password}
            onChange={setPassword}
            required
            type="password"
            autoComplete="new-password"
            minLength={10}
            hint="At least 10 characters. You'll use this once your account is approved."
          />

          {/* Signup closed because the documents behind the checkbox are still
              drafts. Said plainly rather than by a dead button: an applicant who
              cannot tell why the form does nothing assumes it is broken. */}
          {legal && !legal.signup_open && (
            <p className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-sunk,transparent)] px-3 py-2 text-[13px] text-[var(--text-secondary)]">
              We are not taking new accounts through this page yet. Our terms of service
              are being finalised, and we will not ask you to agree to something you
              cannot read.
            </p>
          )}
          {legalFailed && (
            <p role="alert" className="text-[13px] text-[var(--danger,#b3261e)]">
              We could not load our terms just now. Please reload the page.
            </p>
          )}

          <label className="mt-1 flex items-start gap-2 text-[13px] text-[var(--text-secondary)]">
            <input
              type="checkbox"
              required
              checked={acceptedTerms}
              onChange={(e) => setAcceptedTerms(e.target.checked)}
              className="mt-0.5 accent-[var(--accent)]"
            />
            <span>
              I agree to LMX's{' '}
              <a
                href={legal ? legal.terms.path : '/terms'}
                target="_blank"
                rel="noreferrer"
                className="font-medium text-[var(--accent)] hover:underline"
              >
                terms of service
              </a>{' '}
              and{' '}
              <a
                href={legal ? legal.privacy.path : '/privacy'}
                target="_blank"
                rel="noreferrer"
                className="font-medium text-[var(--accent)] hover:underline"
              >
                privacy policy
              </a>
              .
            </span>
          </label>

          {error && (
            <p role="alert" className="text-[13px] text-[var(--danger,#b3261e)]">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting || !legal || !legal.signup_open}
            className="mt-1 rounded-[var(--radius)] bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {submitting ? 'Sending…' : 'Request an account'}
          </button>

          <p className="text-center text-xs text-[var(--text-muted)]">
            Already have an account?{' '}
            <button type="button" onClick={onSignedIn} className="font-medium text-[var(--accent)] hover:underline">
              Sign in
            </button>
          </p>
        </form>
      </div>
    </div>
  )
}

interface FieldProps {
  label: string
  value: string
  onChange: (v: string) => void
  required?: boolean
  type?: string
  autoComplete?: string
  placeholder?: string
  hint?: string
  minLength?: number
}

function Field({ label, value, onChange, hint, ...rest }: FieldProps) {
  return (
    <label className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
      {label}
      <input
        {...rest}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
      />
      {hint && <span className="text-xs text-[var(--text-muted)]">{hint}</span>}
    </label>
  )
}
