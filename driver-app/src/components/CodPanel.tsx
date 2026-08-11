import { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { api, ApiError } from '../api/client';
import type { CodMethod, CodObligation } from '../api/types';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { TextField } from './TextField';

interface CodPanelProps {
  stopId: string;
  obligations: CodObligation[];
  onSettled: () => void;
}

function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/**
 * Cash on delivery, at the door (docs/ROADMAP.md W2, story DO-8).
 *
 * The driver rule is *"never negotiate, one tap escalates to the distributor, keep
 * moving"*, and the roadmap is explicit that it must be enforced by the UI rather
 * than by training. **This screen enforces it by an absence: there is no field to
 * type an amount into.** The figure comes off the order, so "collected" can only
 * mean all of it.
 *
 * That is not paternalism about drivers. The money is the DISTRIBUTOR'S invoice to
 * their own customer — nobody at LMX has authority to discount it, so a box
 * accepting eighty against a hundred would hand a driver an authority nobody gave
 * them and leave them arguing at a door on someone else's behalf. Which is exactly
 * the situation the rule exists to get them out of.
 *
 * The dispute path names who gets told, and tells them to leave.
 */
export function CodPanel({ stopId, obligations, onSettled }: CodPanelProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [codMethod, setCodMethod] = useState<CodMethod>('cash');
  const [disputing, setDisputing] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const outstanding = obligations.filter((o) => !o.settled);
  const totalDue = outstanding.reduce((sum, o) => sum + o.amount_due_cents, 0);

  // Settled covers a dispute as well as a payment: the rule is "keep moving", so
  // once escalated the driver is not held at the door waiting for someone else to
  // resolve it.
  if (outstanding.length === 0) {
    const wasDisputed = obligations.some((o) => o.outcome === 'disputed');
    return (
      <View style={[styles.panel, wasDisputed ? styles.panelDisputed : styles.panelDone]}>
        <Text style={[styles.title, wasDisputed && styles.titleDisputed]}>
          {wasDisputed ? 'Payment disputed — LMX has told the shop' : 'Payment collected'}
        </Text>
        <Text style={styles.body}>
          {wasDisputed
            ? 'Nothing more to do here. Complete the stop and move on.'
            : `${money(obligations.reduce((s, o) => s + o.amount_due_cents, 0))} — you can complete the stop.`}
        </Text>
      </View>
    );
  }

  async function handleCollect() {
    setBusy(true);
    setError(null);
    try {
      await api.collectCod(stopId, codMethod);
      onSettled();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record that — try again.');
    } finally {
      setBusy(false);
    }
  }

  async function handleDispute() {
    setBusy(true);
    setError(null);
    try {
      await api.raiseCodDispute(stopId, note.trim() || undefined);
      onSettled();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record that — try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.panel}>
      <Text style={styles.title}>Collect payment</Text>

      <View style={styles.amountBlock}>
        <Text style={styles.amountLabel}>Amount due</Text>
        <Text style={styles.amount}>{money(totalDue)}</Text>
        {/* Said out loud, because it is the reason there is no other option here. */}
        <Text style={styles.amountHint}>Set by the shop. You can&apos;t change it.</Text>
      </View>

      {!disputing ? (
        <>
          <Text style={styles.fieldLabel}>How did they pay?</Text>
          <View style={styles.segmentRow}>
            {(['cash', 'check'] as CodMethod[]).map((m) => (
              <Pressable
                key={m}
                onPress={() => setCodMethod(m)}
                style={[styles.segment, codMethod === m && styles.segmentActive]}
              >
                <Text style={[styles.segmentLabel, codMethod === m && styles.segmentLabelActive]}>
                  {m === 'cash' ? 'Cash' : 'Check'}
                </Text>
              </Pressable>
            ))}
          </View>

          {busy ? (
            <ActivityIndicator color={colors.textMuted} style={styles.spinner} />
          ) : (
            <Pressable onPress={handleCollect} style={styles.collect}>
              <Text style={styles.collectLabel}>Collected in full</Text>
            </Pressable>
          )}

          <View style={styles.disputeBlock}>
            <Text style={styles.body}>Won&apos;t pay, or disputing the amount?</Text>
            <Pressable onPress={() => setDisputing(true)} style={styles.disputeButton} disabled={busy}>
              <Text style={styles.disputeLabel}>Flag a payment dispute</Text>
            </Pressable>
            <Text style={styles.amountHint}>
              We&apos;ll tell the shop straight away. Don&apos;t negotiate — move on to your next stop.
            </Text>
          </View>
        </>
      ) : (
        <>
          <TextField
            label="What did they say? (optional)"
            placeholder="Says he was quoted 90"
            value={note}
            onChangeText={setNote}
            maxLength={500}
          />
          {/* Free text on purpose: the useful signal is a pattern across an account,
              and a dropdown written now would decide in advance which patterns can
              be seen. */}
          {busy ? (
            <ActivityIndicator color={colors.textMuted} style={styles.spinner} />
          ) : (
            <>
              <Pressable onPress={handleDispute} style={styles.disputeButton}>
                <Text style={styles.disputeLabel}>Send it to the shop</Text>
              </Pressable>
              <Pressable onPress={() => setDisputing(false)} style={styles.cancel}>
                <Text style={styles.cancelLabel}>They paid after all</Text>
              </Pressable>
            </>
          )}
        </>
      )}

      {error ? <Text style={styles.errorText}>{error}</Text> : null}
    </View>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    panel: {
      backgroundColor: colors.surfaceAlt,
      borderColor: colors.borderStrong,
      borderWidth: 1,
      borderRadius: radius.md,
      padding: spacing.md,
      marginBottom: spacing.md,
    },
    panelDone: { backgroundColor: colors.successDim, borderColor: colors.success },
    panelDisputed: { backgroundColor: colors.dangerDim, borderColor: colors.danger },
    title: { ...typography.body, color: colors.textPrimary, fontWeight: '700' },
    titleDisputed: { color: colors.danger },
    body: { ...typography.small, color: colors.textSecondary },
    amountBlock: { alignItems: 'center', paddingVertical: spacing.md },
    amountLabel: { ...typography.small, color: colors.textMuted },
    amount: { fontSize: 34, fontWeight: '700', color: colors.textPrimary, letterSpacing: -0.5 },
    amountHint: { ...typography.small, color: colors.textMuted, textAlign: 'center' },
    fieldLabel: { ...typography.small, color: colors.textSecondary, marginBottom: spacing.sm },
    segmentRow: { flexDirection: 'row', gap: spacing.sm, marginBottom: spacing.md },
    segment: {
      flex: 1,
      borderWidth: 1,
      borderColor: colors.borderStrong,
      borderRadius: radius.md,
      paddingVertical: spacing.md,
      alignItems: 'center',
      backgroundColor: colors.surface,
    },
    segmentActive: { borderColor: colors.primary, backgroundColor: colors.accentDim },
    segmentLabel: { ...typography.body, color: colors.textSecondary },
    segmentLabelActive: { color: colors.primary, fontWeight: '700' },
    collect: {
      backgroundColor: colors.primary,
      borderRadius: radius.md,
      paddingVertical: spacing.md,
      alignItems: 'center',
    },
    collectLabel: { ...typography.body, color: colors.primaryText, fontWeight: '700' },
    disputeBlock: {
      marginTop: spacing.lg,
      paddingTop: spacing.md,
      borderTopWidth: 1,
      borderTopColor: colors.border,
      gap: spacing.sm,
    },
    disputeButton: {
      backgroundColor: colors.dangerDim,
      borderColor: colors.danger,
      borderWidth: 1,
      borderRadius: radius.md,
      paddingVertical: spacing.md,
      alignItems: 'center',
    },
    disputeLabel: { ...typography.body, color: colors.danger, fontWeight: '700' },
    cancel: { paddingVertical: spacing.md, alignItems: 'center' },
    cancelLabel: { ...typography.small, color: colors.textSecondary },
    spinner: { marginVertical: spacing.md },
    errorText: { color: colors.danger, marginTop: spacing.sm, fontSize: 13 },
  });
