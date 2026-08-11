import { api } from './client';
import type { DocType, UploadContentType, UploadKind } from './types';

// Real photo/signature/barcode capture (docs/ROADMAP.md A2/A3): a local
// file URI (from expo-camera's takePictureAsync, or a signature pad's
// exported PNG) gets uploaded directly to wherever
// api.createUploadUrl() points - S3 once a real bucket is configured,
// or nowhere at all (requires_upload=false) if it isn't, in which case
// final_url is already the whole answer and there's nothing left to do.
//
// Uploading straight from capture, before the outbox even sees this
// action, is a deliberate scope boundary: outboxManager's queue only
// ever stores plain JSON (see offline/outboxManager.ts), not binary
// blobs pending upload, so this step needs real connectivity - unlike
// arrive/scan/complete, which stay fully offline-safe regardless.
export async function uploadCapturedFile(
  stopId: string,
  kind: UploadKind,
  localUri: string,
  contentType: UploadContentType,
): Promise<string> {
  const { upload_url, final_url, requires_upload } = await api.createUploadUrl(stopId, kind, contentType);
  if (!requires_upload) {
    return final_url;
  }

  const fileResponse = await fetch(localUri);
  const fileBlob = await fileResponse.blob();

  const putResponse = await fetch(upload_url, {
    method: 'PUT',
    headers: { 'Content-Type': contentType },
    body: fileBlob,
  });
  if (!putResponse.ok) {
    throw new Error(`Upload failed (${putResponse.status})`);
  }

  return final_url;
}


// The same two steps for a compliance document (docs/ROADMAP.md R4).
//
// **Separate from the stop-scoped function above because the key belongs to a
// driver, not a delivery** - and because the backend writes `file_url` on the
// document row itself from the key it minted. A driver used to be able to send
// any string as their licence scan; now the only way to get a URL onto that row
// is to have actually uploaded through here.
//
// The claimed expiry travels with the upload request so a reviewer has something
// to compare the document against, and because a new upload resets the review -
// a document that was verified and then replaced is not still verified.
export async function uploadDocumentFile(
  docType: DocType,
  localUri: string,
  contentType: UploadContentType,
  claimedExpiresAt: string,
): Promise<string> {
  const { upload_url, final_url, requires_upload } = await api.createDocumentUploadUrl(docType, {
    content_type: contentType,
    claimed_expires_at: claimedExpiresAt,
  });
  if (!requires_upload) {
    // No bucket configured on this deployment - final_url is already the whole
    // answer and there is nothing to PUT.
    return final_url;
  }

  const fileResponse = await fetch(localUri);
  const fileBlob = await fileResponse.blob();
  const putResponse = await fetch(upload_url, {
    method: 'PUT',
    headers: { 'Content-Type': contentType },
    body: fileBlob,
  });
  if (!putResponse.ok) {
    throw new Error(`Upload failed (${putResponse.status})`);
  }
  return final_url;
}
