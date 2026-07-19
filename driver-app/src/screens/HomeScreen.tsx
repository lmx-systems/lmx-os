import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, StyleSheet, Switch, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { HomeStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<HomeStackParamList, 'Home'>;

const OFFER_POLL_INTERVAL_MS = 8000;

// Screens 1d/1e, "Home - Offline/Online". No earnings summary card here in
// v1 (docs/NEXT_STEPS.md - earnings is Phase 2, no backend for it yet) -
// this screen sticks to what's real: availability + getting to work.
export function HomeScreen({ navigation }: Props) {
  const { profile, setProfile } = useAuth();
  // Derived from profile.status, not separate state - profile can be
  // refreshed by paths other than this screen's own toggle (re-login, a
  // future pull-to-refresh), and a standalone isOnline would silently drift
  // out of sync with the server's actual view whenever that happens.
  const isOnline = profile?.status === 'available';
  const [togglingOnline, setTogglingOnline] = useState(false);
  const [toggleError, setToggleError] = useState<string | null>(null);
  const [isForeground, setIsForeground] = useState(AppState.currentState === 'active');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (nextState) => {
      setIsForeground(nextState === 'active');
    });
    return () => subscription.remove();
  }, []);

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
    // Also paused while backgrounded - a driver can stay marked online for
    // a whole shift with the phone in their pocket, and an offer can't be
    // meaningfully acted on without a push-notification system (which
    // doesn't exist yet) while the app isn't in the foreground anyway.
    if (!isOnline || !isForeground) {
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
  }, [isOnline, isForeground, navigation]);

  async function handleToggle(value: boolean) {
    setTogglingOnline(true);
    setToggleError(null);
    try {
      await api.setAvailability(value ? 'available' : 'off_shift');
      if (profile) setProfile({ ...profile, status: value ? 'available' : 'off_shift' });
    } catch (err) {
      // Most likely a 409 from app/api/driver_routes.py's going-online gate
      // (an expired or missing document - screen 1r's compliance section).
      setToggleError(err instanceof ApiError ? err.message : 'Could not go online - try again.');
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

      {toggleError && <Text style={styles.error}>{toggleError}</Text>}

      {!isOnline && <Button label="Go online" onPress={() => handleToggle(true)} loading={togglingOnline} />}
      {isOnline && (
        <Button label="View available jobs" variant="outline" onPress={() => navigation.navigate('AvailableJobs')} />
      )}
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
  error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
  spacer: { flex: 1, marginTop: spacing.xl },
});
