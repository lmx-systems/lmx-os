import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { Stop } from '../api/types';
import { outboxManager } from '../offline/outboxManager';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { returnsActionForStop } from '../utils/stopReturns';
import { TextField } from './TextField';

interface ReturnsPanelProps {
  stop: Stop;
  onDone: () => void;
}

/**
 * Cores, at the counter (docs/ROADMAP.md W1).
 *
 * **`W1` reads "Done (PRs #13–#16)" and the driver never got a button.** The
 * three endpoints have existed since then — collect, not-ready, drop back at
 * the shop — and `StopView` did not carry a returns field, so the app could not
 * have known when to show one even if somebody had drawn it. A counter person
 * could flag cores ready and the driver who arrived to collect them had nothing
 * to press. Found by `tests/test_no_unreachable_routes.py`.
 *
 * **Everything here goes through the outbox**, like every other stop event. A
 * core is collected in a stockroom with no signal about as often as a parcel is
 * delivered in one, and "the button did nothing" is how a driver learns to stop
 * pressing it (`applyOptimistic.test.ts` — that regression was the same shape).
 *
 * **The dropoff and pickup halves are different jobs**, which is why this takes
 * `stopType` rather than inferring from which array is non-empty. At a dropoff
 * the driver is *taking* a core away; at a pickup they are *handing it back*.
 * Only one is ever possible at one stop, and the server enforces that — a
 * collect on a pickup is a 409 — so rendering both would be offering a refusal.
 *
 * **"Not ready" is as prominent as "Collected"** and deliberately not hidden
 * behind a confirmation. It is the honest outcome roughly as often, and the
 * alternative to making it easy is a driver tapping *Collected* on a core they
 * do not have, which puts a phantom part in the record and a real argument at
 * the shop three days later.
 */
export function ReturnsPanel({ stop, onDone }: ReturnsPanelProps) {
  const colors = useThemeColors();
  const styles = makeStyles(colors);
  const [busy, setBusy] = useState(false);
  const [adhoc, setAdhoc] = useState('');
  const [addingAdhoc, setAddingAdhoc] = useState(false);

  const stopId = stop.stop_id;
  // The decision lives in `stopReturns.ts` and is tested there. Restating it
  // here is what produced both bugs in `applyOptimistic.test.ts`.
  const action = returnsActionForStop(stop);

  async function queue(type: 'collect-return' | 'return-not-ready' | 'return-to-shop', payload = {}) {
    setBusy(true);
    try {
      await outboxManager.enqueue(type, stopId, payload);
      onDone();
    } finally {
      setBusy(false);
    }
  }

  if (action.kind === 'none') return null;

  if (action.kind === 'drop') {
    const toDrop = action.manifests;
    return (
      <View style={styles.card}>
        <Text style={styles.heading}>
          {toDrop.length === 1 ? '1 core to drop here' : `${toDrop.length} cores to drop here`}
        </Text>
        {toDrop.map((manifest, index) => (
          <Text key={`${manifest}-${index}`} style={styles.manifest}>
            {manifest}
          </Text>
        ))}
        <Pressable
          disabled={busy}
          onPress={() => queue('return-to-shop')}
          style={[styles.primary, busy && styles.disabled]}
        >
          <Text style={styles.primaryLabel}>Dropped at this shop</Text>
        </Pressable>
      </View>
    );
  }

  const expected = action.manifests;
  const canAct = action.enabled && !busy;

  return (
    <View style={styles.card}>
      <Text style={styles.heading}>
        {expected.length === 0
          ? 'Cores'
          : expected.length === 1
            ? '1 core expected back'
            : `${expected.length} cores expected back`}
      </Text>
      {expected.map((manifest, index) => (
        <Text key={`${manifest}-${index}`} style={styles.manifest}>
          {manifest}
        </Text>
      ))}

      {expected.length > 0 && (
        <View style={styles.row}>
          <Pressable
            disabled={!canAct}
            onPress={() => queue('collect-return')}
            style={[styles.primary, styles.grow, !canAct && styles.disabled]}
          >
            <Text style={styles.primaryLabel}>Collected</Text>
          </Pressable>
          <Pressable
            disabled={!canAct}
            onPress={() => queue('return-not-ready')}
            style={[styles.secondary, styles.grow, !canAct && styles.disabled]}
          >
            <Text style={styles.secondaryLabel}>Not ready</Text>
          </Pressable>
        </View>
      )}

      {!action.enabled && (
        <Text style={styles.manifest}>
          Tap Arrived first — the server will not record a collection before then.
        </Text>
      )}

      {/* A core nobody expected. The counter hands one over unannounced often
          enough that the backend has always accepted an ad-hoc manifest, and
          without this the driver's only options are to refuse it or carry an
          unrecorded part. */}
      {addingAdhoc ? (
        <View style={styles.adhoc}>
          <TextField
            label="What is it?"
            value={adhoc}
            onChangeText={setAdhoc}
            placeholder="core: alternator"
          />
          <View style={styles.row}>
            <Pressable
              disabled={!canAct || adhoc.trim().length === 0}
              onPress={() => {
                const manifest = adhoc.trim();
                setAdhoc('');
                setAddingAdhoc(false);
                void queue('collect-return', { manifest });
              }}
              style={[styles.primary, styles.grow, (!canAct || !adhoc.trim()) && styles.disabled]}
            >
              <Text style={styles.primaryLabel}>Take it</Text>
            </Pressable>
            <Pressable
              disabled={busy}
              onPress={() => {
                setAdhoc('');
                setAddingAdhoc(false);
              }}
              style={[styles.secondary, styles.grow]}
            >
              <Text style={styles.secondaryLabel}>Cancel</Text>
            </Pressable>
          </View>
        </View>
      ) : (
        <Pressable disabled={!canAct} onPress={() => setAddingAdhoc(true)} style={styles.link}>
          <Text style={styles.linkLabel}>They have another core for me</Text>
        </Pressable>
      )}
    </View>
  );
}

function makeStyles(colors: ColorScheme) {
  return StyleSheet.create({
    card: {
      backgroundColor: colors.surface,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: colors.border,
      padding: spacing.md,
      gap: spacing.sm,
    },
    heading: { ...typography.label, color: colors.textPrimary },
    manifest: { ...typography.body, color: colors.textMuted },
    row: { flexDirection: 'row', gap: spacing.sm },
    grow: { flex: 1 },
    primary: {
      backgroundColor: colors.primary,
      borderRadius: radius.sm,
      paddingVertical: spacing.sm,
      alignItems: 'center',
    },
    primaryLabel: { ...typography.label, color: colors.primaryText },
    secondary: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: radius.sm,
      paddingVertical: spacing.sm,
      alignItems: 'center',
    },
    secondaryLabel: { ...typography.label, color: colors.textPrimary },
    disabled: { opacity: 0.4 },
    adhoc: { gap: spacing.sm },
    link: { paddingVertical: spacing.xs },
    linkLabel: { ...typography.small, color: colors.textMuted, textDecorationLine: 'underline' },
  });
}
