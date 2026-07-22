import * as LocalAuthentication from 'expo-local-authentication';
import * as SecureStore from 'expo-secure-store';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { api, setAuthToken } from '../api/client';
import type { DriverProfile } from '../api/types';
import { registerForPushNotifications } from '../notifications/registerForPushNotifications';

// SecureStore (Keychain on iOS, EncryptedSharedPreferences on Android), not
// AsyncStorage - this token is a long-lived bearer credential for a real
// driver session and shouldn't sit in plain, unencrypted app-sandbox storage.
const TOKEN_STORAGE_KEY = 'lmx_driver_token';

interface AuthContextValue {
  isLoading: boolean;
  isSignedIn: boolean;
  // True when a stored session exists but biometric unlock failed/was
  // cancelled - distinct from "signed out": the token is still there,
  // RootNavigator shows BiometricLockScreen instead of the sign-in flow.
  needsBiometricRetry: boolean;
  profile: DriverProfile | null;
  signIn: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
  refreshProfile: () => Promise<DriverProfile | null>;
  setProfile: (profile: DriverProfile) => void;
  retryBiometric: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// Gates access to the SecureStore-persisted token, doesn't replace it - a
// device with no biometric hardware/enrollment (older phone, disabled in
// settings) must never be locked out, since that's the same trust
// boundary this app already relied on before this feature existed.
async function tryBiometricUnlock(): Promise<boolean> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync();
  const isEnrolled = await LocalAuthentication.isEnrolledAsync();
  if (!hasHardware || !isEnrolled) return true;

  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: 'Unlock LMX Driver',
    disableDeviceFallback: false, // OS passcode is an acceptable fallback within the prompt itself
  });
  return result.success;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [isSignedIn, setIsSignedIn] = useState(false);
  const [needsBiometricRetry, setNeedsBiometricRetry] = useState(false);
  const [profile, setProfileState] = useState<DriverProfile | null>(null);
  const [pendingToken, setPendingToken] = useState<string | null>(null);

  const completeSignIn = useCallback(async (token: string) => {
    setAuthToken(token);
    const fetchedProfile = await api.getMyProfile();
    setProfileState(fetchedProfile);
    setIsSignedIn(true);
    // Slides the session forward and keeps this device's last_seen_at
    // fresh (app/api/driver_routes.py's /auth/refresh) - best-effort, a
    // failure here shouldn't block sign-in.
    api.refreshToken().catch(() => {});
    // Registers this device for job-offer push notifications
    // (docs/ROADMAP.md A1) - also best-effort; no-ops entirely on a
    // build with no EAS project id configured yet, see that module.
    registerForPushNotifications().catch(() => {});
  }, []);

  useEffect(() => {
    (async () => {
      const stored = await SecureStore.getItemAsync(TOKEN_STORAGE_KEY);
      if (stored) {
        const unlocked = await tryBiometricUnlock();
        if (!unlocked) {
          setPendingToken(stored);
          setNeedsBiometricRetry(true);
          setIsLoading(false);
          return;
        }
        try {
          await completeSignIn(stored);
        } catch {
          // Stored token is stale/invalid - fall through to signed-out.
          await SecureStore.deleteItemAsync(TOKEN_STORAGE_KEY);
          setAuthToken(null);
        }
      }
      setIsLoading(false);
    })();
  }, [completeSignIn]);

  const retryBiometric = useCallback(async () => {
    if (!pendingToken) return;
    const unlocked = await tryBiometricUnlock();
    if (!unlocked) return;
    const token = pendingToken;
    setPendingToken(null);
    setNeedsBiometricRetry(false);
    try {
      await completeSignIn(token);
    } catch {
      await SecureStore.deleteItemAsync(TOKEN_STORAGE_KEY);
      setAuthToken(null);
    }
  }, [pendingToken, completeSignIn]);

  const signIn = useCallback(
    async (token: string) => {
      await SecureStore.setItemAsync(TOKEN_STORAGE_KEY, token);
      await completeSignIn(token);
    },
    [completeSignIn],
  );

  const signOut = useCallback(async () => {
    await SecureStore.deleteItemAsync(TOKEN_STORAGE_KEY);
    setAuthToken(null);
    setProfileState(null);
    setIsSignedIn(false);
    setNeedsBiometricRetry(false);
    setPendingToken(null);
  }, []);

  const refreshProfile = useCallback(async () => {
    const fetchedProfile = await api.getMyProfile();
    setProfileState(fetchedProfile);
    return fetchedProfile;
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      isLoading,
      isSignedIn,
      needsBiometricRetry,
      profile,
      signIn,
      signOut,
      refreshProfile,
      setProfile: setProfileState,
      retryBiometric,
    }),
    [isLoading, isSignedIn, needsBiometricRetry, profile, signIn, signOut, refreshProfile, retryBiometric],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
