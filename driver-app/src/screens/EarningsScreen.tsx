import { useEffect, useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { Earnings } from '../api/types';
import type { EarningsStackParamList } from '../navigation/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

type Props = NativeStackScreenProps<EarningsStackParamList, 'EarningsHome'>;

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// w2 drivers get a monthly period, 1099/gig weekly (app/payroll/hours.py)
// - the API doesn't send employment_type on EarningsView itself, so this
// infers the label from how wide the period actually is rather than a
// second round trip.
function periodLabel(periodStart: string, periodEnd: string): string {
  const days = (new Date(`${periodEnd}T00:00:00`).getTime() - new Date(`${periodStart}T00:00:00`).getTime()) / 86_400_000;
  return days > 20 ? 'This month' : 'This week';
}

// Screen 1n, "Earnings". Hours now come from the real online/offline/
// break log (app/models/driver_shift_event.py), not route timestamps,
// and overtime_hours applies the federal 40hr/week 1.5x rule for w2
// drivers - still explicitly labeled an estimate when hourly_rate_cents
// is a placeholder (docs/NEXT_STEPS.md): no real fare/price field exists
// for gig-style pay, and payroll integration (app/payroll/) isn't
// credentialed yet, so this is not what you'll actually be paid.
export function EarningsScreen({ navigation }: Props) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [earnings, setEarnings] = useState<Earnings | null>(null);

  useEffect(() => {
    (async () => {
      setEarnings(await api.getEarnings());
    })();
  }, []);

  if (!earnings) {
    return null;
  }

  return (
    <ScreenContainer>
      {earnings.is_placeholder && (
        <Card style={styles.placeholderBanner}>
          <Text style={styles.placeholderTitle}>Estimate, not final pay</Text>
          <Text style={styles.noteText}>{earnings.note}</Text>
        </Card>
      )}

      <Text style={styles.weekLabel}>
        {periodLabel(earnings.period_start, earnings.period_end)} · {formatDate(earnings.period_start)} - {formatDate(earnings.period_end)}
      </Text>
      <Text style={styles.bigNumber}>{formatCents(earnings.estimated_pay_cents)}</Text>

      <View style={styles.statsRow}>
        <Card style={styles.statCard}>
          <Text style={styles.statLabel}>Hours worked</Text>
          <Text style={styles.statValue}>{earnings.hours_worked.toFixed(1)}</Text>
        </Card>
        <Card style={styles.statCard}>
          <Text style={styles.statLabel}>Rate{earnings.is_placeholder ? ' (placeholder)' : ''}</Text>
          <Text style={styles.statValue}>{formatCents(earnings.hourly_rate_cents)}/hr</Text>
        </Card>
      </View>

      {earnings.overtime_hours > 0 && (
        <Card style={styles.overtimeCard}>
          <Text style={styles.statLabel}>Includes overtime</Text>
          <Text style={styles.noteText}>
            {earnings.overtime_hours.toFixed(1)} hour{earnings.overtime_hours === 1 ? '' : 's'} over 40/week, paid at 1.5x
          </Text>
        </Card>
      )}

      <Button label="View trip history" variant="outline" onPress={() => navigation.navigate('TripHistory')} />
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    placeholderBanner: { backgroundColor: colors.warningDim, borderColor: colors.warning, marginBottom: spacing.lg, gap: spacing.xs },
    placeholderTitle: { fontSize: 14, fontWeight: '700', color: colors.warning },
    noteText: { ...typography.small, color: colors.textMuted },
    weekLabel: { ...typography.label, color: colors.textPrimary },
    bigNumber: { fontSize: 40, fontWeight: '700', color: colors.textPrimary, marginTop: spacing.xs, marginBottom: spacing.lg },
    statsRow: { flexDirection: 'row', gap: spacing.md, marginBottom: spacing.lg },
    overtimeCard: { backgroundColor: colors.accentDim, marginBottom: spacing.lg, gap: spacing.xs },
    statCard: { flex: 1 },
    statLabel: { ...typography.label, color: colors.textPrimary },
    statValue: { fontSize: 20, fontWeight: '700', color: colors.textPrimary, marginTop: spacing.xs },
  });
