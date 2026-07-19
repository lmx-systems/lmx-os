import { useEffect, useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { Stop } from '../api/types';
import type { MainStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<MainStackParamList, 'ArrivedPickup'>;

// Screen 1j, "Arrived at pickup". Confirms location + load count before
// scanning starts.
export function ArrivedPickupScreen({ route, navigation }: Props) {
  const { stopId } = route.params;
  const [stop, setStop] = useState<Stop | null>(null);

  useEffect(() => {
    (async () => {
      const currentRoute = await api.getMyRoute();
      setStop(currentRoute?.stops.find((s) => s.stop_id === stopId) ?? null);
    })();
  }, [stopId]);

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

      <Button label="Scan parcels" onPress={() => navigation.navigate('ScanParcels', { stopId, parcelCount: stop.parcel_count })} />
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
});
