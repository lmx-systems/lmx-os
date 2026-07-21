import * as SecureStore from 'expo-secure-store';

const DEVICE_ID_STORAGE_KEY = 'lmx_driver_device_id';

// A stable per-install id, generated once and persisted - not an OS
// advertising id. Lets the backend bind a session to "this install of the
// app" so a specific device can be revoked later (see
// app/models/driver_device.py) without invalidating every device this
// driver has ever signed in on.
export async function getOrCreateDeviceId(): Promise<string> {
  const existing = await SecureStore.getItemAsync(DEVICE_ID_STORAGE_KEY);
  if (existing) return existing;

  const generated = `dev-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  await SecureStore.setItemAsync(DEVICE_ID_STORAGE_KEY, generated);
  return generated;
}
