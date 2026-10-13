import { createContext, useContext } from 'react';
import type { ReactNode } from 'react';

import { palette } from './tokens';
import type { ColorScheme } from './tokens';

// Light only (docs/DESIGN_SYSTEM.md). The provider stays so every screen keeps
// reading colours through useThemeColors() - one place to change them - rather
// than importing the palette directly.
export type ThemeMode = 'light';

interface ThemeContextValue {
  mode: ThemeMode;
  colors: ColorScheme;
}

const VALUE: ThemeContextValue = { mode: 'light', colors: palette.light };

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  return <ThemeContext.Provider value={VALUE}>{children}</ThemeContext.Provider>;
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
