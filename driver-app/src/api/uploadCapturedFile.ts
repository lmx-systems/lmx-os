import { api } from './client';
import type { UploadContentType, UploadKind } from './types';

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
