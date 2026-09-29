import { useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { DockSurveyBody } from '../api/types';
import { outboxManager } from '../offline/outboxManager';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

/**
 * Eight taps at the door (docs/ROADMAP.md DRV-7).
 *
 * **The writer `IDN-4`'s surveyed columns never had.** `set_access` and
 * `set_autonomy_fit` were complete, validated and tested, and nothing called
 * them — they sat in the orphan allowlist as *"profile field with no live
 * writer"*. `MODEL_AND_DATA_BRIEF.md` §6 calls surveying weeks of fieldwork,
 * and it is for anybody who has to travel to the docks. Our drivers are already
 * standing at them.
 *
 * **It opens after the completion is queued, never before.** *A measurement may
 * fail; a delivery may not* (`THE_DRIVER_APP.md` §2). Dismissing costs nothing
 * and is never blocked, every question has a Skip, and the whole thing goes
 * through the outbox so answering it in a stockroom with no signal works
 * exactly as well as answering it on the street.
 *
 * **Whether to show it is not decided here.** The server sends
 * `dock_needs_survey` on the stop, computed from the interval, the per-shift
 * cap and the stop type together. Restating any of that on the phone is the
 * mistake behind both bugs `THE_DRIVER_APP.md` §6 records.
 *
 * **The answer codes are the server's vocabulary**, not a parallel one. A
 * mismatch is refused with a 422 naming the field rather than stored, because
 * these become `M5`'s labels and a stray value does not fail at write time — it
 * fails months later as a class the model has one example of.
 */

interface Question {
  key: keyof DockSurveyBody;
  prompt: string;
  options: { value: string; label: string }[];
}

const QUESTIONS: Question[] = [
  {
    key: 'stop_point',
    prompt: 'Where could you stop the vehicle?',
    options: [
      { value: 'loading_dock', label: 'Loading dock' },
      { value: 'marked_bay', label: 'Marked bay' },
      { value: 'lot', label: 'Car park' },
      { value: 'street_legal', label: 'Street, legally' },
      { value: 'double_parked', label: 'Had to double-park' },
      { value: 'none_legal', label: 'Nowhere legal' },
    ],
  },
  {
    key: 'curb_access',
    prompt: 'Could a vehicle pull up at the kerb by the door?',
    options: [
      { value: 'direct', label: 'Right at the door' },
      { value: 'short_walk', label: 'Short walk' },
      { value: 'long_walk', label: 'Long walk' },
      { value: 'no_curb', label: 'No kerb access' },
    ],
  },
  {
    key: 'walk_distance_band',
    prompt: 'How far from the vehicle to the handover?',
    options: [
      { value: 'at_vehicle', label: 'At the vehicle' },
      { value: 'under_20m', label: 'Under 20 m' },
      { value: 'under_100m', label: 'Under 100 m' },
      { value: 'over_100m', label: 'Over 100 m' },
    ],
  },
  {
    key: 'door_path',
    prompt: 'The way in to the door',
    options: [
      { value: 'ground_level', label: 'Ground level' },
      { value: 'steps', label: 'Steps' },
      { value: 'ramp', label: 'Ramp' },
      { value: 'loading_dock', label: 'Loading dock' },
      { value: 'freight_lift', label: 'Freight lift' },
    ],
  },
  {
    key: 'obstruction',
    prompt: 'Anything in the way?',
    options: [
      { value: 'none', label: 'Nothing' },
      { value: 'gate', label: 'Gate' },
      { value: 'security_desk', label: 'Security desk' },
      { value: 'narrow_access', label: 'Narrow access' },
      { value: 'overhead_limit', label: 'Height limit' },
    ],
  },
  {
    key: 'who_receives',
    prompt: 'Who took it?',
    options: [
      { value: 'anyone', label: 'Anyone there' },
      { value: 'named_person', label: 'A named person' },
      { value: 'counter_staff', label: 'Counter staff' },
      { value: 'dock_crew', label: 'Dock crew' },
      { value: 'unattended_ok', label: 'Could be left' },
    ],
  },
  {
    key: 'landing_surface',
    prompt: 'Open ground nearby something could set down on?',
    options: [
      { value: 'paved_lot', label: 'Paved area' },
      { value: 'gravel', label: 'Gravel' },
      { value: 'grass', label: 'Grass' },
      { value: 'street_only', label: 'Street only' },
      { value: 'rooftop', label: 'Rooftop' },
      { value: 'none', label: 'Nothing open' },
    ],
  },
];

export function DockSurveyModal({
  visible,
  stopId,
  onClose,
}: {
  visible: boolean;
  stopId: string;
  onClose: () => void;
}) {
  const colors = useThemeColors();
  const styles = makeStyles(colors);
  const [answers, setAnswers] = useState<DockSurveyBody>({});
  const [busy, setBusy] = useState(false);

  function choose(key: keyof DockSurveyBody, value: string) {
    setAnswers((prev) => ({ ...prev, [key]: prev[key] === value ? null : value }));
  }

  async function send() {
    setBusy(true);
    try {
      // Through the outbox, like every other stop event. A dock is surveyed in
      // a stockroom about as often as a parcel is delivered in one.
      await outboxManager.enqueue('survey', stopId, answers as Record<string, unknown>);
      onClose();
    } finally {
      setBusy(false);
    }
  }

  const answered = Object.values(answers).filter((v) => v !== null && v !== undefined).length;

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={styles.screen}>
        <View style={styles.header}>
          <Text style={styles.title}>About this dock</Text>
          <Text style={styles.subtitle}>
            Eight taps, once a year. Skip anything you did not see — a blank answer is
            better than a guess.
          </Text>
        </View>

        <ScrollView contentContainerStyle={styles.body}>
          {QUESTIONS.map((question) => (
            <View key={question.key as string} style={styles.question}>
              <Text style={styles.prompt}>{question.prompt}</Text>
              <View style={styles.options}>
                {question.options.map((option) => {
                  const chosen = answers[question.key] === option.value;
                  return (
                    <Pressable
                      key={option.value}
                      onPress={() => choose(question.key, option.value)}
                      style={[styles.option, chosen && styles.optionChosen]}
                    >
                      <Text style={[styles.optionLabel, chosen && styles.optionLabelChosen]}>
                        {option.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>
          ))}

          <View style={styles.question}>
            <Text style={styles.prompt}>Did you need an appointment or booking?</Text>
            <View style={styles.options}>
              {[
                { value: true, label: 'Yes' },
                { value: false, label: 'No' },
              ].map((option) => {
                const chosen = answers.appointment_required === option.value;
                return (
                  <Pressable
                    key={String(option.value)}
                    onPress={() =>
                      setAnswers((prev) => ({
                        ...prev,
                        appointment_required: chosen ? null : option.value,
                      }))
                    }
                    style={[styles.option, chosen && styles.optionChosen]}
                  >
                    <Text style={[styles.optionLabel, chosen && styles.optionLabelChosen]}>
                      {option.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
        </ScrollView>

        <View style={styles.footer}>
          {/* Dismissing is as easy as sending, and says so. The alternative is a
              driver who answers carelessly to get rid of it, which is worse
              than no answers at all. */}
          <Pressable disabled={busy} onPress={onClose} style={[styles.secondary, styles.grow]}>
            <Text style={styles.secondaryLabel}>Not now</Text>
          </Pressable>
          <Pressable
            disabled={busy || answered === 0}
            onPress={send}
            style={[styles.primary, styles.grow, (busy || answered === 0) && styles.disabled]}
          >
            <Text style={styles.primaryLabel}>
              {answered === 0 ? 'Nothing answered' : `Send ${answered}`}
            </Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

function makeStyles(colors: ColorScheme) {
  return StyleSheet.create({
    screen: { flex: 1, backgroundColor: colors.bg },
    header: { padding: spacing.lg, gap: spacing.xs },
    title: { ...typography.title, color: colors.textPrimary },
    subtitle: { ...typography.small, color: colors.textMuted },
    body: { padding: spacing.lg, paddingTop: 0, gap: spacing.lg },
    question: { gap: spacing.sm },
    prompt: { ...typography.label, color: colors.textPrimary },
    options: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
    option: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: radius.sm,
      paddingVertical: spacing.sm,
      paddingHorizontal: spacing.md,
      backgroundColor: colors.surface,
    },
    optionChosen: { backgroundColor: colors.primary, borderColor: colors.primary },
    optionLabel: { ...typography.body, color: colors.textPrimary },
    optionLabelChosen: { color: colors.primaryText },
    footer: {
      flexDirection: 'row',
      gap: spacing.sm,
      padding: spacing.lg,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    grow: { flex: 1 },
    primary: {
      backgroundColor: colors.primary,
      borderRadius: radius.sm,
      paddingVertical: spacing.md,
      alignItems: 'center',
    },
    primaryLabel: { ...typography.label, color: colors.primaryText },
    secondary: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: radius.sm,
      paddingVertical: spacing.md,
      alignItems: 'center',
    },
    secondaryLabel: { ...typography.label, color: colors.textPrimary },
    disabled: { opacity: 0.4 },
  });
}
