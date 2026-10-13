import { useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { HandoffPhotoSlot } from '../api/types';
import { PhotoCaptureModal } from '../media/PhotoCaptureModal';
import { discardPhoto, keepPhoto } from '../offline/handoffPhotos';
import { outboxManager } from '../offline/outboxManager';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

const SLOTS: { slot: HandoffPhotoSlot; label: string; hint: string }[] = [
  { slot: 'approach', label: 'The approach', hint: 'The building as you drive up to it' },
  { slot: 'stop_point', label: 'Where you stopped', hint: 'The kerb, bay or lot spot' },
  { slot: 'path', label: 'The way in', hint: 'From the vehicle to the door' },
  { slot: 'handoff', label: 'The handoff', hint: 'The counter, bay or door itself' },
];

/**
 * Four guided photos of the handoff point (docs/ROADMAP_1.5.md DE-4).
 *
 * The record's drop-off end is gold only with all four, so each slot names
 * what to frame. **No faces, no number plates** - said above the buttons,
 * because a photo with a person in it cannot be pooled or shared, and a
 * reminder at the moment of taking it is the only one that works.
 *
 * Each photo is kept as a file and queued, so a stockroom with no signal loses
 * nothing; a retake replaces the photo for its slot. Optional, like every
 * question here.
 */
export function HandoffPhotosPanel({ stopId, queueAfter }: { stopId: string; queueAfter?: string }) {
  const colors = useThemeColors();
  const styles = makeStyles(colors);
  const [taking, setTaking] = useState<HandoffPhotoSlot | null>(null);
  const [taken, setTaken] = useState<Partial<Record<HandoffPhotoSlot, string>>>({});
  const kept = useRef<Partial<Record<HandoffPhotoSlot, string>>>({});

  async function captured(slot: HandoffPhotoSlot, cacheUri: string) {
    setTaking(null);
    const previous = kept.current[slot];
    const uri = await keepPhoto(cacheUri);
    kept.current[slot] = uri;
    await outboxManager.enqueue('handoff-photo', stopId, { slot, uri, contentType: 'image/jpeg' }, { after: queueAfter });
    // The retake replaced the queued photo, so its file is no longer anybody's.
    if (previous) await discardPhoto(previous);
    setTaken((t) => ({ ...t, [slot]: uri }));
  }

  return (
    <View style={styles.panel}>
      <Text style={styles.title}>Four photos of the door</Text>
      <Text style={styles.warning}>No faces and no number plates in the frame.</Text>
      {SLOTS.map(({ slot, label, hint }) => (
        <Pressable
          key={slot}
          accessibilityRole="button"
          onPress={() => setTaking(slot)}
          style={[styles.slot, taken[slot] ? styles.slotDone : null]}
        >
          <Text style={styles.slotLabel}>{taken[slot] ? `✓ ${label}` : label}</Text>
          <Text style={styles.slotHint}>{taken[slot] ? 'Tap to retake' : hint}</Text>
        </Pressable>
      ))}
      <PhotoCaptureModal
        visible={taking !== null}
        onCaptured={(uri) => taking && void captured(taking, uri)}
        onCancel={() => setTaking(null)}
      />
    </View>
  );
}

function makeStyles(colors: ColorScheme) {
  return StyleSheet.create({
    panel: { gap: spacing.sm },
    title: { ...typography.label, color: colors.textPrimary },
    warning: { ...typography.small, color: colors.danger },
    slot: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: radius.sm,
      padding: spacing.md,
      backgroundColor: colors.surface,
    },
    slotDone: { borderColor: colors.primary },
    slotLabel: { ...typography.body, color: colors.textPrimary },
    slotHint: { ...typography.small, color: colors.textMuted },
  });
}
