import { useEffect, useMemo, useState } from 'react';
import { FlatList, StyleSheet, Text } from 'react-native';

import { api } from '../api/client';
import { ScreenContainer } from '../components/ScreenContainer';
import { Row } from '../components/ui/kit';
import { Route as RouteIcon } from 'lucide-react-native';
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
          <Row
            icon={RouteIcon}
            title={formatDate(item.completed_at)}
            detail={`${item.stop_count} stop${item.stop_count === 1 ? '' : 's'} · ${item.hours.toFixed(1)}h (est.)`}
          />
        )}
      />
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    list: { paddingBottom: spacing.lg, gap: spacing.sm },
    emptyText: { ...typography.subtitle, color: colors.textSecondary },
    tripDate: { ...typography.body, color: colors.textPrimary },
    tripMeta: { ...typography.small, color: colors.textMuted },
  });
