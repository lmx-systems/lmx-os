import { useEffect, useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import { Banner, Facts, Row } from '../components/ui/kit';
import { BarChart3, Gift, History, Info } from 'lucide-react-native';
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

  const facts = [
    { k: earnings.employment_type === 'gig' ? 'Hours online' : 'Hours worked', v: earnings.hours_worked.toFixed(1) },
    earnings.employment_type === 'gig'
      ? { k: 'Pay model', v: 'Per delivery' }
      : { k: `Rate${earnings.is_placeholder ? ' (placeholder)' : ''}`, v: `${formatCents(earnings.hourly_rate_cents)}/hr` },
  ];
  if (earnings.overtime_hours > 0) facts.push({ k: 'Overtime', v: `${earnings.overtime_hours.toFixed(1)} h` });

  return (
    <ScreenContainer>
      {/* The page's one lime tile, as on the console's Home: the number that matters here. */}
      <View style={styles.headline}>
        <Text style={styles.headlineLabel}>{periodLabel(earnings.period_start, earnings.period_end)}</Text>
        <Text style={styles.bigNumber} numberOfLines={1} adjustsFontSizeToFit accessibilityLabel={`Estimated pay ${formatCents(earnings.estimated_pay_cents)}`}>
          {formatCents(earnings.estimated_pay_cents)}
        </Text>
        <Text style={styles.headlineMeta}>
          {formatDate(earnings.period_start)} – {formatDate(earnings.period_end)}
          {earnings.is_placeholder ? ' · an estimate' : ''}
        </Text>
      </View>

      <Card style={styles.factsCard}>
        <Facts items={facts} />
        {earnings.overtime_hours > 0 && <Text style={styles.noteText}>Overtime is hours over 40 a week, paid at 1.5x.</Text>}
      </Card>

      {(earnings.bonuses_owed_cents ?? 0) > 0 && (
        // DEC-10: bonuses on offers taken after an order was turned down.
        // Recorded as owed; nothing pays per job yet, so they are not in the
        // estimate above.
        <Row
          icon={Gift}
          tone="lime"
          title={`${formatCents(earnings.bonuses_owed_cents ?? 0)} in bonuses owed`}
          detail="For deliveries you took after others turned them down. Not in the estimate above."
        />
      )}

      {earnings.is_placeholder && <Banner icon={Info} tone="warning" title="Estimate, not final pay" body={earnings.note} />}

      <View style={styles.rows}>
        <Row icon={History} title="Trip history" detail="Every route you've finished" onPress={() => navigation.navigate('TripHistory')} />
        {/* W4: the same metrics the dispatch team sees. Under Earnings because it is a
            fact about the work done, which is what this tab already is. */}
        <Row icon={BarChart3} title="Your numbers" detail="Your figures beside the hub's" onPress={() => navigation.navigate('Scorecard')} />
      </View>
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    headline: { backgroundColor: colors.lime, borderRadius: 28, padding: spacing.xl, gap: spacing.xs, marginBottom: spacing.md },
    headlineLabel: { ...typography.tileTitle, color: colors.textPrimary },
    bigNumber: { ...typography.hero, color: colors.textPrimary },
    headlineMeta: { ...typography.meta, color: colors.limeInk },
    factsCard: { gap: spacing.md, marginBottom: spacing.md, borderRadius: 28 },
    noteText: { ...typography.small, color: colors.textMuted },
    rows: { gap: spacing.sm, marginTop: spacing.sm },
  });
