import { useMemo } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { useOutboxPending } from '../offline/OutboxContext';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

// Actions are now fire-and-forget-to-queue (see outboxManager), so the
// per-button loading spinner that used to cover "waiting on the network"
// no longer means anything - this pill is the replacement affordance that
// reassures a driver in a dead zone that their tap was recorded and will
// reach the server once connectivity returns.
export function SyncStatusPill({ stopId }: { stopId: string }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const pending = useOutboxPending(stopId);

  if (pending.length === 0) return null;

  const hasError = pending.some((i) => i.lastError !== null);

  return (
    <View style={[styles.pill, hasError && styles.pillWarning]}>
      {!hasError && <ActivityIndicator size="small" color={colors.textSecondary} />}
      <Text style={styles.text}>
        {hasError ? "Couldn't sync yet - will retry" : 'Saved - syncing…'}
      </Text>
    </View>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    pill: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: spacing.sm,
      alignSelf: 'flex-start',
      paddingVertical: spacing.xs,
      paddingHorizontal: spacing.md,
      borderRadius: radius.lg,
      backgroundColor: colors.surfaceAlt,
      marginBottom: spacing.md,
    },
    pillWarning: { backgroundColor: colors.warningDim },
    text: { ...typography.small, color: colors.textSecondary },
  });
