import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import { api } from '../api/client';
import { getOrCreateDeviceId } from '../auth/deviceId';

// Notifications while the app is foregrounded still show a banner/sound -
// without this handler Expo's default is to suppress them, which would
// make testing "did the push actually arrive" while developing look like
// nothing happened even when it did.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

// Real push notifications for new job offers (docs/ROADMAP.md A1) need an
// EAS project id (driver-app/app.json's extra.eas.projectId) for
// Notifications.getExpoPushTokenAsync() to mint a real, routable token -
// that's a one-time `eas init` this codebase hasn't run yet (A6, the
// app-store deployment pipeline, is a separate unstarted item). Until
// then this deliberately no-ops rather than throwing, same
// "unconfigured -> skip, don't crash" convention as every other
// not-yet-provisioned external credential in this app.
function getEasProjectId(): string | undefined {
  return Constants.expoConfig?.extra?.eas?.projectId as string | undefined;
}

// Called once per app launch after sign-in (AuthContext.tsx's
// completeSignIn) - best-effort, matching how refreshToken() is already
// called there: a failure here should never block sign-in.
export async function registerForPushNotifications(): Promise<void> {
  const projectId = getEasProjectId();
  if (!projectId) {
    return;
  }

  // Push tokens are meaningless on a simulator/emulator - Device.isDevice
  // is Expo's own recommended check before ever calling getExpoPushTokenAsync.
  if (!Device.isDevice) {
    return;
  }

  const { status: existingStatus } = await Notifications.getPermissionsAsync();
  let status = existingStatus;
  if (status !== 'granted') {
    const requested = await Notifications.requestPermissionsAsync();
    status = requested.status;
  }
  if (status !== 'granted') {
    return;
  }

  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('default', {
      name: 'default',
      importance: Notifications.AndroidImportance.DEFAULT,
    });
  }

  const { data: expoPushToken } = await Notifications.getExpoPushTokenAsync({ projectId });
  const deviceId = await getOrCreateDeviceId();
  await api.registerPushToken(deviceId, expoPushToken);
}
