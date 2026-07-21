import * as SecureStore from 'expo-secure-store';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useColorScheme } from 'react-native';

import { palette } from './tokens';
import type { ColorScheme } from './tokens';

// Same expo-secure-store already used by AuthContext for the driver token -
// no reason to pull in a second storage library for this one preference.
const PREFERENCE_STORAGE_KEY = 'lmx_theme_preference';

export type ThemeMode = 'light' | 'dark';
export type ThemePreference = ThemeMode | 'system';

interface ThemeContextValue {
  mode: ThemeMode;
  preference: ThemePreference;
  colors: ColorScheme;
  setPreference: (preference: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Default to 'system', not a resolved 'light'/'dark' snapshot - RN's
  // useColorScheme() already returns the live value synchronously (no
  // flash to guard against), and locking in a concrete value here would
  // freeze the app's theme at whatever the OS setting was on first mount,
  // never following a later OS appearance change since there's no in-app
  // toggle (unlike the web apps) that could ever set 'system' again.
  const systemScheme = useColorScheme();
  const [preference, setPreferenceState] = useState<ThemePreference>('system');

  useEffect(() => {
    (async () => {
      const stored = await SecureStore.getItemAsync(PREFERENCE_STORAGE_KEY);
      if (stored === 'light' || stored === 'dark' || stored === 'system') {
        setPreferenceState(stored);
      }
    })();
  }, []);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    SecureStore.setItemAsync(PREFERENCE_STORAGE_KEY, next).catch(() => {});
  }, []);

  const mode: ThemeMode = preference === 'system' ? (systemScheme === 'dark' ? 'dark' : 'light') : preference;

  const value = useMemo<ThemeContextValue>(
    () => ({ mode, preference, colors: palette[mode], setPreference }),
    [mode, preference, setPreference],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return ctx;
}

export function useThemeColors(): ColorScheme {
  return useTheme().colors;
}
