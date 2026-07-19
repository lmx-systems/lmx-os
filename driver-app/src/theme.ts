/**
 * Minimal design tokens - matches the wireframes' mid-fi grayscale look
 * (black primary actions, light surfaces) rather than introducing new
 * brand color decisions nobody's signed off on yet.
 */
export const colors = {
  bg: '#f6f7f9',
  surface: '#ffffff',
  border: '#e2e5ea',
  borderStrong: '#cfd4dc',
  textPrimary: '#14171c',
  textSecondary: '#5b6472',
  textMuted: '#8b93a1',
  primary: '#14171c',
  primaryText: '#ffffff',
  accent: '#0c8599',
  danger: '#c62828',
  success: '#1f9254',
  warning: '#a15c07',
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
  md: 10,
  lg: 14,
};

export const typography = {
  title: { fontSize: 22, fontWeight: '700' as const, color: colors.textPrimary },
  subtitle: { fontSize: 15, color: colors.textSecondary },
  body: { fontSize: 15, color: colors.textPrimary },
  label: { fontSize: 13, fontWeight: '600' as const, color: colors.textSecondary },
  small: { fontSize: 12, color: colors.textMuted },
};
