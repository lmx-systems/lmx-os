import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';

/**
 * Whether the driver has been shown the disclosure, and whether to show it now.
 *
 * DRV-2. The disclosure screen exists and is reachable from Profile, but a
 * driver who never opens Profile never sees it and never grants - so the
 * sensor would stay off for everyone by default and DRV-1 would record
 * nothing. Something has to ask.
 *
 * **Asked once, not once per launch.** A prompt that returns every shift is a
 * prompt that gets dismissed reflexively, and on iOS the "always" dialog can
 * only be shown once anyway - after that the OS silently returns the previous
 * answer, so re-asking would show our screen, request nothing, and look
 * broken. The Profile entry is the way back for anyone who declined.
 */
const ASKED_KEY = 'lmx_driver_background_location_asked_v1';

export async function hasBeenAsked(): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(ASKED_KEY)) === 'true';
  } catch {
    // Storage unavailable. Treating it as "already asked" errs towards not
    // prompting, which is the right way to be wrong: a driver who is never
    // prompted can still grant from Profile, whereas one prompted on every
    // launch has no way to stop it.
    return true;
  }
}

export async function markAsked(): Promise<void> {
  try {
    await AsyncStorage.setItem(ASKED_KEY, 'true');
  } catch {
    // Best effort. Worst case the driver sees the screen twice.
  }
}

/**
 * Should the disclosure be shown right now?
 *
 * Only when there is work to measure, the permission is genuinely missing, and
 * we have not asked before. Asking a driver with no route to approve
 * background location is asking them to agree to something abstract, which is
 * both a worse consent and a worse conversion.
 */
export async function shouldAskForBackgroundLocation(hasRoute: boolean): Promise<boolean> {
  if (!hasRoute) return false;
  if (await hasBeenAsked()) return false;
  try {
    const { granted } = await Location.getBackgroundPermissionsAsync();
    return !granted;
  } catch {
    return false;
  }
}
