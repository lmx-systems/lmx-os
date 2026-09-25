import { useCallback, useMemo, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';

import { api } from '../api/client';
import type { DriverDevice } from '../api/types';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import { lastSeen } from '../utils/lastSeen';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

/**
 * The phones this driver is signed in on (`docs/ROADMAP.md` S1).
 *
 * **An admin could see a driver's sessions and the driver could not.**
 * `GET` and `DELETE /driver/me/devices` shipped with the first auth commit,
 * were tested, and had no caller — so the person whose account it is was the
 * one person who could not check it or end it. That asymmetry is the whole
 * finding: everything needed already existed, including `is_current`, which
 * only a screen has any use for.
 *
 * Worth a screen rather than a support call because revocation lands on the
 * revoked device's **very next request**, not at its next token refresh
 * (`get_current_driver` checks the revoked set). A driver who left a phone in
 * a van can end that session before the van arrives anywhere.
 *
 * ## This phone is shown and cannot be signed out from here
 *
 * Not squeamishness — the queue. Revoking the current session does not empty
 * the offline outbox, it **strands** it: every queued stop event then 401s,
 * and `refreshOnce` has no valid session to refresh with. `flush` treats a 401
 * as transient exactly so that a shift in a dead zone is not lost (`DRV-4`),
 * and that reasoning holds only while the driver can still sign in. Revoking
 * your own device from a list of devices is also a confusing way to log out
 * when there is a Log out on the previous screen, which now counts the same
 * queue before it does anything.
 *
 * So the current row carries no button and says where to go. Every other row
 * signs out freely, because a phone that is not this one has no queue this
 * app can see and nothing local to lose.
 *
 * ## Why the list can be wrong for a moment, and why that is fine
 *
 * Refetched on focus rather than polled. A session revoked on another phone
 * thirty seconds ago still shows here until the screen is re-entered, which is
 * the same staleness every other list in this app accepts. The failure it
 * would cause — revoking something already revoked — is a 404 the screen
 * treats as success, because the state the driver wanted is the state they got.
 */

export function DevicesScreen() {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [devices, setDevices] = useState<DriverDevice[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDevices(await api.getMyDevices());
      setError(null);
    } catch {
      setError("Couldn't load your phones — check your connection and try again.");
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  function confirmRevoke(device: DriverDevice) {
    const label = device.device_name ?? 'this phone';
    Alert.alert(
      `Sign out ${label}?`,
      'It stops working on its very next request. Signing back in on that phone needs a new code.',
      [
        { text: 'Keep it', style: 'cancel' },
        { text: 'Sign it out', style: 'destructive', onPress: () => void revoke(device) },
      ],
    );
  }

  async function revoke(device: DriverDevice) {
    setRevoking(device.device_id);
    setError(null);
    try {
      await api.revokeDevice(device.device_id);
      await load();
    } catch (err) {
      // A 404 means it is already gone — somebody revoked it from another
      // phone, or from the console. That is the state the driver asked for, so
      // refresh and say nothing rather than report a failure that isn't one.
      const status = (err as { status?: number }).status;
      if (status === 404) {
        await load();
      } else {
        setError("Couldn't sign that phone out. Try again in a moment.");
      }
    } finally {
      setRevoking(null);
    }
  }

  if (devices === null && error === null) {
    return (
      <ScreenContainer>
        <ActivityIndicator color={colors.primary} />
      </ScreenContainer>
    );
  }

  const others = (devices ?? []).filter((d) => !d.is_current);

  return (
    <ScreenContainer>
      {error && <Text style={styles.error}>{error}</Text>}

      <Text style={styles.intro}>
        Signing a phone out here takes effect straight away — on its next tap, not the
        next time it asks for a new token.
      </Text>

      {(devices ?? []).map((device) => (
        <Card key={device.device_id} style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowBody}>
              {device.device_name ?? 'Unnamed phone'}
              {device.is_current ? ' · this phone' : ''}
            </Text>
            <Text style={styles.rowSmall}>
              {device.is_current ? 'In use now' : `Last used ${lastSeen(device.last_seen_at)}`}
            </Text>
          </View>
          {device.is_current ? (
            // No button on purpose. See the note at the top of this file: the
            // queued stop events go out under this session, and Log out on the
            // previous screen is the thing that counts them first.
            <Text style={styles.currentHint}>Use Log out</Text>
          ) : (
            <Pressable
              disabled={revoking !== null}
              onPress={() => confirmRevoke(device)}
              accessibilityRole="button"
              accessibilityLabel={`Sign out ${device.device_name ?? 'unnamed phone'}`}
              style={[styles.signOut, revoking !== null && styles.disabled]}
            >
              <Text style={styles.signOutLabel}>
                {revoking === device.device_id ? 'Signing out…' : 'Sign out'}
              </Text>
            </Pressable>
          )}
        </Card>
      ))}

      {devices !== null && others.length === 0 && (
        <Text style={styles.rowSmall}>
          This is the only phone signed in to your account.
        </Text>
      )}
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    intro: { ...typography.small, color: colors.textMuted, marginBottom: spacing.md },
    error: { ...typography.small, color: colors.danger, marginBottom: spacing.md },
    row: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: spacing.sm,
    },
    rowText: { flex: 1, paddingRight: spacing.md },
    rowBody: { ...typography.body, color: colors.textPrimary },
    rowSmall: { ...typography.small, color: colors.textMuted },
    currentHint: { ...typography.small, color: colors.textMuted },
    signOut: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: radius.sm,
      paddingVertical: spacing.sm,
      paddingHorizontal: spacing.md,
    },
    signOutLabel: { ...typography.label, color: colors.danger },
    disabled: { opacity: 0.4 },
  });
