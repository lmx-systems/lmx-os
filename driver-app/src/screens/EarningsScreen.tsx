import { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { Earnings } from '../api/types';
import type { EarningsStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<EarningsStackParamList, 'EarningsHome'>;

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// Screen 1n, "Earnings". Everything here is explicitly labeled an
// estimate - there's no real fare/price field anywhere in Order/Route/
// Stop to compute a true payout from yet, and no payroll integration
// wired up (docs/NEXT_STEPS.md item 14). This shows real data (hours
// worked, computed from actual route timestamps) run through a
// placeholder hourly rate, not a fabricated number - but it is not what
// you'll actually be paid.
export function EarningsScreen({ navigation }: Props) {
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
          <Text style={typography.small}>{earnings.note}</Text>
        </Card>
      )}

      <Text style={typography.label}>
        This week · {formatDate(earnings.period_start)} - {formatDate(earnings.period_end)}
      </Text>
      <Text style={styles.bigNumber}>{formatCents(earnings.estimated_pay_cents)}</Text>

      <View style={styles.statsRow}>
        <Card style={styles.statCard}>
          <Text style={typography.label}>Hours worked (est.)</Text>
          <Text style={styles.statValue}>{earnings.hours_worked.toFixed(1)}</Text>
        </Card>
        <Card style={styles.statCard}>
          <Text style={typography.label}>Rate (placeholder)</Text>
          <Text style={styles.statValue}>{formatCents(earnings.hourly_rate_cents)}/hr</Text>
        </Card>
      </View>

      <Button label="View trip history" variant="outline" onPress={() => navigation.navigate('TripHistory')} />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  placeholderBanner: { backgroundColor: '#fff8e6', borderColor: colors.warning, marginBottom: spacing.lg, gap: spacing.xs },
  placeholderTitle: { fontSize: 14, fontWeight: '700', color: colors.warning },
  bigNumber: { fontSize: 40, fontWeight: '700', color: colors.textPrimary, marginTop: spacing.xs, marginBottom: spacing.lg },
  statsRow: { flexDirection: 'row', gap: spacing.md, marginBottom: spacing.lg },
  statCard: { flex: 1 },
  statValue: { fontSize: 20, fontWeight: '700', color: colors.textPrimary, marginTop: spacing.xs },
});
