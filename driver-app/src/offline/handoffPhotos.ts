import * as FileSystem from 'expo-file-system';

import { api } from '../api/client';
import type { HandoffPhotoSlot, UploadContentType } from '../api/types';

/**
 * The four guided handoff photos, offline (docs/ROADMAP_1.5.md DE-4).
 *
 * A proof photo uploads straight from the camera and needs signal there and
 * then (`api/uploadCapturedFile.ts`): the outbox stores only JSON. A handoff
 * photo is taken at doors in exactly the places with no signal, so it is kept
 * as a file the outbox can point at - copied out of the camera's cache, which
 * the system may clear, into the app's own documents - and sent when the
 * phone is back online: mint an upload URL, PUT the file, record it on the
 * handoff point, then delete the copy.
 */

const DIRECTORY = `${FileSystem.documentDirectory ?? ''}handoff/`;

/** Copy a captured photo somewhere it survives until it is sent. */
export async function keepPhoto(cacheUri: string): Promise<string> {
  await FileSystem.makeDirectoryAsync(DIRECTORY, { intermediates: true }).catch(() => undefined);
  const kept = `${DIRECTORY}${Date.now()}-${Math.random().toString(36).slice(2)}.jpg`;
  await FileSystem.copyAsync({ from: cacheUri, to: kept });
  return kept;
}

/** Throw away a kept photo the driver replaced or abandoned. */
export async function discardPhoto(uri: string): Promise<void> {
  await FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => undefined);
}

/**
 * Send one kept photo. Safe to retry from the start: a fresh upload URL is
 * minted each time, and recording a slot overwrites it. The copy is deleted
 * only after the server has it.
 */
export async function sendHandoffPhoto(
  stopId: string,
  slot: HandoffPhotoSlot,
  uri: string,
  contentType: UploadContentType,
): Promise<void> {
  const { upload_url, final_url, requires_upload } = await api.createUploadUrl(stopId, 'handoff', contentType);
  if (requires_upload) {
    const result = await FileSystem.uploadAsync(upload_url, uri, {
      httpMethod: 'PUT',
      uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT,
      headers: { 'Content-Type': contentType },
    });
    if (result.status < 200 || result.status >= 300) {
      throw new Error(`Upload failed (${result.status})`);
    }
  }
  await api.recordHandoffPhoto(stopId, slot, final_url);
  await discardPhoto(uri);
}
