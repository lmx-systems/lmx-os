import { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, FlatList, Switch, Text, View, StyleSheet } from 'react-native';
import type { BottomTabNavigationProp } from '@react-navigation/bottom-tabs';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { OfferBanner } from '../components/OfferBanner';
import { ComplianceBanner } from '../components/ComplianceBanner';
import { LocationDegradedBanner } from '../components/LocationDegradedBanner';
import { RouteChangeBanner } from '../components/RouteChangeBanner';
import { ScreenContainer } from '../components/ScreenContainer';
import { BrandBar, Facts, KindPill, Row, SectionLabel, Segments } from '../components/ui/kit';
import { ClipboardList, Package, Radio } from 'lucide-react-native';
import type { DriverCompliance } from '../api/types';
import type { HomeStackParamList, MainTabParamList } from '../navigation/types';
import { startReportingLocation, stopReportingLocation } from '../location/reportDriverLocation';
import { useLocationDegradation } from '../location/useLocationDegradation';
import type { RouteChangeEvent } from '../realtime/routeEventsClient';
import { useRouteEvents } from '../realtime/useRouteEvents';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { isStopTerminal, primaryActionForStop, primaryActionLabel, stopLabel, stopTypeLabel } from '../utils/stopStatus';
import { etaLabel } from '../utils/etaLabel';
import { useTodayRoute } from './useTodayRoute';
import { LocationDisclosureScreen } from './LocationDisclosureScreen';
import { markAsked, shouldAskForBackgroundLocation } from '../location/backgroundConsent';
import { loadDrafts } from '../offline/offPlatformRecord';
import type { OffPlatformDraft } from '../offline/offPlatformRecord';

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
  // Read up front so a blocked driver is told WHY rather than discovering it by
  // tapping the toggle and getting a 409 (docs/ROADMAP.md R4). Same computation
  // the gate uses, so the banner and the refusal can never disagree.
  const [compliance, setCompliance] = useState<DriverCompliance | null>(null);
  // DRV-2. The disclosure is reachable from Profile, but a driver who never
  // opens Profile never grants and the sensor stays off for everyone by
  // default. Asked once, when there is actually a route to measure - agreeing
  // to background location with no work in front of you is a worse consent and
  // a worse conversion.
  const [askingConsent, setAskingConsent] = useState(false);
  // DE-7: off-platform deliveries started and not yet logged at the door.
  const [drafts, setDrafts] = useState<OffPlatformDraft[]>([]);

  const driverId = profile?.driver_id ?? null;
  useEffect(() => {
    // Only this driver's: a shared phone holds someone else's too.
    const load = () => {
      if (driverId) void loadDrafts(driverId).then(setDrafts);
      else setDrafts([]);
    };
    load();
    return navigation.addListener('focus', load);
  }, [navigation, driverId]);

  useRouteEvents(route?.route_id ?? null, useCallback((event) => setRouteChangeEvent(event), []));

  useEffect(() => {
    let cancelled = false;
    shouldAskForBackgroundLocation(Boolean(route)).then((should) => {
      if (!cancelled && should) setAskingConsent(true);
    });
    return () => {
      cancelled = true;
    };
  }, [route]);

  // Report position only while on duty (docs/ROADMAP.md F1). Keyed off
  // isOnline rather than the toggle handler below so that relaunching the app
  // mid-shift resumes reporting - otherwise a driver who force-quit would
  // stay invisible to the optimizer for the rest of their shift while
  // appearing perfectly online to themselves.
  useEffect(() => {
    if (!isOnline) {
      stopReportingLocation();
      return;
    }
    startReportingLocation().catch(() => {});
    return stopReportingLocation;
  }, [isOnline]);

  // Permission is checked when the watcher starts and when geofences register,
  // and never again - so a driver who revokes it in Settings mid-shift leaves
  // the app claiming a sensor it no longer has (DRV-5). Re-checked whenever the
  // app comes back to the foreground, which is exactly when it can have
  // changed.
  const locationState = useLocationDegradation(isOnline);

  const loadCompliance = useCallback(async () => {
    try {
      setCompliance(await api.getMyCompliance());
    } catch {
      // Non-fatal: the gate still refuses correctly, the driver just gets the
      // 409's text instead of the banner. Not worth failing the screen over.
    }
  }, []);

  useEffect(() => {
    void loadCompliance();
  }, [loadCompliance]);

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
      // Re-read it: the most likely cause is a compliance change since the screen
      // loaded (a document expiring overnight, or a review coming back), and the
      // banner should reflect that rather than staying stale behind the error.
      void loadCompliance();
    } finally {
      setTogglingOnline(false);
    }
  }

  const currentStop = route?.stops.find((s) => !isStopTerminal(s));
  const doneCount = route?.stops.filter(isStopTerminal).length ?? 0;
  const primaryOffer = offers.find((o) => o.offer_id === selectedOfferId) ?? offers[0];
  const otherOffers = offers.filter((o) => o.offer_id !== primaryOffer?.offer_id);

  if (askingConsent) {
    // A full-screen takeover rather than a banner: this is a consent step, and
    // Play requires the disclosure to be what a driver sees before the runtime
    // prompt rather than something alongside it. `markAsked` runs on either
    // answer - declining is a decision, and re-asking every launch would make
    // "Not now" meaningless.
    return (
      <LocationDisclosureScreen
        onDecided={() => {
          void markAsked();
          setAskingConsent(false);
        }}
      />
    );
  }
  const zone = profile?.delivery_zone ?? 'Your zone';
  const nextStops = route ? route.stops.filter((st) => st.stop_id !== currentStop?.stop_id) : [];

  const top = (
    <>
    <BrandBar
      right={
        <View style={styles.duty}>
          <Text style={styles.dutyText}>{isOnline ? 'On duty' : 'Off duty'}</Text>
          <Switch
            value={isOnline}
            onValueChange={handleToggle}
            disabled={togglingOnline}
            accessibilityLabel={isOnline ? 'Go off duty' : 'Go on duty'}
            // Ink when on, like every other selected control; not the platform's green.
            trackColor={{ false: colors.borderStrong, true: colors.primary }}
            ios_backgroundColor={colors.borderStrong}
          />
        </View>
      }
    />

    {/* The page title, as on the console: what the driver is doing, in one line. */}
    <View style={styles.titleBlock}>
      <Text style={styles.kicker}>
        {route ? zone : isOnline ? `${zone} · looking for jobs` : 'Go online to receive jobs'}
      </Text>
      <Text style={styles.display} accessibilityRole="header">
        {route
          ? doneCount >= route.stops.length
            ? 'Route complete'
            : `Stop ${doneCount + 1} of ${route.stops.length}`
          : isOnline
            ? "You're online"
            : "You're offline"}
      </Text>
      {/* Each bar from its own stop, so a stop finished out of order still reads right. */}
      {route && (
        <Segments
          states={route.stops.map((st) => (isStopTerminal(st) ? 'done' : st.stop_id === currentStop?.stop_id ? 'current' : 'ahead'))}
        />
      )}
    </View>

    {/* Below the compliance banner, which refuses to let a driver work at
        all. This one says a measurement is off while everything else keeps
        working, and putting it above would rank it wrongly (DRV-5). */}
    <LocationDegradedBanner state={locationState} />

    {compliance && !compliance.can_go_on_shift && (
      <ComplianceBanner
        compliance={compliance}
        // Documents lives in the Profile stack under a different tab, so this
        // crosses navigators rather than pushing within Home's - getParent()
        // is what makes that reachable from here.
        onOpenDocuments={() =>
          navigation
            .getParent<BottomTabNavigationProp<MainTabParamList>>()
            ?.navigate('ProfileTab', { screen: 'Documents' })
        }
      />
    )}

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
      // The one card that matters, as on the on-call phone: what kind of stop,
      // where, the facts at a glance, and one thing to press.
      <Card style={styles.currentCard} onPress={() => navigation.navigate('StopDetail', { stopId: currentStop.stop_id })}>
        <View style={styles.currentTop}>
          <KindPill label={stopTypeLabel(currentStop)} tone={currentStop.stop_type === 'pickup' ? 'neutral' : 'lime'} />
          <Text style={styles.meta}>Next stop</Text>
        </View>
        <View style={styles.currentText}>
          <Text style={styles.headline}>{stopLabel(currentStop)}</Text>
          {currentStop.stop_type === 'pickup' && currentStop.address ? (
            <Text style={styles.secondary}>{currentStop.address}</Text>
          ) : null}
        </View>
        <Facts
          items={[
            // The forecast the customer was shown, not a deadline.
            { k: 'ETA', v: etaLabel(currentStop)?.replace(/^ETA /, '') ?? '—' },
            // Only a pickup carries a real parcel count; a drop-off's is always 1
            // on the server, which would tell a driver to hand over one of three.
            ...(currentStop.stop_type === 'pickup' ? [{ k: 'Parcels', v: String(currentStop.parcel_count) }] : []),
            { k: 'Then', v: `${route.stops.length - doneCount - 1} more` },
          ]}
        />
        <Button
          label={primaryActionLabel(primaryActionForStop(currentStop), currentStop.stop_type)}
          onPress={() => navigation.navigate('StopDetail', { stopId: currentStop.stop_id })}
        />
      </Card>
    )}
    </>
  );

  if (route) {
    // The whole page scrolls as one list: the header, banners and the next-stop
    // card ride above the stops, so a small phone with a banner up never pushes
    // the card's button out of reach.
    return (
      <ScreenContainer scroll={false}>
        <FlatList
          data={nextStops}
          keyExtractor={(st) => st.stop_id}
          contentContainerStyle={styles.list}
          ListHeaderComponent={
            <>
              {top}
              {nextStops.length > 0 && <SectionLabel>Every stop</SectionLabel>}
            </>
          }
          renderItem={({ item }) => (
            <View style={[styles.stopRow, isStopTerminal(item) && styles.stopRowDone]}>
              <View style={[styles.sequenceBadge, item.status === 'completed' && styles.sequenceDone]}>
                <Text style={styles.sequenceText}>{item.status === 'completed' ? '✓' : item.status === 'failed' ? '✕' : item.sequence + 1}</Text>
              </View>
              <View style={styles.stopTextCol}>
                <Text style={isStopTerminal(item) ? styles.stopLabelDone : styles.stopTitle} numberOfLines={1}>
                  {stopLabel(item)}
                </Text>
                <Text style={styles.stopMeta}>{stopTypeLabel(item)}</Text>
              </View>
              {etaLabel(item) && <Text style={styles.stopEta}>{etaLabel(item)?.replace(/^ETA /, '')}</Text>}
            </View>
          )}
        />
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      {top}

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
            <Row
              key={offer.offer_id}
              icon={Package}
              title={offer.stops[0]?.shop_name || 'Pickup'}
              detail={`${offer.stops.length} ${offer.stops.length === 1 ? 'delivery' : 'deliveries'}${
                offer.estimated_pay_cents !== null ? ` · $${(offer.estimated_pay_cents / 100).toFixed(2)}` : ''
              }`}
              onPress={() => setSelectedOfferId(offer.offer_id)}
            />
          ))}
        </>
      )}

      {!route && !primaryOffer && (
        // Said in words: there is no map, and an empty grey box read as a broken one.
        <Card style={styles.waiting}>
          {isOnline ? <ActivityIndicator color={colors.textMuted} /> : <Radio size={22} color={colors.textMuted} />}
          <Text style={styles.waitingText}>
            {isOnline ? 'Looking for jobs near you. An offer appears here.' : 'Go online and jobs near you appear here.'}
          </Text>
        </Card>
      )}

      {!route && !primaryOffer && !isOnline && (
        <Button label="Go online" onPress={() => handleToggle(true)} loading={togglingOnline} />
      )}

      {/* DE-7. Not LMX work, so never in the way of it: only when there is no
          route, below everything that is. */}
      {!route && (
        <View style={styles.offPlatform}>
          {drafts
            .filter((d) => !d.deliveredAt)
            .map((d) => (
              <Row
                key={d.recordId}
                icon={ClipboardList}
                tone="needs"
                title={`Finish logging ${d.jobLabel || 'a job'}`}
                detail="Started, not logged at the door yet"
                onPress={() => navigation.navigate('OffPlatform', { recordId: d.recordId })}
              />
            ))}
          <Button label="Log an off-platform delivery" variant="outline" onPress={() => navigation.navigate('OffPlatform', {})} />
        </View>
      )}
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    duty: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
    dutyText: { ...typography.meta, color: colors.textSecondary },
    titleBlock: { gap: spacing.sm, marginTop: spacing.lg, marginBottom: spacing.lg },
    kicker: { ...typography.subtitle, color: colors.textSecondary },
    display: { ...typography.display, color: colors.textPrimary },
    meta: { ...typography.meta, color: colors.textMuted },
    headline: { ...typography.headline, color: colors.textPrimary },
    secondary: { ...typography.body, color: colors.textSecondary },
    offPlatform: { marginTop: spacing.sm, gap: spacing.sm },
    currentCard: { marginBottom: spacing.lg, gap: spacing.lg, borderRadius: 28 },
    currentTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    currentText: { gap: 4 },
    waiting: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, marginBottom: spacing.lg },
    waitingText: { ...typography.body, color: colors.textSecondary, flex: 1 },
    error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
    list: { paddingBottom: spacing.lg, gap: spacing.sm },
    // White rows on the grey ground, no dividers.
    stopRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: spacing.md,
      paddingVertical: spacing.md,
      paddingHorizontal: spacing.lg,
      backgroundColor: colors.surface,
      borderRadius: 20,
    },
    stopRowDone: { opacity: 0.55 },
    sequenceBadge: {
      width: 32,
      height: 32,
      borderRadius: radius.pill,
      backgroundColor: colors.surfaceAlt,
      alignItems: 'center',
      justifyContent: 'center',
    },
    sequenceDone: { backgroundColor: colors.lime },
    stopTextCol: { flex: 1, minWidth: 0, gap: 1 },
    sequenceText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 13, color: colors.textPrimary },
    stopTitle: { ...typography.rowTitle, fontSize: 15, color: colors.textPrimary },
    stopMeta: { ...typography.small, color: colors.textMuted },
    stopEta: { ...typography.meta, color: colors.textSecondary },
    stopLabelDone: { ...typography.rowTitle, fontSize: 15, color: colors.textPrimary, textDecorationLine: 'line-through' as const },
  });
