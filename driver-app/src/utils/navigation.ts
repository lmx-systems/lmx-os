import { Linking, Platform } from 'react-native';

/**
 * Real turn-by-turn navigation (docs/ROADMAP.md A5) - hands off to the
 * device's own native maps app instead of embedding a maps SDK/rendering
 * a route ourselves. Apple Maps and Google Maps both already do live
 * turn-by-turn, voice guidance, and real-time traffic/rerouting far
 * better than reimplementing that here would; this only needs to get the
 * driver into one of them, already pointed at the right destination.
 *
 * No API key required either way - these are plain URL schemes/intents,
 * not the Google Maps SDK's autocomplete/embedded-view APIs (the ones
 * `settings.google_maps_api_key`, app/config.py, actually gates).
 */

const WEB_FALLBACK_URL = (lat: number, lng: number) =>
  `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;

// Tries Linking.openURL directly, in order, falling through on rejection -
// deliberately not gated on Linking.canOpenURL first. On iOS, canOpenURL
// requires the scheme to be pre-declared in LSApplicationQueriesSchemes or
// it always reports false regardless of whether the app is installed; on
// Android 11+, canOpenURL's underlying package query is subject to package-
// visibility restrictions that don't apply to actually starting an intent.
// openURL itself has neither restriction - it just fails to resolve (and
// the promise rejects) if nothing can handle the URL, which is exactly the
// "try the next one" signal this needs, with no manifest/plist wiring.
async function openFirstAvailable(urls: string[]): Promise<void> {
  for (const url of urls) {
    try {
      await Linking.openURL(url);
      return;
    } catch {
      // No app handled this URL - fall through to the next candidate. The
      // last entry is always the https web fallback, which every platform
      // can always open, so this loop never exhausts without opening
      // something.
    }
  }
}

export async function openTurnByTurnNavigation(lat: number, lng: number): Promise<void> {
  const destination = `${lat},${lng}`;

  if (Platform.OS === 'ios') {
    // Prefer Google Maps if the driver has it installed; Apple Maps ships
    // on every iOS device, so it's a safe next fallback before the web.
    await openFirstAvailable([
      `comgooglemaps://?daddr=${destination}&directionsmode=driving`,
      `maps://?daddr=${destination}&dirflg=d`,
      WEB_FALLBACK_URL(lat, lng),
    ]);
    return;
  }

  if (Platform.OS === 'android') {
    // google.navigation: drops the driver straight into turn-by-turn
    // navigation mode, not just a route preview - the Android intent
    // system resolves it via a chooser if more than one app can handle it.
    await openFirstAvailable([`google.navigation:q=${destination}&mode=d`, WEB_FALLBACK_URL(lat, lng)]);
    return;
  }

  await Linking.openURL(WEB_FALLBACK_URL(lat, lng));
}
