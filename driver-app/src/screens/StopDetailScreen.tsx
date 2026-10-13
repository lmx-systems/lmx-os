import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Linking, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ParcelScanPanel } from '../components/ParcelScanPanel';
import { CodPanel } from '../components/CodPanel';
import { ReturnsPanel } from '../components/ReturnsPanel';
import { ItemFactsPanel } from '../components/ItemFactsPanel';
import { DockSurveyModal } from '../components/DockSurveyModal';
import { siteFactQuestions } from '../components/siteFactQuestions';
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
import { currentPin, doorPin } from '../location/currentPin';
import { dialable } from '../utils/dialable';
import { openTurnByTurnNavigation } from '../utils/navigation';
import { primaryActionForStop, stopLabel, stopTypeLabel } from '../utils/stopStatus';

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
  // A list, because an order can require several photos with named subjects, and
  // a signature can be required on top of them rather than instead of them.
  const [photoUrls, setPhotoUrls] = useState<string[]>([]);
  const [signatureUrl, setSignatureUrl] = useState<string | null>(null);
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
    // The stop pin, when the phone has a recent fix (DE-4). Never waited for.
    const pin = await currentPin();
    await outboxManager.enqueue('arrive', stopId, pin ? { ...pin } : {});
  }

  async function handleScanNext() {
    if (!stop || stop.scanned_count >= stop.parcel_count) return;
    await outboxManager.enqueue('scan', stopId, { scannedCount: stop.scanned_count + 1 });
  }

  // Whether this pickup's orders came with barcodes to check a scan against
  // (DE-5) - the source's own, not ones LMX minted, which no label carries.
  // Most sources send none, and then a barcode can only be counted; offline,
  // or when the list will not load, the count is the safe fallback.
  const [expectsBarcodes, setExpectsBarcodes] = useState(false);
  const scanning = stop?.stop_type === 'pickup' && stop.scanned_count < stop.parcel_count;
  useEffect(() => {
    if (!scanning) return;
    let live = true;
    api
      .stopParcels(stopId)
      .then((parcels) => live && setExpectsBarcodes(parcels.some((p) => p.from_source)))
      .catch(() => live && setExpectsBarcodes(false));
    return () => {
      live = false;
    };
  }, [scanning, stopId]);

  async function handleBarcode(barcode: string) {
    if (!expectsBarcodes) {
      await handleScanNext();
      return;
    }
    // Checked against the stop's orders on the server; a wrong part comes
    // back rejected, and SyncStatusPill says so.
    await outboxManager.enqueue('scan-parcel', stopId, { barcode });
  }

  function handleNavigate() {
    if (!stop) return;
    openTurnByTurnNavigation(stop.lat, stop.lng).catch(() => {
      Alert.alert('Could not open navigation', 'No maps app is available on this device.');
    });
  }

  // The recipient is called or texted from the driver's own phone. LMX used
  // to bridge a masked call through Twilio; with no phone provider, the
  // phone's own dialer and messages app do it, and the recipient sees the
  // driver's number.
  function contactRecipient(scheme: 'tel' | 'sms') {
    const number = dialable(stop?.contact_phone);
    if (!number) return;
    Linking.openURL(`${scheme}:${number}`).catch(() => {
      Alert.alert(
        scheme === 'tel' ? 'Could not start a call' : 'Could not open messages',
        `The number is ${number}.`,
      );
    });
  }

  // Shown after a dropoff completes, when the server says this dock is due.
  const [surveying, setSurveying] = useState(false);
  // DE-15: the site's facts alone, when the survey is not due but the server
  // sent facts still open - a door captured before the desk filled its site.
  const [askingFacts, setAskingFacts] = useState(false);

  async function handlePickupComplete() {
    await outboxManager.enqueue('complete', stopId, { method: 'photo', ...doorPin(await currentPin()) });
    navigation.navigate('Home');
  }

  // DRV-7. **After the completion is queued or confirmed, never before it** - a
  // measurement may fail, a delivery may not. The driver is already free to
  // walk away; this asks while they are still at the door, and dismissing it
  // costs nothing. One place for both ways of completing a drop-off: the PIN
  // path went straight home, so a dock confirmed by PIN was never surveyed.
  function leaveTheDoor() {
    if (stop?.dock_needs_survey) {
      setSurveying(true);
      return;
    }
    if (siteFactQuestions(stop?.site_facts).length > 0) {
      setAskingFacts(true);
      return;
    }
    navigation.navigate('Home');
  }

  // A visit (DE-6): the technician is at the door with nothing to hand over.
  // Completing it takes the door pin, then the capture opens - the same survey
  // and four photos as a drop-off, without the questions only a delivery can
  // answer.
  async function handleVisitComplete() {
    await outboxManager.enqueue('complete', stopId, { method: 'photo', ...doorPin(await currentPin()) });
    leaveTheDoor();
  }

  async function handleDropoffComplete() {
    // The door pin: where the item changed hands (DE-4).
    const door = doorPin(await currentPin());
    if (method === 'pin') {
      setSubmittingPin(true);
      setPinError(null);
      try {
        await api.completeStop(stopId, { method: 'pin', pin, left_at: leftAt.trim() || undefined, ...door });
        leaveTheDoor();
      } catch (err) {
        setPinError(err instanceof ApiError ? err.message : 'Could not verify PIN. Try again.');
      } finally {
        setSubmittingPin(false);
      }
      return;
    }

    await outboxManager.enqueue('complete', stopId, {
      method,
      // Both, always - the server takes whatever satisfies the order's stated
      // requirement, and a signature can be needed alongside photos rather than
      // instead of them. Sending only the one matching `method` is what made a
      // multi-photo order impossible to complete from here.
      photo_urls: photoUrls.length > 0 ? photoUrls : undefined,
      signature_url: signatureUrl ?? undefined,
      left_at: leftAt.trim() || undefined,
      ...door,
    });

    leaveTheDoor();
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
      <Text style={styles.stopTypeLabel}>{stopTypeLabel(stop)}</Text>
      <Text style={styles.title}>{stopLabel(stop)}</Text>
      {stop.stop_type === 'dropoff' && stop.notes && <Text style={styles.notes}>{stop.notes}</Text>}

      <SyncStatusPill stopId={stopId} />

      {action.kind !== 'done' && (
        <View style={styles.navigateRow}>
          <Button label="Navigate" variant="outline" onPress={handleNavigate} />
        </View>
      )}

      {stop.stop_type === 'dropoff' && stop.contact_phone && (
        <>
          {/* The number as the sender gave it, so the driver can check what the
              dialer will show - and still has it when it can't be dialled. */}
          <Text style={styles.contactNumber}>
            {stop.contact_name ? `${stop.contact_name} · ` : ''}
            {stop.contact_phone}
          </Text>
          {dialable(stop.contact_phone) && (
            <View style={styles.contactRow}>
              <Button label="Call" variant="outline" onPress={() => contactRecipient('tel')} />
              <Button label="Text" variant="outline" onPress={() => contactRecipient('sms')} />
            </View>
          )}
        </>
      )}

      <Card style={styles.card}>
        {action.kind === 'arrive' && <Button label="Arrived" onPress={handleArrive} />}

        {action.kind === 'scan' && (
          <ParcelScanPanel
            scannedCount={stop.scanned_count}
            total={stop.parcel_count}
            onScanned={handleBarcode}
            onConfirmManually={handleScanNext}
          />
        )}

        {/* Cores before the completion controls, in that order and for the same
            reason money is: it is the thing that has to happen while the
            counter person is still standing there. A driver who confirms the
            stop first has said goodbye, and the core is still on the shelf.
            W1's endpoints have existed since PRs #13-#16 with no button at all
            (tests/test_no_unreachable_routes.py). */}
        <DockSurveyModal
          visible={surveying}
          stopId={stopId}
          visit={stop.stop_type === 'visit'}
          siteFacts={stop.site_facts ?? null}
          onClose={() => {
            setSurveying(false);
            navigation.navigate('Home');
          }}
        />
        <DockSurveyModal
          visible={askingFacts}
          stopId={stopId}
          factsOnly
          siteFacts={stop.site_facts ?? null}
          onClose={() => {
            setAskingFacts(false);
            navigation.navigate('Home');
          }}
        />

        {stop.returns && (
          <ReturnsPanel stop={stop} onDone={() => setLoadToken((t) => t + 1)} />
        )}

        {/* DE-5: before the confirmation, while the parts are on the counter.
            Optional - collection never waits on it. */}
        {action.kind === 'confirmDelivery' && stop.stop_type === 'pickup' && stop.order_ids.length > 0 && (
          <ItemFactsPanel stopId={stopId} orderIds={stop.order_ids} />
        )}

        {action.kind === 'confirmDelivery' && stop.stop_type === 'pickup' && (
          // "Confirm collection", not "Confirm delivery". This is the pickup
          // branch - the driver is at a shop loading the van, and nothing has
          // been delivered to anybody yet.
          <Button label="Confirm collection" onPress={handlePickupComplete} />
        )}

        {/* Money before proof, in that order on the screen, because it is the thing
            that has to happen while the customer is still standing there - and
            because the server refuses a completion with the cash unaccounted for
            (app/delivery/cod.py), so offering the proof controls first would set a
            driver up to be refused. */}
        {action.kind === 'confirmDelivery' && stop.stop_type === 'dropoff' && stop.cod.length > 0 && (
          <CodPanel stopId={stopId} obligations={stop.cod} onSettled={() => setLoadToken((t) => t + 1)} />
        )}

        {action.kind === 'confirmDelivery' && stop.stop_type === 'dropoff' && (
          <PodCapture
            stopId={stopId}
            proof={stop.proof}
            method={method}
            onChangeMethod={(m) => {
              setMethod(m);
              // Captures are NOT discarded on a method switch any more. A driver who
              // took two photos and then realised a signature was also needed would
              // otherwise lose the photos by tapping across.
              setPinError(null);
            }}
            photoUrls={photoUrls}
            signatureUrl={signatureUrl}
            onCapturePhoto={(url) => setPhotoUrls((prev) => [...prev, url])}
            onCaptureSignature={setSignatureUrl}
            onRemoveLastPhoto={() => setPhotoUrls((prev) => prev.slice(0, -1))}
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

        {action.kind === 'captureDoor' && (
          <>
            <Text style={styles.visitText}>
              Stand at the door a delivery would use, then capture it. Nothing is handed over at a visit.
            </Text>
            <Button label="At the door - capture it" onPress={handleVisitComplete} />
          </>
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
    navigateRow: { marginBottom: spacing.lg },
    contactRow: { flexDirection: 'row', gap: spacing.sm, marginBottom: spacing.lg },
    contactNumber: { ...typography.small, color: colors.textSecondary, marginBottom: spacing.sm },
    card: { marginBottom: spacing.lg, gap: spacing.sm },
    doneText: { ...typography.body, color: colors.textPrimary, textAlign: 'center' },
    visitText: { ...typography.small, color: colors.textSecondary },
    error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
  });
