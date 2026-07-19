import { useEffect, useState } from 'react';
import { FlatList, StyleSheet, Text, View } from 'react-native';

import { api } from '../api/client';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { TripSummary } from '../api/types';
import { colors, spacing, typography } from '../theme';

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

// Screen 1o, "Trip history". Real completed-route data, not week-scoped
// like the earnings estimate (screen 1n) - this is a full history list.
export function TripHistoryScreen() {
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
        ListEmptyComponent={<Text style={typography.subtitle}>No completed trips yet.</Text>}
        renderItem={({ item }) => (
          <Card>
            <View>
              <Text style={typography.body}>{formatDate(item.completed_at)}</Text>
              <Text style={typography.small}>
                {item.stop_count} stop{item.stop_count === 1 ? '' : 's'} · {item.hours.toFixed(1)}h (est.)
              </Text>
            </View>
          </Card>
        )}
      />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  list: { padding: spacing.lg, gap: spacing.sm },
});
