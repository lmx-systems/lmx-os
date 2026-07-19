import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { api, setAuthToken } from '../api/client';
import type { DriverProfile } from '../api/types';

const TOKEN_STORAGE_KEY = 'lmx_driver_token';

interface AuthContextValue {
  isLoading: boolean;
  isSignedIn: boolean;
  profile: DriverProfile | null;
  signIn: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
  refreshProfile: () => Promise<DriverProfile | null>;
  setProfile: (profile: DriverProfile) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [isSignedIn, setIsSignedIn] = useState(false);
  const [profile, setProfileState] = useState<DriverProfile | null>(null);

  useEffect(() => {
    (async () => {
      const stored = await AsyncStorage.getItem(TOKEN_STORAGE_KEY);
      if (stored) {
        setAuthToken(stored);
        try {
          const fetchedProfile = await api.getMyProfile();
          setProfileState(fetchedProfile);
          setIsSignedIn(true);
        } catch {
          // Stored token is stale/invalid - fall through to signed-out.
          await AsyncStorage.removeItem(TOKEN_STORAGE_KEY);
          setAuthToken(null);
        }
      }
      setIsLoading(false);
    })();
  }, []);

  const signIn = useCallback(async (token: string) => {
    await AsyncStorage.setItem(TOKEN_STORAGE_KEY, token);
    setAuthToken(token);
    const fetchedProfile = await api.getMyProfile();
    setProfileState(fetchedProfile);
    setIsSignedIn(true);
  }, []);

  const signOut = useCallback(async () => {
    await AsyncStorage.removeItem(TOKEN_STORAGE_KEY);
    setAuthToken(null);
    setProfileState(null);
    setIsSignedIn(false);
  }, []);

  const refreshProfile = useCallback(async () => {
    const fetchedProfile = await api.getMyProfile();
    setProfileState(fetchedProfile);
    return fetchedProfile;
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ isLoading, isSignedIn, profile, signIn, signOut, refreshProfile, setProfile: setProfileState }),
    [isLoading, isSignedIn, profile, signIn, signOut, refreshProfile],
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
