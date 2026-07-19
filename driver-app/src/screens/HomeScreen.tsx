import { useCallback, useEffect, useRef, useState } from 'react';
import { StyleSheet, Switch, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { MainStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<MainStackParamList, 'Home'>;

const OFFER_POLL_INTERVAL_MS = 8000;

// Screens 1d/1e, "Home - Offline/Online". No earnings summary card here in
// v1 (docs/NEXT_STEPS.md - earnings is Phase 2, no backend for it yet) -
// this screen sticks to what's real: availability + getting to work.
export function HomeScreen({ navigation }: Props) {
  const { profile, setProfile, signOut } = useAuth();
  const [isOnline, setIsOnline] = useState(profile?.status === 'available');
  const [togglingOnline, setTogglingOnline] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Resume mid-route if the app was closed/backgrounded during a delivery.
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      (async () => {
        const route = await api.getMyRoute();
        if (!cancelled && route) {
          navigation.replace('ActiveRoute');
        }
      })();
      return () => {
        cancelled = true;
      };
    }, [navigation]),
  );

  useEffect(() => {
    if (!isOnline) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    async function checkForOffers() {
      const offers = await api.getMyOffers();
      if (offers.length > 0) {
        navigation.navigate('JobDetail', { offerId: offers[0].offer_id });
      }
    }
    checkForOffers();
    pollRef.current = setInterval(checkForOffers, OFFER_POLL_INTERVAL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [isOnline, navigation]);

  async function handleToggle(value: boolean) {
    setTogglingOnline(true);
    try {
      await api.setAvailability(value ? 'available' : 'off_shift');
      setIsOnline(value);
      if (profile) setProfile({ ...profile, status: value ? 'available' : 'off_shift' });
    } finally {
      setTogglingOnline(false);
    }
  }

  return (
    <ScreenContainer>
      <View style={styles.headerRow}>
        <View>
          <Text style={typography.title}>{isOnline ? "You're online" : "You're offline"}</Text>
          <Text style={typography.subtitle}>
            {isOnline ? `${profile?.delivery_zone ?? 'Your zone'} · looking for jobs` : 'Go online to receive jobs'}
          </Text>
        </View>
        <Switch value={isOnline} onValueChange={handleToggle} disabled={togglingOnline} />
      </View>

      <Card style={styles.mapPlaceholder}>
        <Text style={typography.small}>Map view</Text>
      </Card>

      {!isOnline && <Button label="Go online" onPress={() => handleToggle(true)} loading={togglingOnline} />}
      {isOnline && (
        <Button label="View available jobs" variant="outline" onPress={() => navigation.navigate('AvailableJobs')} />
      )}

      <View style={styles.spacer} />
      <Text style={typography.small} onPress={signOut}>
        Log out
      </Text>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: spacing.lg,
  },
  mapPlaceholder: {
    height: 220,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.border,
    marginBottom: spacing.lg,
  },
  spacer: { flex: 1, marginTop: spacing.xl },
});
