/**
 * Design tokens - light/dark palettes. Primary/accent is a utilitarian
 * green (not the two web apps' indigo) per the "LMX Driver App Wireframes"
 * spec (docs/LMX Driver App Wireframes.pdf) - a field-use app read
 * differently than an office console: high-contrast, one clear "go"
 * color for actions and success states alike, built for sunlight/gloves
 * rather than a branded consumer look. Typography is font-only (no
 * baked-in color) since text color has to vary with the active scheme -
 * callers apply color from the active palette at the point of use.
 */
export interface ColorScheme {
  bg: string;
  surface: string;
  surfaceAlt: string;
  border: string;
  borderStrong: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  primary: string;
  primaryHover: string;
  primaryText: string;
  accent: string;
  accentDim: string;
  danger: string;
  dangerDim: string;
  success: string;
  successDim: string;
  warning: string;
  warningDim: string;
  premium: string;
  premiumDim: string;
  overlay: string;
}

export const palette: { light: ColorScheme; dark: ColorScheme } = {
  light: {
    bg: '#f6f7f9',
    surface: '#ffffff',
    surfaceAlt: '#eef0f3',
    border: '#e2e5ea',
    borderStrong: '#cfd4dc',
    textPrimary: '#14171c',
    textSecondary: '#5b6472',
    textMuted: '#8b93a1',
    primary: '#16a34a',
    primaryHover: '#15803d',
    primaryText: '#ffffff',
    accent: '#16a34a',
    accentDim: '#dcfce7',
    danger: '#c62828',
    dangerDim: '#fbe9ea',
    success: '#16a34a',
    successDim: '#dcfce7',
    warning: '#a15c07',
    warningDim: '#fdf1de',
    premium: '#7c3aed',
    premiumDim: '#f1e8fd',
    overlay: 'rgba(20, 23, 28, 0.45)',
  },
  dark: {
    bg: '#101317',
    surface: '#1a1e24',
    surfaceAlt: '#20242c',
    border: '#2d323c',
    borderStrong: '#3a4150',
    textPrimary: '#f5f6f8',
    textSecondary: '#a0a8b5',
    textMuted: '#7b8494',
    primary: '#22c55e',
    primaryHover: '#4ade80',
    primaryText: '#ffffff',
    accent: '#22c55e',
    accentDim: '#153824',
    danger: '#f87171',
    dangerDim: '#3d1414',
    success: '#22c55e',
    successDim: '#153824',
    warning: '#fbbf24',
    warningDim: '#3a2a08',
    premium: '#a78bfa',
    premiumDim: '#2c2350',
    overlay: 'rgba(0, 0, 0, 0.6)',
  },
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
};

export const radius = {
  sm: 6,
  md: 10,
  lg: 14,
};

export const typography = {
  title: { fontFamily: 'Inter_700Bold', fontWeight: '700' as const, fontSize: 22 },
  subtitle: { fontFamily: 'Inter_400Regular', fontSize: 15 },
  body: { fontFamily: 'Inter_400Regular', fontSize: 15 },
  label: { fontFamily: 'Inter_600SemiBold', fontWeight: '600' as const, fontSize: 13 },
  small: { fontFamily: 'Inter_400Regular', fontSize: 12 },
};

/** RN elevation needs shadowOpacity tuned per scheme - dark surfaces need a stronger shadow to read. */
export function elevation(scheme: 'light' | 'dark') {
  const isDark = scheme === 'dark';
  return {
    sm: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 1 },
      shadowOpacity: isDark ? 0.3 : 0.06,
      shadowRadius: 2,
      elevation: 1,
    },
    md: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: isDark ? 0.35 : 0.08,
      shadowRadius: 8,
      elevation: 3,
    },
  };
}
