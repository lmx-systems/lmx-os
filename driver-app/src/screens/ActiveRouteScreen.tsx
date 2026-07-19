import { useCallback, useState } from 'react';
import { Alert, FlatList, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { Route, Stop } from '../api/types';
import type { HomeStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<HomeStackParamList, 'ActiveRoute'>;

function stopLabel(stop: Stop): string {
  return stop.stop_type === 'pickup' ? stop.shop_name || 'Pickup' : stop.address || 'Drop-off';
}

// Merges wireframe screens 1h (route navigation), 1i (stops list), and 1l
// (en route to customer) into one hub screen for v1 - the "current stop"
// card at the top covers the nav-banner/en-route-to-customer role for
// whichever stop type is next, and the list below covers the stops
// overview. No turn-by-turn map/"Open in Maps" handoff yet (needs a real
// maps SDK integration - not wired in this pass).
export function ActiveRouteScreen({ navigation }: Props) {
  const [route, setRoute] = useState<Route | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const fetched = await api.getMyRoute();
    setRoute(fetched);
    if (!fetched) {
      navigation.replace('Home');
    }
  }, [navigation]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  if (!route) {
    return (
      <ScreenContainer>
        <Text style={typography.subtitle}>Loading your route…</Text>
      </ScreenContainer>
    );
  }

  const currentStop = route.stops.find((s) => s.status !== 'completed');
  const doneCount = route.stops.filter((s) => s.status === 'completed').length;

  async function handleArrived() {
    if (!currentStop) return;
    setBusy(true);
    try {
      await api.arriveAtStop(currentStop.stop_id);
      if (currentStop.stop_type === 'pickup') {
        navigation.navigate('ArrivedPickup', { stopId: currentStop.stop_id });
      } else {
        navigation.navigate('ProofOfDelivery', { stopId: currentStop.stop_id });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScreenContainer scroll={false}>
      <View style={styles.header}>
        <Text style={typography.title}>
          Stop {doneCount + 1} of {route.stops.length}
        </Text>
      </View>

      {currentStop && (
        <Card style={styles.currentCard}>
          <Text style={typography.label}>{currentStop.stop_type === 'pickup' ? 'Pickup' : 'Drop-off'}</Text>
          <Text style={typography.body}>{stopLabel(currentStop)}</Text>
          {currentStop.stop_type === 'dropoff' && currentStop.notes && (
            <Text style={typography.small}>{currentStop.notes}</Text>
          )}
          {currentStop.stop_type === 'dropoff' && currentStop.contact_name && (
            <View style={styles.contactRow}>
              <Button
                label="Call"
                variant="outline"
                onPress={() =>
                  Alert.alert(
                    'Masked calling not available yet',
                    'Voice calling needs its own Twilio Voice/Proxy setup, separate from the SMS messaging below. Use Message for now.',
                  )
                }
              />
              <Button
                label="Message"
                variant="outline"
                onPress={() =>
                  navigation.navigate('MessageCustomer', {
                    stopId: currentStop.stop_id,
                    contactName: currentStop.contact_name,
                  })
                }
              />
            </View>
          )}
          <Button label="Arrived" onPress={handleArrived} loading={busy} />
        </Card>
      )}

      <FlatList
        data={route.stops}
        keyExtractor={(s) => s.stop_id}
        contentContainerStyle={styles.list}
        renderItem={({ item }) => (
          <View style={[styles.stopRow, item.status === 'completed' && styles.stopRowDone]}>
            <Text style={typography.small}>
              {item.status === 'completed' ? '✓' : item.sequence + 1}
            </Text>
            <View style={styles.stopTextCol}>
              <Text style={item.status === 'completed' ? styles.stopLabelDone : typography.body}>
                {stopLabel(item)}
              </Text>
            </View>
          </View>
        )}
      />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.lg },
  currentCard: { margin: spacing.lg, gap: spacing.sm },
  contactRow: { flexDirection: 'row', gap: spacing.sm },
  list: { paddingHorizontal: spacing.lg, paddingBottom: spacing.lg, gap: spacing.sm },
  stopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  stopRowDone: { opacity: 0.5 },
  stopTextCol: { flex: 1 },
  stopLabelDone: { ...typography.body, textDecorationLine: 'line-through' as const },
});
