import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ParcelScanPanel } from '../components/ParcelScanPanel';
import { PodCapture } from '../components/PodCapture';
import { ScreenContainer } from '../components/ScreenContainer';
import { SyncStatusPill } from '../components/SyncStatusPill';
import type { PodMethod, Stop } from '../api/types';
import type { HomeStackParamList } from '../navigation/types';
import { applyPendingToStop } from '../offline/applyOptimistic';
import { useOutboxPending } from '../offline/OutboxContext';
import { outboxManager } from '../offline/outboxManager';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { primaryActionForStop, stopLabel } from '../utils/stopStatus';

type Props = NativeStackScreenProps<HomeStackParamList, 'StopDetail'>;

// Collapses what used to be three separate screens (arrive/scan/POD) into
// one state-driven view, per the wireframe's "one button reflects the
// stop's state" stop-detail approach - the UI body changes based on
// primaryActionForStop(stop) instead of pushing to a new screen for each
// step. Call/Message move here from the old ActiveRouteScreen too, since
// this is "work this specific stop," while TodayRouteScreen stays a
// lightweight overview.
//
// Actions (arrive/scan/complete) go through outboxManager instead of a
// direct api.* call - offline-first, so a tap in a dead zone is recorded
// immediately (see applyPendingToStop overlaying the queue onto the last
// server-fetched stop) and actually reaches the server once connectivity
// returns, rather than blocking on the network right here.
export function StopDetailScreen({ route, navigation }: Props) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const { stopId } = route.params;
  const [serverStop, setServerStop] = useState<Stop | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadToken, setLoadToken] = useState(0);
  // Distinguishes "first fetch still in flight" from "fetched, and this
  // stop genuinely isn't on an active route anymore" - both look like
  // serverStop === null, but only the first one should show "Loading…"
  // forever is what a driver saw before this existed: re-opening a stop
  // whose route had since completed (e.g. its last remaining stop got
  // flagged/completed while this screen wasn't focused) hung with no way
  // out.
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const pending = useOutboxPending(stopId);

  // POD form state (dropoff only). capturedUrl is the real, already-
  // uploaded URL from PodCapture (app/api/uploadCapturedFile.ts) - not
  // fabricated here anymore (docs/ROADMAP.md A3).
  const [method, setMethod] = useState<PodMethod>('photo');
  const [capturedUrl, setCapturedUrl] = useState<string | null>(null);
  const [pin, setPin] = useState('');
  const [leftAt, setLeftAt] = useState('front door');
  // PIN completion (docs/ROADMAP.md A4) is verified for real server-side
  // and can genuinely be wrong - unlike photo/signature, it can't go
  // through the fire-and-forget offline outbox (a wrong PIN would fail
  // silently in the background after the driver already navigated away).
  // It's submitted directly and awaited instead, so a rejection surfaces
  // immediately and the driver can ask the customer again.
  const [submittingPin, setSubmittingPin] = useState(false);
  const [pinError, setPinError] = useState<string | null>(null);
  // Masked voice calling (docs/ROADMAP.md A7) - the driver's own phone
  // rings via a real carrier call once this resolves (app/messaging/
  // voice_client.py bridges it to the customer server-side), so there's
  // no in-app call UI to show beyond "requested" - just enough state to
  // disable the button mid-request and surface a failure to place it.
  const [callingCustomer, setCallingCustomer] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const currentRoute = await api.getMyRoute();
      setServerStop(currentRoute?.stops.find((s) => s.stop_id === stopId) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load this stop. Try again.');
    } finally {
      setHasLoadedOnce(true);
    }
  }, [stopId]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load, loadToken]),
  );

  const stop = serverStop ? applyPendingToStop(serverStop, pending) : null;

  // The optimistic overlay only lasts as long as an action sits in the
  // outbox queue - once outboxManager successfully sends it, the item is
  // removed and the overlay's effect disappears. Without this, that
  // moment would make the screen flicker back to the stale pre-action
  // state (still-fetched serverStop) instead of progressing, since
  // nothing else here ever re-fetches after a background flush succeeds.
  // Watching for the pending count dropping is what triggers that refetch.
  const prevPendingCountRef = useRef(pending.length);
  useEffect(() => {
    if (pending.length < prevPendingCountRef.current) {
      load();
    }
    prevPendingCountRef.current = pending.length;
  }, [pending.length, load]);

  async function handleArrive() {
    await outboxManager.enqueue('arrive', stopId, {});
  }

  async function handleScanNext() {
    if (!stop || stop.scanned_count >= stop.parcel_count) return;
    await outboxManager.enqueue('scan', stopId, { scannedCount: stop.scanned_count + 1 });
  }

  async function handleCallCustomer() {
    setCallingCustomer(true);
    try {
      await api.callCustomer(stopId);
    } catch (err) {
      Alert.alert('Could not place call', err instanceof ApiError ? err.message : 'Try again in a moment.');
    } finally {
      setCallingCustomer(false);
    }
  }

  async function handlePickupComplete() {
    await outboxManager.enqueue('complete', stopId, { method: 'photo' });
    navigation.navigate('Home');
  }

  async function handleDropoffComplete() {
    if (method === 'pin') {
      setSubmittingPin(true);
      setPinError(null);
      try {
        await api.completeStop(stopId, { method: 'pin', pin, left_at: leftAt.trim() || undefined });
        navigation.navigate('Home');
      } catch (err) {
        setPinError(err instanceof ApiError ? err.message : 'Could not verify PIN. Try again.');
      } finally {
        setSubmittingPin(false);
      }
      return;
    }

    await outboxManager.enqueue('complete', stopId, {
      method,
      photo_url: method === 'photo' ? (capturedUrl ?? undefined) : undefined,
      signature_url: method === 'signature' ? (capturedUrl ?? undefined) : undefined,
      left_at: leftAt.trim() || undefined,
    });
    navigation.navigate('Home');
  }

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
    if (!hasLoadedOnce) {
      return (
        <ScreenContainer>
          <Text style={styles.loadingText}>Loading…</Text>
        </ScreenContainer>
      );
    }
    // Fetched successfully, but this stop isn't on an active route
    // anymore - most likely its route finished (every stop went
    // completed/failed) while this screen wasn't focused.
    return (
      <ScreenContainer>
        <Text style={styles.loadingText}>This stop is no longer on your active route.</Text>
        <Button label="Back to Home" onPress={() => navigation.navigate('Home')} />
      </ScreenContainer>
    );
  }

  const action = primaryActionForStop(stop);

  return (
    <ScreenContainer>
      <Text style={styles.stopTypeLabel}>{stop.stop_type === 'pickup' ? 'Pickup' : 'Drop-off'}</Text>
      <Text style={styles.title}>{stopLabel(stop)}</Text>
      {stop.stop_type === 'dropoff' && stop.notes && <Text style={styles.notes}>{stop.notes}</Text>}

      <SyncStatusPill stopId={stopId} />

      {stop.stop_type === 'dropoff' && stop.contact_name && (
        <View style={styles.contactRow}>
          <Button label="Call" variant="outline" onPress={handleCallCustomer} loading={callingCustomer} />
          <Button
            label="Message"
            variant="outline"
            onPress={() => navigation.navigate('MessageCustomer', { stopId, contactName: stop.contact_name })}
          />
        </View>
      )}

      <Card style={styles.card}>
        {action.kind === 'arrive' && <Button label="Arrived" onPress={handleArrive} />}

        {action.kind === 'scan' && (
          <ParcelScanPanel scannedCount={stop.scanned_count} total={stop.parcel_count} onScanNext={handleScanNext} />
        )}

        {action.kind === 'confirmDelivery' && stop.stop_type === 'pickup' && (
          <Button label="Confirm delivery" onPress={handlePickupComplete} />
        )}

        {action.kind === 'confirmDelivery' && stop.stop_type === 'dropoff' && (
          <PodCapture
            stopId={stopId}
            method={method}
            onChangeMethod={(m) => {
              setMethod(m);
              setCapturedUrl(null);
              setPinError(null);
            }}
            captured={capturedUrl !== null}
            onCapture={setCapturedUrl}
            pin={pin}
            onChangePin={(value) => {
              setPin(value);
              setPinError(null);
            }}
            pinError={pinError}
            leftAt={leftAt}
            onChangeLeftAt={setLeftAt}
            onSubmit={handleDropoffComplete}
            busy={submittingPin}
          />
        )}

        {action.kind === 'done' && <Text style={styles.doneText}>Stop complete</Text>}
      </Card>

      {action.kind !== 'done' && (
        <Button
          label="Flag issue"
          variant="outline"
          onPress={() => navigation.navigate('FlagIssue', { stopId })}
        />
      )}
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    loadingText: { ...typography.subtitle, color: colors.textSecondary },
    stopTypeLabel: { ...typography.label, color: colors.textPrimary },
    title: { ...typography.title, color: colors.textPrimary, marginBottom: spacing.sm },
    notes: { ...typography.small, color: colors.textMuted, marginBottom: spacing.md },
    contactRow: { flexDirection: 'row', gap: spacing.sm, marginBottom: spacing.lg },
    card: { marginBottom: spacing.lg, gap: spacing.sm },
    doneText: { ...typography.body, color: colors.textPrimary, textAlign: 'center' },
    error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
  });
