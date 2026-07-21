import { useEffect, useMemo, useState } from 'react';
import { FlatList, StyleSheet, Text, View } from 'react-native';

import { api } from '../api/client';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { TripSummary } from '../api/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

// Screen 1o, "Trip history". Real completed-route data, not week-scoped
// like the earnings estimate (screen 1n) - this is a full history list.
export function TripHistoryScreen() {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [trips, setTrips] = useState<TripSummary[] | null>(null);

  useEffect(() => {
    (async () => {
      setTrips(await api.getTrips());
    })();
  }, []);

  if (!trips) {
    return null;
  }

  return (
    <ScreenContainer scroll={false}>
      <FlatList
        data={trips}
        keyExtractor={(t) => t.route_id}
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Text style={styles.emptyText}>No completed trips yet.</Text>}
        renderItem={({ item }) => (
          <Card>
            <View>
              <Text style={styles.tripDate}>{formatDate(item.completed_at)}</Text>
              <Text style={styles.tripMeta}>
                {item.stop_count} stop{item.stop_count === 1 ? '' : 's'} · {item.hours.toFixed(1)}h (est.)
              </Text>
            </View>
          </Card>
        )}
      />
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    list: { padding: spacing.lg, gap: spacing.sm },
    emptyText: { ...typography.subtitle, color: colors.textSecondary },
    tripDate: { ...typography.body, color: colors.textPrimary },
    tripMeta: { ...typography.small, color: colors.textMuted },
  });
