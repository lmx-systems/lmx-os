jest.mock('expo-file-system', () => ({
  documentDirectory: 'file:///docs/',
  makeDirectoryAsync: jest.fn(async () => undefined),
  copyAsync: jest.fn(async () => undefined),
  deleteAsync: jest.fn(async () => undefined),
  uploadAsync: jest.fn(),
  FileSystemUploadType: { BINARY_CONTENT: 0 },
}));
jest.mock('../api/client', () => ({
  api: { createUploadUrl: jest.fn(), recordHandoffPhoto: jest.fn() },
}));

import * as FileSystem from 'expo-file-system';

import { api } from '../api/client';
import { keepPhoto, sendHandoffPhoto } from './handoffPhotos';

const fs = FileSystem as unknown as Record<'copyAsync' | 'deleteAsync' | 'uploadAsync', jest.Mock>;
const mockApi = api as unknown as Record<'createUploadUrl' | 'recordHandoffPhoto', jest.Mock>;

/** DE-4: a handoff photo waits as a file, and is deleted only once the server has it. */
describe('a handoff photo, offline', () => {
  beforeEach(() => {
    for (const fn of [fs.copyAsync, fs.deleteAsync, fs.uploadAsync, mockApi.createUploadUrl, mockApi.recordHandoffPhoto]) fn.mockReset();
    fs.copyAsync.mockResolvedValue(undefined);
    fs.deleteAsync.mockResolvedValue(undefined);
    mockApi.recordHandoffPhoto.mockResolvedValue({});
  });

  it('keeps the camera file somewhere the system will not clear', async () => {
    const kept = await keepPhoto('file:///cache/camera.jpg');

    expect(kept.startsWith('file:///docs/handoff/')).toBe(true);
    expect(fs.copyAsync).toHaveBeenCalledWith({ from: 'file:///cache/camera.jpg', to: kept });
  });

  it('uploads, records the slot, then deletes the kept file', async () => {
    mockApi.createUploadUrl.mockResolvedValue({ upload_url: 'https://s3/put', final_url: 'https://s3/key', requires_upload: true });
    fs.uploadAsync.mockResolvedValue({ status: 200 });

    await sendHandoffPhoto('s1', 'path', 'file:///docs/handoff/a.jpg', 'image/jpeg');

    expect(mockApi.createUploadUrl).toHaveBeenCalledWith('s1', 'handoff', 'image/jpeg');
    expect(fs.uploadAsync).toHaveBeenCalledWith('https://s3/put', 'file:///docs/handoff/a.jpg', expect.objectContaining({ httpMethod: 'PUT' }));
    expect(mockApi.recordHandoffPhoto).toHaveBeenCalledWith('s1', 'path', 'https://s3/key');
    expect(fs.deleteAsync).toHaveBeenCalledWith('file:///docs/handoff/a.jpg', { idempotent: true });
  });

  it('keeps the file when the upload fails, so the retry has it', async () => {
    mockApi.createUploadUrl.mockResolvedValue({ upload_url: 'https://s3/put', final_url: 'https://s3/key', requires_upload: true });
    fs.uploadAsync.mockResolvedValue({ status: 500 });

    await expect(sendHandoffPhoto('s1', 'path', 'file:///docs/handoff/a.jpg', 'image/jpeg')).rejects.toThrow('Upload failed (500)');

    expect(mockApi.recordHandoffPhoto).not.toHaveBeenCalled();
    expect(fs.deleteAsync).not.toHaveBeenCalled();
  });

  it('records it without an upload where no bucket is configured', async () => {
    mockApi.createUploadUrl.mockResolvedValue({ upload_url: '', final_url: 'local://key', requires_upload: false });

    await sendHandoffPhoto('s1', 'handoff', 'file:///docs/handoff/b.jpg', 'image/jpeg');

    expect(fs.uploadAsync).not.toHaveBeenCalled();
    expect(mockApi.recordHandoffPhoto).toHaveBeenCalledWith('s1', 'handoff', 'local://key');
  });
});
