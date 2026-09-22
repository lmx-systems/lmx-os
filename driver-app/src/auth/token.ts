import * as SecureStore from 'expo-secure-store';

import { setAuthToken } from '../api/client';

export const TOKEN_STORAGE_KEY = 'lmx_driver_token';

/**
 * Take a token into use, everywhere it needs to live.
 *
 * Two places, and missing either is its own bug: the in-memory variable the
 * API client attaches to requests, and SecureStore, which is what survives
 * the app being killed.
 *
 * This exists because `/driver/auth/refresh` returns a fresh token and
 * **nothing was doing anything with it** — `AuthContext` called it as
 * `api.refreshToken().catch(() => {})` and discarded the result. So the app
 * never actually adopted a refreshed token: when the original expired, every
 * request 401'd, and the outbox marked a shift's queued stop events
 * permanently failed (see `outboxManager.flush`).
 *
 * Here rather than in `AuthContext` so `outboxManager` can call it without
 * reaching into React state, which it has no business doing from a
 * background flush.
 */
export async function adoptAuthToken(token: string): Promise<void> {
  setAuthToken(token);
  await SecureStore.setItemAsync(TOKEN_STORAGE_KEY, token);
}

export async function clearAuthToken(): Promise<void> {
  setAuthToken(null);
  await SecureStore.deleteItemAsync(TOKEN_STORAGE_KEY);
}
