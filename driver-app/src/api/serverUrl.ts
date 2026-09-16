import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants from 'expo-constants';

/**
 * Which LMX OS this app talks to, changeable on the device.
 *
 * `app.json`'s `extra.apiBaseUrl` is baked in at build time, and its default is
 * `http://localhost:8000`. On a simulator that resolves to the developer's own
 * machine and everything works. On a real handset it resolves to the handset,
 * so the app can reach nothing at all - which is exactly what happened to the
 * first Android build: it installed, launched, and could not sign in.
 *
 * Rebuilding for every network change is the wrong answer. A build takes seven
 * minutes on EAS, and the address changes when you move between a desk and a
 * meeting room. So the address is a setting, with the build-time value as its
 * default.
 *
 * **Not a debug-only escape hatch.** The same mechanism is how a real install
 * points at staging rather than production, which is a thing anyone testing a
 * pilot will need on day one.
 */
const OVERRIDE_KEY = 'lmx_driver_api_base_url_v1';

function buildTimeDefault(): string {
  return (
    (Constants.expoConfig?.extra?.apiBaseUrl as string | undefined) ?? 'http://localhost:8000'
  );
}

// Read once at startup and held here, because `request()` is synchronous at the
// point it needs the value and every call site would otherwise have to await.
let current: string = buildTimeDefault();

export function getApiBaseUrl(): string {
  return current;
}

export function getBuildTimeApiBaseUrl(): string {
  return buildTimeDefault();
}

/**
 * Load any saved override. Call once, before the first request.
 *
 * Falls back silently to the build-time default: a device whose storage is
 * unreadable should still reach the default server rather than refuse to start.
 */
export async function loadApiBaseUrl(): Promise<string> {
  try {
    const saved = await AsyncStorage.getItem(OVERRIDE_KEY);
    if (saved) current = saved;
  } catch {
    // Keep the default.
  }
  return current;
}

/**
 * Point the app at a different server. Takes effect immediately.
 *
 * Trailing slashes are trimmed because every call site concatenates a path that
 * already starts with one, and `//driver/me` is a 404 that looks like a bug in
 * the backend rather than a typo in a settings field.
 */
export async function setApiBaseUrl(url: string): Promise<void> {
  const cleaned = url.trim().replace(/\/+$/, '');
  if (!cleaned) {
    await AsyncStorage.removeItem(OVERRIDE_KEY).catch(() => {});
    current = buildTimeDefault();
    return;
  }
  current = cleaned;
  try {
    await AsyncStorage.setItem(OVERRIDE_KEY, cleaned);
  } catch {
    // The change still applies for this session.
  }
}

/** Whether the address in use is a saved override rather than the built-in one. */
export function isOverridden(): boolean {
  return current !== buildTimeDefault();
}

/**
 * Is this a plausible server address?
 *
 * Deliberately permissive about the host - `http://192.168.1.5:8000` is the
 * single most likely correct answer during a pilot, and a validator that
 * demanded https would reject it. It rejects the mistakes that produce a
 * confusing failure much later: a missing scheme, and a path.
 */
export function describeProblem(url: string): string | null {
  const cleaned = url.trim();
  if (!cleaned) return 'Enter an address, or clear the field to use the default.';
  if (!/^https?:\/\//i.test(cleaned)) return 'Start with http:// or https://';
  try {
    const parsed = new URL(cleaned);
    if (!parsed.hostname) return 'That address has no host.';
    if (parsed.pathname && parsed.pathname !== '/') {
      return 'Just the host and port - no path.';
    }
  } catch {
    return "That doesn't look like a web address.";
  }
  return null;
}
