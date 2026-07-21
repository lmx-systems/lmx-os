import AsyncStorage from '@react-native-async-storage/async-storage';

import type { OutboxItem } from './types';

const STORAGE_KEY = 'lmx_driver_outbox_v1';

// A single FIFO JSON blob, not a relational table - there's exactly one
// query pattern here ("read the whole array, mutate, write it back"),
// never a WHERE/join/index, so expo-sqlite would be schema/migration
// surface for nothing. Not SecureStore either - that's for secrets (this
// queue holds stop actions, not credentials) and its keychain round-trip
// is slower than warranted for data with no confidentiality requirement.
export async function loadOutbox(): Promise<OutboxItem[]> {
  const raw = await AsyncStorage.getItem(STORAGE_KEY);
  if (!raw) return [];
  try {
    return JSON.parse(raw) as OutboxItem[];
  } catch {
    return [];
  }
}

export async function saveOutbox(items: OutboxItem[]): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(items));
}
