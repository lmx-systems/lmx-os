/**
 * Design tokens - the LMX design system (docs/DESIGN_SYSTEM.md), the same
 * values by the same meaning as dashboard/src/index.css and
 * client-portal/src/index.css: a soft grey ground, white tiles, ink for
 * anything you press, lime for the one number that matters, peach for what
 * needs a person.
 *
 * Light only. The app is used outdoors, often in sunlight; the wireframes'
 * brief ("LMX Driver App Wireframes", built for sunlight and gloves) is best
 * served by dark ink on a light ground at high contrast, and one look is one
 * thing to test. `primary` and `accent` are ink; `success` is a real green,
 * no longer the same value as the action colour, so "done" and "press here"
 * can be told apart. Typography is font-only (no baked-in color) - callers
 * apply color from the palette at the point of use.
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
  lime: string;
  limeStrong: string;
  limeInk: string;
  needs: string;
  needsDim: string;
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

export const palette: { light: ColorScheme } = {
  light: {
    bg: '#eeeff1',
    surface: '#ffffff',
    surfaceAlt: '#f6f7f8',
    border: '#e6e7ea',
    borderStrong: '#d3d5da',
    textPrimary: '#15161a',
    textSecondary: '#4b4e57',
    textMuted: '#6b6e78',
    primary: '#15161a',
    primaryHover: '#000000',
    primaryText: '#ffffff',
    accent: '#15161a',
    accentDim: '#e6f2c0',
    lime: '#cdeb6b',
    limeStrong: '#afcb52',
    limeInk: '#2e3a0c',
    needs: '#a8330a',
    needsDim: '#ffe4d4',
    danger: '#b42318',
    dangerDim: '#fde8e6',
    success: '#1f7a4d',
    successDim: '#e3f3e9',
    warning: '#8a5300',
    warningDim: '#fff1c7',
    premium: '#6d3fd8',
    premiumDim: '#efe8fd',
    overlay: 'rgba(21, 22, 26, 0.4)',
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

// sm: inputs and small boxes; md: wells and rows; lg: tiles; pill: buttons, chips, badges.
export const radius = {
  sm: 12,
  md: 18,
  lg: 24,
  pill: 999,
};

// Weight comes from the family, never from fontWeight: expo-font registers each
// file as a regular face, and on Android a fontWeight of 700+ asks for a bold
// variant that isn't there and falls back to Roboto Bold. Pick the family
// (PlusJakartaSans_700Bold, _800ExtraBold) instead of setting a weight.
export const typography = {
  // The console's scale, sized for a phone (docs/DESIGN_SYSTEM.md "Type"): a page
  // title, a card headline, a tile title, a row title, quiet meta, and big numbers.
  display: { fontFamily: 'PlusJakartaSans_800ExtraBold', fontSize: 30, letterSpacing: -1.3, lineHeight: 34 },
  headline: { fontFamily: 'PlusJakartaSans_800ExtraBold', fontSize: 22, letterSpacing: -0.7, lineHeight: 27 },
  tileTitle: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 16, letterSpacing: -0.2 },
  rowTitle: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 15.5 },
  meta: { fontFamily: 'PlusJakartaSans_600SemiBold', fontSize: 12.5 },
  number: { fontFamily: 'PlusJakartaSans_800ExtraBold', fontSize: 30, letterSpacing: -1.2 },
  hero: { fontFamily: 'PlusJakartaSans_800ExtraBold', fontSize: 56, letterSpacing: -3, lineHeight: 60 },
  title: { fontFamily: 'PlusJakartaSans_800ExtraBold', fontSize: 26, letterSpacing: -0.8 },
  subtitle: { fontFamily: 'PlusJakartaSans_400Regular', fontSize: 15 },
  body: { fontFamily: 'PlusJakartaSans_400Regular', fontSize: 15 },
  label: { fontFamily: 'PlusJakartaSans_600SemiBold', fontSize: 13 },
  small: { fontFamily: 'PlusJakartaSans_400Regular', fontSize: 12 },
};

/** Tiles sit on the grey ground without shadows; kept for the odd floating element (a sheet, a banner). */
export function elevation() {
  return {
    sm: {
      shadowColor: '#15161a',
      shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.05,
      shadowRadius: 2,
      elevation: 1,
    },
    md: {
      shadowColor: '#15161a',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.08,
      shadowRadius: 14,
      elevation: 3,
    },
  };
}
