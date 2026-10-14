import { useEffect, useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import type { GigJob, GigPlatform, ItemSizeClass } from '../api/types';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { DockSurveyModal } from '../components/DockSurveyModal';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import { currentPin } from '../location/currentPin';
import type { HomeStackParamList } from '../navigation/types';
import {
  itemFacts,
  jobIntake,
  loadDrafts,
  newDraft,
  pinBody,
  recordBody,
  removeDraft,
  saveDraft,
} from '../offline/offPlatformRecord';
import type { ItemForm, JobForm, OffPlatformDraft } from '../offline/offPlatformRecord';
import { outboxManager } from '../offline/outboxManager';
import type { OutboxItem } from '../offline/types';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

type Props = NativeStackScreenProps<HomeStackParamList, 'OffPlatform'>;

const PLATFORMS: { code: GigPlatform; label: string }[] = [
  { code: 'curri', label: 'Curri' },
  { code: 'dispatch', label: 'Dispatch' },
  { code: 'roadie', label: 'Roadie' },
];

const SIZES: { code: ItemSizeClass; label: string }[] = [
  { code: 'envelope', label: 'Envelope' },
  { code: 'small', label: 'Shoebox' },
  { code: 'medium', label: 'Carry-on' },
  { code: 'large', label: 'Car boot' },
  { code: 'oversize', label: 'Van only' },
];

/**
 * Log a delivery made for a gig platform as a record (docs/ROADMAP_1.5.md DE-7).
 *
 * The short form, built from screens the app already has: the job (picked from
 * the driver's gig jobs, or typed), the item at the counter as `DE-5` asks it
 * plus what it is, and the door at the drop-off as `DE-4` captures it - the
 * pins taken by the phone, the questions and four photos. The pickup counter is
 * optional. Nothing here is LMX work: it is not on the route, not paid by the
 * delivery, and the platform's own app is still where the job is closed.
 *
 * Every send goes through the outbox, so a counter and a door with no signal
 * lose nothing; the draft keeps the screen's place until the door is logged.
 */
export function OffPlatformRecordScreen({ navigation, route }: Props) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [draft, setDraft] = useState<OffPlatformDraft | null>(null);
  const [jobs, setJobs] = useState<GigJob[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [surveying, setSurveying] = useState<'counter' | 'door' | null>(null);
  // Known false once the server says the pickup address named no place: then
  // there is no counter to capture, and it is not offered.
  const [noCounter, setNoCounter] = useState(false);
  const [refused, setRefused] = useState<OutboxItem[]>([]);
  const driverId = useAuth().profile?.driver_id ?? null;

  useEffect(() => {
    if (!driverId) return;
    let live = true;
    const wanted = route.params?.recordId;
    void (async () => {
      const kept = wanted ? (await loadDrafts(driverId)).find((d) => d.recordId === wanted) : undefined;
      if (live) setDraft(kept ?? newDraft(driverId));
    })();
    // Offline this fails and the job is typed instead.
    api
      .myGigJobs()
      // Only jobs that can become a record: not logged yet, not declined or
      // cancelled, and with a drop-off address - the server refuses one without.
      .then(
        (all) =>
          live &&
          setJobs(
            all.filter(
              (j) =>
                !j.is_logged_as_record &&
                j.dropoff_address &&
                ['offered', 'accepted', 'picked_up', 'delivered'].includes(j.status),
            ),
          ),
      )
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [route.params?.recordId, driverId]);

  // What the server refused for this record, so the driver can read why and
  // discard the log rather than be stuck with it.
  const recordId = draft?.recordId;
  const pickupStopId = draft?.pickupStopId;
  useEffect(
    () =>
      outboxManager.subscribe((items) =>
        setRefused(
          items.filter((i) => i.permanentlyFailed && (i.stopId === recordId || i.stopId === pickupStopId)),
        ),
      ),
    [recordId, pickupStopId],
  );

  if (!draft) return <ScreenContainer>{null}</ScreenContainer>;

  async function update(next: OffPlatformDraft) {
    setDraft(next);
    await saveDraft(next);
  }

  /** The job named, as this send will carry it - or what is still missing. */
  function theJob() {
    if (!draft) return 'No record';
    if (draft.gigJobId) return null;
    return jobIntake(draft.form);
  }

  async function send(extra: Parameters<typeof recordBody>[2], next: Partial<OffPlatformDraft>) {
    if (!draft) return;
    const job = theJob();
    if (typeof job === 'string') {
      setProblem(job);
      return;
    }
    const facts = itemFacts(draft.item);
    if (typeof facts === 'string') {
      setProblem(facts);
      return;
    }
    setProblem(null);
    setBusy(true);
    try {
      const body = recordBody(draft, job, { item: facts, ...extra });
      await outboxManager.enqueue('off-platform-record', draft.recordId, body as unknown as Record<string, unknown>);
      await update({ ...draft, ...next, sent: true, jobLabel: draft.jobLabel || draft.form.ref });
    } finally {
      setBusy(false);
    }
  }

  async function parked(end: 'pickup' | 'dropoff') {
    const pin = pinBody(await currentPin());
    if (!pin) {
      setProblem('No location fix yet - try again in a moment.');
      return;
    }
    await send({ [end]: { stop_pin: pin } }, {});
  }

  async function atCounter() {
    if (draft?.pickedUpAt) {
      // Saving a correction to the item, maybe from the door: the counter's
      // time and pin stay what they were when it was collected.
      await send({}, {});
      return;
    }
    const pin = pinBody(await currentPin());
    const at = new Date().toISOString();
    await send({ pickup: { ...(pin ? { door_pin: pin } : {}), at } }, { pickedUpAt: at });
  }

  async function captureCounter() {
    if (!draft) return;
    try {
      const record = await api.getOffPlatformRecord(draft.recordId);
      if (!record.counter_resolved) {
        setNoCounter(true);
        return;
      }
    } catch {
      // Offline, or not landed yet: offered anyway, and the server refuses it
      // there if the counter turns out to have no place.
    }
    setSurveying('counter');
  }

  async function discard() {
    if (!draft) return;
    for (const item of refused) await outboxManager.dismiss(item.id);
    await removeDraft(draft.recordId);
    navigation.goBack();
  }

  async function delivered() {
    const pin = pinBody(await currentPin());
    const at = new Date().toISOString();
    await send({ dropoff: { ...(pin ? { door_pin: pin } : {}), at } }, { deliveredAt: at });
    setSurveying('door');
  }

  async function done() {
    if (!draft) return;
    await removeDraft(draft.recordId);
    navigation.goBack();
  }

  const form = draft.form;
  const setForm = (patch: Partial<JobForm>) => void update({ ...draft, form: { ...form, ...patch } });
  const setItem = (patch: Partial<ItemForm>) => void update({ ...draft, item: { ...draft.item, ...patch } });

  return (
    <ScreenContainer>
      <Text style={styles.body}>
        A job you did for Curri, Dispatch or Roadie, recorded the way we record our own deliveries. Close it in the
        platform's app as usual. A bonus is recorded for each job you log; it is paid once payouts are set up.
      </Text>

      <Card style={styles.card}>
        <Text style={styles.section}>The job</Text>
        {draft.sent || draft.gigJobId ? (
          <View style={styles.row}>
            <Text style={styles.body}>{draft.jobLabel}</Text>
            {!draft.sent && (
              <Chip label="Type it instead" active={false} styles={styles} onPress={() => void update({ ...draft, gigJobId: null, jobLabel: '' })} />
            )}
          </View>
        ) : (
          <>
            {jobs.length > 0 && !draft.pickedUpAt && (
              <>
                <Text style={styles.fieldLabel}>One of your jobs</Text>
                <View style={styles.chips}>
                  {jobs.map((job) => (
                    <Chip
                      key={job.gig_job_id}
                      label={`${job.platform_job_ref} · ${job.dropoff_address}`}
                      active={false}
                      styles={styles}
                      onPress={() => void update({ ...draft, gigJobId: job.gig_job_id, jobLabel: job.platform_job_ref })}
                    />
                  ))}
                </View>
              </>
            )}
            <Text style={styles.fieldLabel}>Platform</Text>
            <View style={styles.chips}>
              {PLATFORMS.map((p) => (
                <Chip key={p.code} label={p.label} active={form.platform === p.code} styles={styles} onPress={() => setForm({ platform: p.code })} />
              ))}
            </View>
            <TextField label="Job number on the platform" value={form.ref} onChangeText={(ref) => setForm({ ref })} />
            <TextField label="Pickup address" value={form.pickupAddress} onChangeText={(pickupAddress) => setForm({ pickupAddress })} />
            <TextField label="Drop-off address" value={form.dropoffAddress} onChangeText={(dropoffAddress) => setForm({ dropoffAddress })} />
            <View style={styles.row}>
              <View style={styles.grow}>
                <TextField label="Pickup from (HH:MM)" value={form.pickupFrom} onChangeText={(pickupFrom) => setForm({ pickupFrom })} />
              </View>
              <View style={styles.grow}>
                <TextField label="Pickup to" value={form.pickupTo} onChangeText={(pickupTo) => setForm({ pickupTo })} />
              </View>
            </View>
            <View style={styles.row}>
              <View style={styles.grow}>
                <TextField label="Deliver by (if stated)" value={form.deliverBy} onChangeText={(deliverBy) => setForm({ deliverBy })} />
              </View>
              <View style={styles.grow}>
                <TextField label="Pay ($)" value={form.pay} onChangeText={(pay) => setForm({ pay })} keyboardType="decimal-pad" />
              </View>
            </View>
          </>
        )}
      </Card>

      <Card style={styles.card}>
        <Text style={styles.section}>At the pickup counter</Text>
        <TextField
          label="What is it?"
          value={draft.item.description}
          onChangeText={(description) => setItem({ description })}
          placeholder="e.g. brake rotors, boxed"
        />
        <TextField
          label="Weight (kg)"
          value={draft.item.weightKg}
          onChangeText={(weightKg) => setItem({ weightKg })}
          keyboardType="decimal-pad"
          placeholder="unknown"
        />
        <Text style={styles.fieldLabel}>Size</Text>
        <View style={styles.chips}>
          {SIZES.map((s) => (
            <Chip key={s.code} label={s.label} active={draft.item.size === s.code} styles={styles} onPress={() => setItem({ size: draft.item.size === s.code ? null : s.code })} />
          ))}
        </View>
        <Text style={styles.fieldLabel}>Hazardous or liquid?</Text>
        <View style={styles.chips}>
          <Chip label="Yes" active={draft.item.hazmat === true} styles={styles} onPress={() => setItem({ hazmat: draft.item.hazmat === true ? null : true })} />
          <Chip label="No" active={draft.item.hazmat === false} styles={styles} onPress={() => setItem({ hazmat: draft.item.hazmat === false ? null : false })} />
        </View>
        <Button label="I've parked here" variant="outline" onPress={() => void parked('pickup')} disabled={busy} />
        <View style={styles.gap} />
        <Button
          label={draft.pickedUpAt ? 'Collected ✓ - save changes' : "I'm at the counter - collected"}
          onPress={() => void atCounter()}
          loading={busy}
        />
        {draft.pickedUpAt && !noCounter && (
          <>
            <View style={styles.gap} />
            <Button label="Capture the counter (optional)" variant="outline" onPress={() => void captureCounter()} />
          </>
        )}
        {noCounter && (
          <Text style={styles.body}>The pickup address didn't match a place, so there's no counter to capture.</Text>
        )}
      </Card>

      <Card style={styles.card}>
        <Text style={styles.section}>At the door</Text>
        <Button label="I've parked here" variant="outline" onPress={() => void parked('dropoff')} disabled={busy} />
        <View style={styles.gap} />
        <Button
          label={draft.deliveredAt ? 'Delivered ✓ - capture the door' : 'Delivered - at the door now'}
          onPress={() => void (draft.deliveredAt ? setSurveying('door') : delivered())}
          loading={busy}
        />
      </Card>

      {problem && <Text style={styles.problem}>{problem}</Text>}
      {refused.length > 0 && (
        <>
          <Text style={styles.problem}>Not accepted: {refused[0].lastError}</Text>
          <Button label="Discard this log" variant="outline" onPress={() => void discard()} />
          <View style={styles.gap} />
        </>
      )}
      {draft.deliveredAt && <Button label="Done" variant="outline" onPress={() => void done()} />}

      <DockSurveyModal
        visible={surveying === 'door'}
        stopId={draft.recordId}
        onClose={() => setSurveying(null)}
      />
      <DockSurveyModal
        visible={surveying === 'counter'}
        stopId={draft.pickupStopId}
        atPickup
        queueAfter={draft.recordId}
        onClose={() => setSurveying(null)}
      />
    </ScreenContainer>
  );
}

function Chip({
  label,
  active,
  onPress,
  styles,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
  styles: ReturnType<typeof makeStyles>;
}) {
  return (
    <Pressable accessibilityRole="button" onPress={onPress} style={[styles.chip, active && styles.chipActive]}>
      <Text style={[styles.chipLabel, active && styles.chipLabelActive]}>{label}</Text>
    </Pressable>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    title: { ...typography.title, color: colors.textPrimary, marginBottom: spacing.sm },
    body: { ...typography.body, color: colors.textSecondary, marginBottom: spacing.md },
    card: { marginBottom: spacing.md },
    section: { ...typography.label, color: colors.textPrimary, marginBottom: spacing.md },
    fieldLabel: { ...typography.small, color: colors.textSecondary, marginBottom: spacing.sm },
    row: { flexDirection: 'row', gap: spacing.sm, alignItems: 'center' },
    grow: { flex: 1 },
    gap: { height: spacing.sm },
    problem: { ...typography.body, color: colors.danger, marginBottom: spacing.md },
    chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.md },
    chip: {
      borderWidth: 1,
      borderColor: colors.borderStrong,
      borderRadius: radius.md,
      paddingVertical: spacing.sm,
      paddingHorizontal: spacing.md,
      backgroundColor: colors.surface,
    },
    chipActive: { borderColor: colors.primary, backgroundColor: colors.accentDim },
    chipLabel: { ...typography.small, color: colors.textSecondary },
    chipLabelActive: { color: colors.primary, fontFamily: 'PlusJakartaSans_700Bold' },
  });
