import { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { DriverScorecard, ScorecardMetric } from '../api/types';
import type { EarningsStackParamList } from '../navigation/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

type Props = NativeStackScreenProps<EarningsStackParamList, 'Scorecard'>;

/**
 * Screen for docs/ROADMAP.md W4, story DR-10: the driver sees the same metrics and
 * definitions the orchestrator sees.
 *
 * The framing matters as much as the numbers, and the roadmap says so - "a shared
 * standard, not a camera pointed at me". Three things follow.
 *
 * Every figure comes with the hub's own figure beside it, because a number about you with
 * nothing to compare it against reads as a target somebody set. When there are too few
 * drivers on shift for a team median to point at anyone but one person, the server
 * withholds the comparison and says so - the driver's own numbers still show.
 *
 * The definitions are on the screen. A metric a driver cannot check the meaning of is
 * not a shared standard, whatever it is called.
 *
 * There is no score, no grade and no ranking. Two measurements and the team's, which is
 * what was asked for and deliberately not more.
 */

function formatValue(metric: ScorecardMetric, value: number | null): string {
  if (value === null) return '—';
  if (metric.unit === 'minutes') {
    // Signed on purpose: early and late are different things to know about yourself, and
    // an absolute value would tell a consistently-early driver they were "accurate".
    const rounded = Math.round(value);
    if (rounded === 0) return 'on time';
    return rounded > 0 ? `${rounded} min late` : `${Math.abs(rounded)} min early`;
  }
  return `${value.toFixed(1)} / hr`;
}

// What each number actually means, in the driver's terms rather than the schema's.
const EXPLANATIONS: Record<string, string> = {
  'Deliveries per hour': 'Deliveries you completed, divided by the hours you were on shift.',
  'ETA error (actual minus predicted)':
    'How far your arrivals landed from the time the app predicted before you set off.',
};

export function ScorecardScreen({}: Props) {
  const colors = useThemeColors();
  const styles = createStyles(colors);
  const [card, setCard] = useState<DriverScorecard | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .getMyScorecard()
      .then((result) => {
        if (live) setCard(result);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, []);

  if (failed) {
    return (
      <ScreenContainer>
        <Text style={styles.muted}>Couldn&rsquo;t load your scorecard. Pull back and try again.</Text>
      </ScreenContainer>
    );
  }

  if (!card) {
    return (
      <ScreenContainer>
        <Text style={styles.muted}>Loading…</Text>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <Text style={styles.period}>Last {card.window_days} days</Text>

      {card.metrics.map((metric) => (
        <Card key={metric.name} style={styles.card}>
          <Text style={styles.metricName}>{metric.name}</Text>

          {metric.not_measured ? (
            /* An honest refusal rather than a zero. Telling a new driver they are
               performing at 0.0 per hour would be both false and exactly the reading
               this screen exists to avoid. */
            <Text style={styles.muted}>{metric.not_measured}</Text>
          ) : (
            <View style={styles.row}>
              <View style={styles.column}>
                <Text style={styles.label}>You</Text>
                <Text style={styles.value}>{formatValue(metric, metric.own_median)}</Text>
                <Text style={styles.sample}>
                  over {metric.own_sample_size} {metric.unit === 'minutes' ? 'stops' : 'shifts'}
                </Text>
              </View>
              <View style={styles.column}>
                <Text style={styles.label}>Your hub</Text>
                <Text style={[styles.value, styles.fleetValue]}>
                  {formatValue(metric, metric.fleet_median)}
                </Text>
                <Text style={styles.sample}>
                  {metric.fleet_median === null ? 'not shown' : 'typical'}
                </Text>
              </View>
            </View>
          )}

          {EXPLANATIONS[metric.name] && (
            <Text style={styles.explanation}>{EXPLANATIONS[metric.name]}</Text>
          )}
        </Card>
      ))}

      {card.comparison_withheld && (
        <Card style={styles.card}>
          <Text style={styles.muted}>{card.comparison_withheld}</Text>
        </Card>
      )}

      <Text style={styles.footnote}>
        These are the same numbers, worked out the same way, that the dispatch team sees.
        There is no score and no ranking.
      </Text>
    </ScreenContainer>
  );
}

function createStyles(colors: ColorScheme) {
  return StyleSheet.create({
    period: {
      ...typography.small,
      color: colors.textMuted,
      marginBottom: spacing.sm,
    },
    card: { marginBottom: spacing.md },
    metricName: {
      ...typography.label,
      color: colors.textPrimary,
      marginBottom: spacing.sm,
    },
    row: { flexDirection: 'row', gap: spacing.lg },
    column: { flex: 1 },
    label: {
      ...typography.small,
      color: colors.textMuted,
      marginBottom: 2,
    },
    value: {
      ...typography.title,
      color: colors.textPrimary,
    },
    fleetValue: { color: colors.textMuted },
    sample: {
      ...typography.small,
      color: colors.textMuted,
      marginTop: 2,
    },
    explanation: {
      ...typography.small,
      color: colors.textMuted,
      marginTop: spacing.sm,
    },
    muted: {
      ...typography.body,
      color: colors.textMuted,
    },
    footnote: {
      ...typography.small,
      color: colors.textMuted,
      marginTop: spacing.sm,
    },
  });
}
