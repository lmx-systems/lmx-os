import { useCallback, useMemo, useState } from 'react';
import { FlatList, Switch, Text, View, StyleSheet } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { OfferBanner } from '../components/OfferBanner';
import { RouteChangeBanner } from '../components/RouteChangeBanner';
import { ScreenContainer } from '../components/ScreenContainer';
import type { HomeStackParamList } from '../navigation/types';
import type { RouteChangeEvent } from '../realtime/routeEventsClient';
import { useRouteEvents } from '../realtime/useRouteEvents';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { isStopTerminal, primaryActionForStop, primaryActionLabel, stopLabel } from '../utils/stopStatus';
import { useTodayRoute } from './useTodayRoute';

type Props = NativeStackScreenProps<HomeStackParamList, 'Home'>;

// The wireframe's "Today's Route" home screen - one on-deck stop card plus
// a collapsed list of the rest, replacing three separate screens (Home,
// Available Jobs, Active Route) and their navigation transitions between
// each other. A driver lives here for the whole shift.
export function TodayRouteScreen({ navigation }: Props) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const { profile, setProfile } = useAuth();
  const { route, offers, isOnline, refresh, setRoute } = useTodayRoute();
  const [togglingOnline, setTogglingOnline] = useState(false);
  const [toggleError, setToggleError] = useState<string | null>(null);
  const [selectedOfferId, setSelectedOfferId] = useState<string | null>(null);
  const [routeChangeEvent, setRouteChangeEvent] = useState<RouteChangeEvent | null>(null);

  useRouteEvents(route?.route_id ?? null, useCallback((event) => setRouteChangeEvent(event), []));

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

  const currentStop = route?.stops.find((s) => !isStopTerminal(s));
  const doneCount = route?.stops.filter(isStopTerminal).length ?? 0;
  const primaryOffer = offers.find((o) => o.offer_id === selectedOfferId) ?? offers[0];
  const otherOffers = offers.filter((o) => o.offer_id !== primaryOffer?.offer_id);

  return (
    <ScreenContainer scroll={!route}>
      <View style={styles.headerRow}>
        <View>
          <Text style={styles.statusTitle}>{isOnline ? "You're online" : "You're offline"}</Text>
          <Text style={styles.statusSubtitle}>
            {route
              ? `Stop ${doneCount + 1} of ${route.stops.length}`
              : isOnline
                ? `${profile?.delivery_zone ?? 'Your zone'} · looking for jobs`
                : 'Go online to receive jobs'}
          </Text>
        </View>
        <Switch value={isOnline} onValueChange={handleToggle} disabled={togglingOnline} />
      </View>

      {toggleError && <Text style={styles.error}>{toggleError}</Text>}

      {routeChangeEvent && (
        <RouteChangeBanner
          event={routeChangeEvent}
          onAccept={() => {
            setRouteChangeEvent(null);
            refresh();
          }}
        />
      )}

      {route && currentStop && (
        <Card style={styles.currentCard} onPress={() => navigation.navigate('StopDetail', { stopId: currentStop.stop_id })}>
          <Text style={styles.stopTypeLabel}>{currentStop.stop_type === 'pickup' ? 'Pickup' : 'Drop-off'}</Text>
          <Text style={styles.stopBodyText}>{stopLabel(currentStop)}</Text>
          <Button
            label={primaryActionLabel(primaryActionForStop(currentStop))}
            onPress={() => navigation.navigate('StopDetail', { stopId: currentStop.stop_id })}
          />
        </Card>
      )}

      {route && (
        <FlatList
          data={route.stops}
          keyExtractor={(s) => s.stop_id}
          contentContainerStyle={styles.list}
          renderItem={({ item }) => (
            <View style={[styles.stopRow, isStopTerminal(item) && styles.stopRowDone]}>
              <Text style={styles.sequenceText}>{item.status === 'completed' ? '✓' : item.status === 'failed' ? '✕' : item.sequence + 1}</Text>
              <View style={styles.stopTextCol}>
                <Text style={isStopTerminal(item) ? styles.stopLabelDone : styles.stopBodyText}>{stopLabel(item)}</Text>
              </View>
            </View>
          )}
        />
      )}

      {!route && primaryOffer && (
        <>
          <OfferBanner
            offer={primaryOffer}
            onAccept={(acceptedRoute) => setRoute(acceptedRoute)}
            onDecline={() => {
              setSelectedOfferId(null);
              refresh();
            }}
          />
          {otherOffers.map((offer) => (
            <Card key={offer.offer_id} style={styles.otherOfferCard} onPress={() => setSelectedOfferId(offer.offer_id)}>
              <Text style={styles.cardBody}>{offer.stops[0]?.shop_name || 'Pickup'}</Text>
              <Text style={styles.cardSmall}>
                {offer.stops.length} stop{offer.stops.length > 1 ? 's' : ''}
                {offer.estimated_pay_cents !== null ? ` · $${(offer.estimated_pay_cents / 100).toFixed(2)}` : ''}
              </Text>
            </Card>
          ))}
        </>
      )}

      {!route && !primaryOffer && (
        <Card style={styles.mapPlaceholder}>
          <Text style={styles.mapLabel}>{isOnline ? 'Looking for jobs…' : 'Map view'}</Text>
        </Card>
      )}

      {!route && !primaryOffer && !isOnline && (
        <Button label="Go online" onPress={() => handleToggle(true)} loading={togglingOnline} />
      )}
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    headerRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'flex-start',
      marginBottom: spacing.lg,
    },
    statusTitle: { ...typography.title, color: colors.textPrimary },
    statusSubtitle: { ...typography.subtitle, color: colors.textSecondary },
    currentCard: { marginBottom: spacing.lg, gap: spacing.sm },
    stopTypeLabel: { ...typography.label, color: colors.textPrimary },
    stopBodyText: { ...typography.body, color: colors.textPrimary },
    mapPlaceholder: {
      height: 220,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: colors.border,
      marginBottom: spacing.lg,
    },
    mapLabel: { ...typography.small, color: colors.textMuted },
    error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
    otherOfferCard: { marginBottom: spacing.sm },
    cardBody: { ...typography.body, color: colors.textPrimary, marginBottom: spacing.xs },
    cardSmall: { ...typography.small, color: colors.textMuted },
    list: { paddingBottom: spacing.lg, gap: spacing.sm },
    stopRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: spacing.md,
      paddingVertical: spacing.md,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    stopRowDone: { opacity: 0.5 },
    stopTextCol: { flex: 1 },
    sequenceText: { ...typography.label, fontSize: 15, color: colors.textPrimary },
    stopLabelDone: { ...typography.body, color: colors.textPrimary, textDecorationLine: 'line-through' as const },
  });
