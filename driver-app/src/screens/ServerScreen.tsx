import { useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import {
  describeProblem,
  getApiBaseUrl,
  getBuildTimeApiBaseUrl,
  setApiBaseUrl,
} from '../api/serverUrl';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

/**
 * Which LMX OS this install talks to.
 *
 * The build-time default is `http://localhost:8000`, which is right on a
 * simulator and useless on a handset - there, localhost is the handset. The
 * first real Android build installed, launched, and could reach nothing, and
 * rebuilding for every network change is seven minutes a time.
 *
 * Shown to everyone rather than hidden behind a debug flag: pointing a real
 * install at staging is a thing anyone running a pilot needs, and a setting
 * that only exists in development is a setting that breaks the first time it
 * matters.
 */
export function ServerScreen() {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [value, setValue] = useState(getApiBaseUrl());
  const [saved, setSaved] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function handleSave() {
    const complaint = describeProblem(value);
    setProblem(complaint);
    if (complaint) return;
    await setApiBaseUrl(value);
    setSaved(getApiBaseUrl());
  }

  async function handleReset() {
    await setApiBaseUrl('');
    setValue(getApiBaseUrl());
    setProblem(null);
    setSaved(getApiBaseUrl());
  }

  return (
    <ScreenContainer>
      <View style={styles.content}>
        <Text style={styles.body}>
          The address of the LMX OS this app talks to. Change it to test against a
          different server.
        </Text>

        <TextField
          label="Server address"
          value={value}
          onChangeText={(next) => {
            setValue(next);
            setProblem(null);
            setSaved(null);
          }}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          placeholder="http://192.168.1.5:8000"
        />

        {problem ? <Text style={styles.problem}>{problem}</Text> : null}
        {saved ? <Text style={styles.saved}>Now using {saved}</Text> : null}

        <View style={styles.actions}>
          <Button label="Use default" variant="outline" onPress={handleReset} />
          <Button label="Save" onPress={handleSave} />
        </View>

        <View style={styles.note}>
          <Text style={styles.noteLabel}>Built-in default</Text>
          <Text style={styles.noteBody}>{getBuildTimeApiBaseUrl()}</Text>
          <Text style={styles.noteBody}>
            On a phone, localhost means the phone itself - use your computer&apos;s
            address on the network, or a public URL.
          </Text>
        </View>
      </View>
    </ScreenContainer>
  );
}

function makeStyles(colors: ColorScheme) {
  return StyleSheet.create({
    content: { padding: spacing.lg, gap: spacing.lg },
    body: { ...typography.body, color: colors.textSecondary, lineHeight: 22 },
    problem: { ...typography.small, color: colors.danger },
    saved: { ...typography.small, color: colors.success },
    actions: { flexDirection: 'row', gap: spacing.md, justifyContent: 'flex-end' },
    note: {
      backgroundColor: colors.surfaceAlt,
      borderRadius: radius.md,
      padding: spacing.lg,
      gap: spacing.xs,
    },
    noteLabel: { ...typography.label, color: colors.textPrimary },
    noteBody: { ...typography.small, color: colors.textMuted, lineHeight: 18 },
  });
}
