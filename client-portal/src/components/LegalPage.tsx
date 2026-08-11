import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import { api } from '../lib/api'
import type { LegalDocumentBody } from '../lib/types'

interface LegalPageProps {
  kind: 'terms' | 'privacy'
}

/**
 * The terms and the privacy policy, at /terms and /privacy.
 *
 * These exist so the signup checkbox can link to a document instead of naming one.
 * Before this page the checkbox said "I agree to LMX's terms of service and privacy
 * policy" in plain text and there was nowhere to go and read them, which made the
 * recorded acceptance a record of a tick rather than of assent.
 *
 * Public, no login. Fetched rather than bundled: app/legal/content/ is the single
 * copy, so the version an applicant reads here is by construction the version the
 * form is about to record.
 *
 * A draft renders, with a banner saying so. The alternative - refusing to serve an
 * unpublished document - leaves this page blank for anyone who follows the link, and
 * a reader told "this is not final" is better informed than one shown nothing.
 */
export function LegalPage({ kind }: LegalPageProps) {
  const [doc, setDoc] = useState<LegalDocumentBody | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    api
      .legalDocument(kind)
      .then((d) => {
        if (live) setDoc(d)
      })
      .catch(() => {
        if (live) setFailed(true)
      })
    return () => {
      live = false
    }
  }, [kind])

  return (
    <div className="min-h-screen bg-[var(--bg-page)] px-4 py-10">
      <div className="mx-auto w-full max-w-2xl">
        <a href="/signup" className="mb-6 inline-flex items-center gap-2">
          <img src="/lmx-mark.png" alt="LMX" className="h-8 w-8 rounded-[var(--radius)]" />
          <span className="text-sm font-medium text-[var(--text-secondary)]">LMX</span>
        </a>

        {failed && (
          <p role="alert" className="text-sm text-[var(--danger,#b3261e)]">
            We could not load this document. Please reload the page.
          </p>
        )}

        {doc && (
          <article>
            <h1 className="text-2xl font-semibold text-[var(--text-primary)]">{doc.title}</h1>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Version {doc.version}
              {doc.effective ? ` · in force from ${doc.effective}` : ''}
            </p>

            {!doc.published && (
              <p className="mt-4 rounded-[var(--radius)] border border-[var(--border)] px-3 py-2 text-[13px] text-[var(--text-secondary)]">
                <strong className="font-medium">This is a draft.</strong> It has not been
                finalised and it does not yet apply to anyone. We are not asking anybody to
                agree to it.
              </p>
            )}

            <Markdown source={doc.body} />
          </article>
        )}
      </div>
    </div>
  )
}

/**
 * Just enough markdown for a legal document: headings, paragraphs, bullets, and bold.
 *
 * Deliberately not a markdown library. This renders two files that this repo writes,
 * so the input is known - and a dependency that turns arbitrary text into HTML on a
 * public unauthenticated page is a larger decision than the formatting is worth.
 * Nothing here interprets raw HTML, so the document cannot inject any.
 */
function Markdown({ source }: { source: string }) {
  const blocks: ReactElement[] = []
  let bullets: string[] = []

  const flushBullets = () => {
    if (!bullets.length) return
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="mt-3 list-disc space-y-1.5 pl-5">
        {bullets.map((item, i) => (
          <li key={i} className="text-sm leading-relaxed text-[var(--text-secondary)]">
            <Inline text={item} />
          </li>
        ))}
      </ul>,
    )
    bullets = []
  }

  for (const raw of source.split('\n\n')) {
    const block = raw.trim()
    if (!block) continue

    if (block.startsWith('- ')) {
      // A whole bullet list arrives as one block; a wrapped bullet continues its
      // previous line rather than starting a new item.
      for (const line of block.split('\n')) {
        if (line.trimStart().startsWith('- ')) bullets.push(line.trimStart().slice(2))
        else if (bullets.length) bullets[bullets.length - 1] += ' ' + line.trim()
      }
      continue
    }
    flushBullets()

    if (block.startsWith('## ')) {
      blocks.push(
        <h2
          key={blocks.length}
          className="mt-8 text-base font-semibold text-[var(--text-primary)]"
        >
          {block.slice(3)}
        </h2>,
      )
      continue
    }

    blocks.push(
      <p key={blocks.length} className="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
        <Inline text={block.replace(/\n/g, ' ')} />
      </p>,
    )
  }
  flushBullets()

  return <div className="mt-2">{blocks}</div>
}

/** `**bold**` and `*italic*`, nothing else. */
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return (
            <strong key={i} className="font-semibold text-[var(--text-primary)]">
              {part.slice(2, -2)}
            </strong>
          )
        }
        if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
          return <em key={i}>{part.slice(1, -1)}</em>
        }
        return <span key={i}>{part}</span>
      })}
    </>
  )
}
