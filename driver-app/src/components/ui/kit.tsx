/**
 * The LMX design system's building blocks, for the phone (docs/DESIGN_SYSTEM.md
 * "Components"). Each mirrors one the console and portal use, by the same name
 * where there is one: the brand bar, a kind pill, fact wells, progress segments,
 * the step track (the portal's DeliveryTrack, the console's rule Steps), rows
 * with an icon tile (the console's decision rows), and tinted banners.
 */
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import type { LucideIcon } from 'lucide-react-native';
import { Check, ChevronRight } from 'lucide-react-native';

import { radius, spacing, typography, useThemeColors } from '../../theme';
import type { ColorScheme } from '../../theme';

export type Tone = 'neutral' | 'needs' | 'lime' | 'ink' | 'success' | 'warning' | 'danger';

function toneColors(colors: ColorScheme, tone: Tone): { bg: string; fg: string } {
  switch (tone) {
    case 'needs':
      return { bg: colors.needsDim, fg: colors.needs };
    case 'lime':
      return { bg: colors.lime, fg: colors.textPrimary };
    case 'ink':
      return { bg: colors.primary, fg: colors.primaryText };
    case 'success':
      return { bg: colors.successDim, fg: colors.success };
    case 'warning':
      return { bg: colors.warningDim, fg: colors.warning };
    case 'danger':
      return { bg: colors.dangerDim, fg: colors.danger };
    default:
      return { bg: colors.surfaceAlt, fg: colors.textPrimary };
  }
}

/** The lmx mark and wordmark, as on the console and portal. */
export function BrandBar({ right }: { right?: ReactNode }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  return (
    <View style={styles.brandBar}>
      <View style={styles.brand}>
        {/* The same mark as the console's and portal's top bar. */}
        <Image source={require('../../../assets/lmx-mark.png')} style={styles.mark} accessibilityIgnoresInvertColors />
        <Text style={styles.wordmark}>lmx</Text>
      </View>
      {right}
    </View>
  );
}

/** A short uppercase label on a tinted pill: what kind of thing a card is. */
export function KindPill({ label, tone = 'neutral' }: { label: string; tone?: Tone }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const t = toneColors(colors, tone);
  return (
    <View style={[styles.kindPill, { backgroundColor: t.bg }]}>
      <Text style={[styles.kindPillText, { color: t.fg }]}>{label.toUpperCase()}</Text>
    </View>
  );
}

/** Two to three facts side by side, each a small grey well: label above, number below. */
export function Facts({ items }: { items: { k: string; v: string }[] }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  return (
    <View style={styles.facts}>
      {items.map((f) => (
        <View key={f.k} style={styles.fact}>
          <Text style={styles.factK} numberOfLines={1}>
            {f.k}
          </Text>
          <Text style={styles.factV} numberOfLines={1} adjustsFontSizeToFit>
            {f.v}
          </Text>
        </View>
      ))}
    </View>
  );
}

export type SegmentState = 'done' | 'current' | 'ahead';

/** One segment per stop, each from its own state: done in ink, the current one lime, the rest grey. */
export function Segments({ states }: { states: SegmentState[] }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const done = states.filter((st) => st === 'done').length;
  return (
    <View style={styles.segments} accessible accessibilityLabel={`${done} of ${states.length} stops done`}>
      {states.map((st, i) => (
        <View
          key={i}
          style={[
            styles.segment,
            { backgroundColor: st === 'done' ? colors.primary : st === 'current' ? colors.limeStrong : colors.borderStrong },
          ]}
        />
      ))}
    </View>
  );
}

/**
 * Steps along a pill track: passed in lime with a check, the current one in ink,
 * the rest outlined. The portal's delivery track and the console's rule steps.
 */
export function Steps({ labels, current }: { labels: string[]; current: number }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  return (
    <View style={styles.steps}>
      {labels.map((label, i) => {
        const passed = i < current;
        const now = i === current;
        return (
          <View
            key={label}
            style={[styles.step, passed && styles.stepPassed, now && styles.stepNow]}
            accessible
            accessibilityLabel={`${label}${passed ? ', done' : now ? ', now' : ''}`}
          >
            {passed && <Check size={13} color={colors.textPrimary} strokeWidth={3} />}
            <Text style={[styles.stepText, now && styles.stepTextNow]} numberOfLines={1}>
              {label}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

/** A 44px rounded square holding an icon: peach when it needs a person, grey otherwise. */
export function IconTile({ icon: Icon, tone = 'neutral', size = 44 }: { icon: LucideIcon; tone?: Tone; size?: number }) {
  const colors = useThemeColors();
  const t = toneColors(colors, tone);
  return (
    <View
      style={{
        width: size,
        height: size,
        borderRadius: size * 0.32,
        backgroundColor: t.bg,
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Icon size={size * 0.45} color={tone === 'neutral' ? colors.textPrimary : t.fg} strokeWidth={2.2} />
    </View>
  );
}

interface RowProps {
  icon?: LucideIcon;
  tone?: Tone;
  title: string;
  detail?: string | null;
  right?: ReactNode;
  onPress?: () => void;
  selected?: boolean;
  /** 'tile' sits on the grey ground; 'well' sits inside a white tile. */
  surface?: 'tile' | 'well';
  accessibilityLabel?: string;
}

/** The console's decision row, for the phone: icon tile, bold title, one quiet line, then a chevron or an action. */
export function Row({ icon, tone, title, detail, right, onPress, selected, surface = 'tile', accessibilityLabel }: RowProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const body = (
    <>
      {icon && <IconTile icon={icon} tone={tone} />}
      <View style={styles.rowText}>
        <Text style={styles.rowTitle}>{title}</Text>
        {detail ? <Text style={styles.rowDetail}>{detail}</Text> : null}
      </View>
      {right ?? (onPress ? <ChevronRight size={20} color={colors.textMuted} /> : null)}
    </>
  );
  const style = [styles.row, surface === 'well' && styles.rowWell, selected && styles.rowSelected];
  if (!onPress) return <View style={style}>{body}</View>;
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      // The detail line is part of what a row says ("Documents, 2 need attention").
      accessibilityLabel={accessibilityLabel ?? (detail ? `${title}, ${detail}` : title)}
      accessibilityState={selected !== undefined ? { selected } : undefined}
      style={({ pressed }) => [...style, pressed && styles.pressed]}
    >
      {body}
    </Pressable>
  );
}

/** A small label above a group of tiles. */
export function SectionLabel({ children }: { children: string }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  return <Text style={styles.sectionLabel}>{children}</Text>;
}

/** A tinted tile with an icon and a word: status colour never alone. */
export function Banner({
  icon,
  tone,
  title,
  body,
  children,
}: {
  icon: LucideIcon;
  tone: Tone;
  title: string;
  body?: string | null;
  children?: ReactNode;
}) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const t = toneColors(colors, tone);
  const Icon = icon;
  return (
    // Neutral sits on the grey ground, so it is a white tile rather than a grey well.
    <View style={[styles.banner, { backgroundColor: tone === 'neutral' ? colors.surface : t.bg }]}>
      <Icon size={20} color={t.fg} strokeWidth={2.2} style={styles.bannerIcon} />
      <View style={styles.bannerText}>
        <Text style={[styles.bannerTitle, { color: t.fg }]}>{title}</Text>
        {body ? <Text style={[styles.bannerBody, { color: tone === 'lime' ? colors.limeInk : t.fg }]}>{body}</Text> : null}
        {children}
      </View>
    </View>
  );
}

/** Small white pill buttons in a row (Navigate, Call, Text): secondary actions. */
export function PillAction({ label, icon: Icon, onPress }: { label: string; icon?: LucideIcon; onPress: () => void }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      style={({ pressed }) => [styles.pillAction, pressed && styles.pressed]}
    >
      {Icon && <Icon size={17} color={colors.textPrimary} strokeWidth={2.2} />}
      <Text style={styles.pillActionText}>{label}</Text>
    </Pressable>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    brandBar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: 32 },
    brand: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
    mark: { width: 26, height: 26, borderRadius: 8 },
    wordmark: { fontFamily: 'PlusJakartaSans_800ExtraBold', fontSize: 19, letterSpacing: -0.8, color: colors.textPrimary },

    kindPill: { alignSelf: 'flex-start', borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 4 },
    kindPillText: { fontFamily: 'PlusJakartaSans_800ExtraBold', fontSize: 11, letterSpacing: 0.9 },

    facts: { flexDirection: 'row', gap: 6 },
    fact: { flex: 1, minWidth: 0, backgroundColor: colors.surfaceAlt, borderRadius: 14, padding: 10, gap: 2 },
    factK: { ...typography.small, fontSize: 11, color: colors.textMuted },
    factV: { fontFamily: 'PlusJakartaSans_800ExtraBold', fontSize: 16, letterSpacing: -0.3, color: colors.textPrimary },

    segments: { flexDirection: 'row', gap: 4 },
    segment: { flex: 1, height: 6, borderRadius: 3 },

    steps: { flexDirection: 'row', gap: 6 },
    step: {
      flex: 1,
      minHeight: 34,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 4,
      borderRadius: radius.pill,
      borderWidth: 1.5,
      borderColor: colors.borderStrong,
      backgroundColor: colors.surface,
      paddingHorizontal: 6,
    },
    stepPassed: { backgroundColor: colors.lime, borderColor: colors.lime },
    stepNow: { backgroundColor: colors.primary, borderColor: colors.primary },
    stepText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 12.5, color: colors.textPrimary },
    stepTextNow: { color: colors.primaryText },

    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: spacing.md,
      backgroundColor: colors.surface,
      borderRadius: 20,
      padding: spacing.md,
      minHeight: 68,
      borderWidth: 1.5,
      borderColor: 'transparent',
    },
    rowWell: { backgroundColor: colors.surfaceAlt },
    rowSelected: { borderColor: colors.primary },
    rowText: { flex: 1, minWidth: 0, gap: 2 },
    rowTitle: { ...typography.rowTitle, color: colors.textPrimary },
    rowDetail: { ...typography.small, fontSize: 12.5, color: colors.textSecondary },
    pressed: { opacity: 0.85 },

    sectionLabel: { ...typography.meta, color: colors.textMuted, marginTop: spacing.sm, marginBottom: 2, marginLeft: 4 },

    banner: { flexDirection: 'row', gap: spacing.md, borderRadius: 20, padding: spacing.lg, marginBottom: spacing.md },
    bannerIcon: { marginTop: 1 },
    bannerText: { flex: 1, gap: 2 },
    bannerTitle: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 14.5 },
    bannerBody: { ...typography.small, fontSize: 12.5, lineHeight: 17 },

    pillAction: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      minHeight: 44,
      paddingHorizontal: spacing.lg,
      borderRadius: radius.pill,
      backgroundColor: colors.surface,
    },
    pillActionText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 14, color: colors.textPrimary },
  });
