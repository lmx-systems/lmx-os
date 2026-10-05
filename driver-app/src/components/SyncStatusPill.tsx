import { useMemo } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { useOutboxPending } from '../offline/OutboxContext';
import { outboxManager } from '../offline/outboxManager';
import type { OutboxActionType } from '../offline/types';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

// What each queued action was, as the driver would say it.
const ACTION_NAME: Record<OutboxActionType, string> = {
  arrive: 'Your arrival',
  scan: 'Your parcel scan',
  complete: 'Completing this stop',
  flag: 'Your problem report',
  geofence: 'The automatic arrival',
  hub_geofence: 'The warehouse check-in',
  'collect-return': 'Collecting the return',
  'return-not-ready': 'Marking the return not ready',
  'return-to-shop': 'Returning it to the shop',
  survey: 'Your dock survey',
};

// Actions are now fire-and-forget-to-queue (see outboxManager), so the
// per-button loading spinner that used to cover "waiting on the network"
// no longer means anything - this pill is the replacement affordance that
// reassures a driver in a dead zone that their tap was recorded and will
// reach the server once connectivity returns.
//
// An action the server rejected is shown apart from that, with the server's
// reason, until the driver dismisses it. It won't be sent again, so "will
// retry" was untrue, and the stop has already gone back to what the server
// holds, which leaves the driver free to do it again properly.
export function SyncStatusPill({ stopId }: { stopId: string }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const pending = useOutboxPending(stopId);

  if (pending.length === 0) return null;

  const rejected = pending.filter((i) => i.permanentlyFailed);
  const sending = pending.filter((i) => !i.permanentlyFailed);
  const hasError = sending.some((i) => i.lastError !== null);

  return (
    <View style={styles.stack}>
      {rejected.map((item) => (
        <View key={item.id} style={styles.rejected}>
          <View style={styles.rejectedText}>
            <Text style={styles.rejectedTitle}>{ACTION_NAME[item.type]} wasn&apos;t accepted</Text>
            {item.lastError !== null && <Text style={styles.text}>{item.lastError}</Text>}
          </View>
          <Pressable
            onPress={() => outboxManager.dismiss(item.id)}
            accessibilityRole="button"
            hitSlop={8}
            style={({ pressed }) => [styles.dismiss, pressed && styles.pressed]}
          >
            <Text style={styles.dismissLabel}>Dismiss</Text>
          </Pressable>
        </View>
      ))}
      {sending.length > 0 && (
        <View style={[styles.pill, hasError && styles.pillWarning]}>
          {!hasError && <ActivityIndicator size="small" color={colors.textSecondary} />}
          <Text style={styles.text}>
            {hasError ? "Couldn't sync yet - will retry" : 'Saved - syncing…'}
          </Text>
        </View>
      )}
    </View>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    stack: { gap: spacing.sm, marginBottom: spacing.md },
    pill: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: spacing.sm,
      alignSelf: 'flex-start',
      paddingVertical: spacing.xs,
      paddingHorizontal: spacing.md,
      borderRadius: radius.lg,
      backgroundColor: colors.surfaceAlt,
    },
    pillWarning: { backgroundColor: colors.warningDim },
    rejected: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: spacing.md,
      paddingVertical: spacing.sm,
      paddingHorizontal: spacing.md,
      borderRadius: radius.md,
      backgroundColor: colors.dangerDim,
    },
    rejectedText: { flex: 1, gap: 2 },
    rejectedTitle: { ...typography.label, color: colors.textPrimary },
    text: { ...typography.small, color: colors.textSecondary },
    // Big enough for a gloved thumb: the app is built for gloves (see theme/tokens.ts).
    dismiss: {
      minHeight: 44,
      justifyContent: 'center',
      paddingHorizontal: spacing.md,
      borderRadius: radius.md,
      borderWidth: 1.5,
      borderColor: colors.borderStrong,
      backgroundColor: colors.surface,
    },
    pressed: { opacity: 0.85 },
    dismissLabel: { ...typography.label, color: colors.textPrimary },
  });
