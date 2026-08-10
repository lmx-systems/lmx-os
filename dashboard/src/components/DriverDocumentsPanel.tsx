import { useCallback, useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api, ApiError } from '../lib/api'
import type { PendingDriverDocumentView } from '../lib/types'

interface DriverDocumentsPanelProps {
  onToast: (message: string) => void
}

/**
 * Driver compliance review (docs/ROADMAP.md R4).
 *
 * **The human step that makes the availability gate mean something.** Before this
 * existed, a driver set their own document expiry date and the gate read it back to
 * them - so "documents on file, none expired" was a claim the driver had made about
 * themselves, presented as a check the system had performed. A driver with no
 * documents at all passed, because nothing on file could be expired.
 *
 * The design choice worth understanding here is the date field. It is **empty by
 * default and required to approve**, rather than prefilled with what the driver
 * claimed. Prefilling would make one-click approval the path of least resistance
 * and turn this panel into a rubber stamp on self-attested data - which is the
 * defect, moved one step later. The reviewer has to open the document and type what
 * it actually says, and the claimed date sits alongside so a discrepancy is
 * obvious.
 *
 * Not hub-scoped, unlike its neighbours: a driver whose license is unreviewed can't
 * work at any hub, so this is a whole-company queue.
 */
export function DriverDocumentsPanel({ onToast }: DriverDocumentsPanelProps) {
  const [documents, setDocuments] = useState<PendingDriverDocumentView[] | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [verifiedDates, setVerifiedDates] = useState<Record<string, string>>({})
  const [reasons, setReasons] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    try {
      setDocuments(await api.listPendingDriverDocuments())
    } catch (err) {
      onToast(
        `Could not load driver documents: ${err instanceof ApiError ? err.message : String(err)}`,
      )
    }
  }, [onToast])

  useEffect(() => {
    void load()
  }, [load])

  async function verify(doc: PendingDriverDocumentView) {
    const entered = verifiedDates[doc.document_id]
    // Checked here as well as server-side so the reason is immediate rather than a
    // 422 - and so the reviewer is nudged to read the document rather than trust
    // the claim.
    if (!entered) {
      onToast('Enter the expiry date shown on the document itself before verifying.')
      return
    }

    setBusyId(doc.document_id)
    try {
      const result = await api.reviewDriverDocument(doc.document_id, {
        decision: 'verify',
        verified_expires_at: entered,
      })
      onToast(
        result.driver_can_go_on_shift
          ? `${doc.driver_name} verified — they can go on shift now.`
          : `${doc.doc_type} verified. Still outstanding: ${result.outstanding_problems.join('; ')}`,
      )
      await load()
    } catch (err) {
      onToast(`Could not verify: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setBusyId(null)
    }
  }

  async function reject(doc: PendingDriverDocumentView) {
    const reason = reasons[doc.document_id]?.trim()
    if (!reason) {
      onToast("Say why — a driver can't fix a rejection they can't read.")
      return
    }

    setBusyId(doc.document_id)
    try {
      await api.reviewDriverDocument(doc.document_id, {
        decision: 'reject',
        rejection_reason: reason,
      })
      onToast(`${doc.doc_type} rejected — ${doc.driver_name} has been asked to re-upload.`)
      await load()
    } catch (err) {
      onToast(`Could not reject: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card title="Driver documents" meta="Verify before a driver can go on shift">
      {documents === null ? (
        <p className="text-sm text-[var(--text-muted)]">Loading driver documents…</p>
      ) : documents.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">
          Nothing waiting. Uploaded licenses and insurance certificates appear here — a driver
          can&rsquo;t go on shift until each one is verified.
        </p>
      ) : (
        <div className="flex flex-col gap-3 text-[13px]">
          {documents.map((doc) => (
            <div
              key={doc.document_id}
              className="flex flex-col gap-3 rounded-[var(--radius)] border border-[var(--border)] p-3"
            >
              <div className="flex flex-col gap-1">
                <div className="font-medium text-[var(--text-primary)]">
                  {doc.driver_name} · {doc.doc_type}
                </div>
                <div className="text-[12px] text-[var(--text-muted)]">
                  Driver says it expires {new Date(doc.claimed_expires_at).toLocaleDateString()} ·
                  uploaded {new Date(doc.uploaded_at).toLocaleDateString()}
                </div>
                {doc.file_url && doc.file_url.startsWith('http') ? (
                  <a
                    href={doc.file_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[12px] text-[var(--accent)] underline"
                  >
                    Open the document
                  </a>
                ) : (
                  // The stub upload client issues a local-capture:// marker when
                  // PHOTO_UPLOAD_BUCKET is unset, so there is genuinely nothing to
                  // open. Said plainly rather than rendered as a dead link that
                  // makes a reviewer think the upload failed.
                  <span className="text-[12px] text-[var(--text-muted)]">
                    No stored file — document uploads are not configured on this deployment.
                  </span>
                )}
              </div>

              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1">
                  <span className="text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                    Expiry on the document
                  </span>
                  <input
                    type="date"
                    value={verifiedDates[doc.document_id] ?? ''}
                    onChange={(e) =>
                      setVerifiedDates((prev) => ({ ...prev, [doc.document_id]: e.target.value }))
                    }
                    className="rounded-[var(--radius)] border border-[var(--border)] bg-transparent px-2 py-1 text-[12px] text-[var(--text-primary)]"
                  />
                </label>
                <label className="flex flex-1 flex-col gap-1">
                  <span className="text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                    Or why it&rsquo;s being rejected
                  </span>
                  <input
                    type="text"
                    placeholder="e.g. the photo cuts off the expiry date"
                    value={reasons[doc.document_id] ?? ''}
                    onChange={(e) =>
                      setReasons((prev) => ({ ...prev, [doc.document_id]: e.target.value }))
                    }
                    className="w-full rounded-[var(--radius)] border border-[var(--border)] bg-transparent px-2 py-1 text-[12px] text-[var(--text-primary)]"
                  />
                </label>
              </div>

              <div className="flex gap-2">
                <button
                  disabled={busyId === doc.document_id}
                  onClick={() => verify(doc)}
                  className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-60"
                >
                  Verify
                </button>
                <button
                  disabled={busyId === doc.document_id}
                  onClick={() => reject(doc)}
                  className="rounded-[var(--radius)] border border-[var(--border-strong)] px-3 py-1.5 text-[12px] text-[var(--text-secondary)] disabled:opacity-60"
                >
                  Reject
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
