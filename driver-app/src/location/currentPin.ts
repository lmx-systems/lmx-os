import * as Location from 'expo-location';

/** Where the phone is, as the server's pins take it (DE-4). */
export interface Pin {
  lat: number;
  lng: number;
  accuracy_m: number | null;
}

// The foreground watcher (reportDriverLocation.ts) refreshes the phone's
// position every 30 seconds, so a last-known fix this recent is where the
// driver is standing. Older than this and it could be the previous street.
const MAX_AGE_MS = 60_000;

/**
 * The phone's last known position, or null - never a wait.
 *
 * A pin is a measurement beside the action, so the Arrived tap and Delivered
 * never wait on a GPS fix: no permission, no recent fix or any error is null,
 * and the server records the stop without a pin. Unknown, not zero.
 */
export async function currentPin(): Promise<Pin | null> {
  try {
    const permission = await Location.getForegroundPermissionsAsync();
    if (!permission.granted) return null;
    const fix = await Location.getLastKnownPositionAsync({ maxAge: MAX_AGE_MS });
    if (!fix) return null;
    return {
      lat: fix.coords.latitude,
      lng: fix.coords.longitude,
      accuracy_m: fix.coords.accuracy ?? null,
    };
  } catch {
    return null;
  }
}

/** The door pin, as CompleteStopBody names it. Empty when there is no pin. */
export function doorPin(pin: Pin | null): { door_lat?: number; door_lng?: number; door_accuracy_m?: number } {
  if (!pin) return {};
  return {
    door_lat: pin.lat,
    door_lng: pin.lng,
    ...(pin.accuracy_m !== null ? { door_accuracy_m: pin.accuracy_m } : {}),
  };
}
