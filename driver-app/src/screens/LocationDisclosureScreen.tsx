import { useMemo, useState } from 'react';
import * as Location from 'expo-location';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

/**
 * What the app records at a stop, shown before the "always" permission is ever
 * requested (docs/BACKGROUND_LOCATION_CONSENT.md §4.4).
 *
 * **Google Play requires this.** A prominent in-app disclosure before the
 * runtime prompt is a condition of the background-location declaration, not a
 * nicety - an app that goes straight to the system dialog fails review. Apple
 * does not require it, but a cold "always" prompt is the most common reason
 * drivers refuse, so the same screen serves both.
 *
 * **The copy is the copy.** It matches §4.4 and, deliberately, the App Store
 * review justification and the pilot contract clause - the whole point of
 * writing 0.4 and 0.8 as one document was that a reviewer, a lawyer and a
 * driver should not be told three different things. Change it here and the
 * other two need changing in the same commit.
 *
 * The last paragraph - that this can be turned off and the app keeps working -
 * is load-bearing and should survive review unedited. A permission a driver
 * cannot decline is a permission a labour regulator reads as control, which
 * cuts across `A10`'s classification work.
 */
export function LocationDisclosureScreen({
  onDecided,
}: {
  onDecided: (granted: boolean) => void;
}) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [asking, setAsking] = useState(false);

  async function handleContinue() {
    setAsking(true);
    try {
      // Foreground first, then background. Both platforms want the escalation
      // rather than the cold ask, and on iOS the "always" tier cannot be
      // requested at all until when-in-use has been granted.
      const foreground = await Location.requestForegroundPermissionsAsync();
      if (!foreground.granted) {
        onDecided(false);
        return;
      }
      const background = await Location.requestBackgroundPermissionsAsync();
      onDecided(background.granted);
    } catch {
      // A platform that cannot offer the tier at all - a simulator, or a build
      // where app.json has not enabled background location yet (DRV-2). Treated
      // as a decline: the app falls back to tapping, which is a working app.
      onDecided(false);
    } finally {
      setAsking(false);
    }
  }

  return (
    <ScreenContainer>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>Arrival times, without the tapping</Text>

        <Text style={styles.lede}>
          LMX Driver can record when you arrive at and leave each delivery
          automatically, using the addresses already on your route.
        </Text>

        <View style={styles.card}>
          <Text style={styles.heading}>What this records</Text>
          <Text style={styles.body}>
            The time you arrive at a delivery address, and the time you leave it.
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.heading}>What it doesn&apos;t</Text>
          <Text style={styles.body}>
            Where you are between stops. Your route isn&apos;t traced, and nothing is
            recorded when you&apos;re off duty.
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.heading}>Why</Text>
          <Text style={styles.body}>
            So your arrival times are right without you having to stop and tap a
            button in traffic, and so a delivery that ran late can be explained
            rather than argued about.
          </Text>
        </View>

        <Text style={styles.optOut}>
          You can turn this off at any time in your phone&apos;s settings. The app
          keeps working; you&apos;ll be back to marking arrivals by hand.
        </Text>

        <View style={styles.actions}>
          <Button label="Not now" variant="outline" onPress={() => onDecided(false)} />
          <Button label="Continue" onPress={handleContinue} loading={asking} />
        </View>
      </ScrollView>
    </ScreenContainer>
  );
}

function makeStyles(colors: ColorScheme) {
  return StyleSheet.create({
    content: { padding: spacing.lg, gap: spacing.lg },
    title: { ...typography.title, color: colors.textPrimary },
    lede: { ...typography.body, color: colors.textSecondary, lineHeight: 22 },
    card: {
      backgroundColor: colors.surface,
      borderColor: colors.border,
      borderWidth: 1,
      borderRadius: radius.md,
      padding: spacing.lg,
      gap: spacing.xs,
    },
    heading: { ...typography.label, color: colors.textPrimary },
    body: { ...typography.body, color: colors.textSecondary, lineHeight: 22 },
    optOut: { ...typography.small, color: colors.textMuted, lineHeight: 18 },
    actions: { flexDirection: 'row', gap: spacing.md, justifyContent: 'flex-end' },
  });
}
