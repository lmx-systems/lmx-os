import { useEffect, useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { Stop } from '../api/types';
import type { HomeStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<HomeStackParamList, 'ArrivedPickup'>;

// Screen 1j, "Arrived at pickup". Confirms location + load count before
// scanning starts.
export function ArrivedPickupScreen({ route, navigation }: Props) {
  const { stopId } = route.params;
  const [stop, setStop] = useState<Stop | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadToken, setLoadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const currentRoute = await api.getMyRoute();
        if (cancelled) return;
        setStop(currentRoute?.stops.find((s) => s.stop_id === stopId) ?? null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load this stop. Try again.');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [stopId, loadToken]);

  if (error) {
    return (
      <ScreenContainer>
        <Text style={styles.error}>{error}</Text>
        <Button label="Retry" onPress={() => setLoadToken((t) => t + 1)} />
        <Button label="Back" variant="outline" onPress={() => navigation.goBack()} />
      </ScreenContainer>
    );
  }

  if (!stop) {
    return (
      <ScreenContainer>
        <Text style={typography.subtitle}>Loading…</Text>
      </ScreenContainer>
    );
  }

  const pct = stop.parcel_count > 0 ? Math.round((stop.scanned_count / stop.parcel_count) * 100) : 0;

  return (
    <ScreenContainer>
      <Text style={typography.label}>Stop 1 · Pickup</Text>
      <Text style={[typography.title, styles.title]}>{stop.shop_name}</Text>

      <Card style={styles.card}>
        <Text style={typography.body}>
          {stop.scanned_count} / {stop.parcel_count} parcels to collect
        </Text>
        <View style={styles.progressTrack}>
          <View style={[styles.progressFill, { width: `${pct}%` }]} />
        </View>
      </Card>

      <Button
        label="Scan parcels"
        onPress={() =>
          navigation.navigate('ScanParcels', {
            stopId,
            parcelCount: stop.parcel_count,
            scannedCount: stop.scanned_count,
          })
        }
      />
      <Button
        label="Report an issue"
        variant="outline"
        onPress={() => Alert.alert('Report an issue', 'Issue reporting is not wired up yet.')}
      />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  title: { marginBottom: spacing.lg },
  card: { marginBottom: spacing.lg, gap: spacing.sm },
  progressTrack: { height: 6, borderRadius: 3, backgroundColor: colors.border, overflow: 'hidden' },
  progressFill: { height: '100%', backgroundColor: colors.accent },
  error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
});
