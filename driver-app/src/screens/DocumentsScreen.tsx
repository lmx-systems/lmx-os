import { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { api } from '../api/client';
import { uploadDocumentFile } from '../api/uploadCapturedFile';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { DocType, DriverDocument } from '../api/types';
import { PhotoCaptureModal } from '../media/PhotoCaptureModal';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

const DOC_LABELS: Record<DocType, string> = {
  license: "Driver's license",
  insurance: 'Vehicle insurance',
};

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Screen 1r's document section, rebuilt for R4.
 *
 * **What changed and why it had to.** This screen used to send an expiry date and
 * an optional `file_url` - and the backend accepted any string as a licence scan
 * and trusted the driver's own date. Both are gone: the date is recorded as a
 * *claim*, the file is uploaded through a URL the backend mints, and an LMX
 * reviewer reads the real expiry off the document. Nothing here can put a driver
 * on the road; only a review can.
 *
 * So the screen's job is no longer "enter your expiry". It is: get the document
 * to us, show honestly where it is in review, and make a rejection actionable.
 */
function DocumentCard({
  docType,
  doc,
  onChanged,
}: {
  docType: DocType;
  doc: DriverDocument | null;
  onChanged: () => void;
}) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [claimedExpiry, setClaimedExpiry] = useState(doc?.claimed_expires_at ?? '');
  const [busy, setBusy] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dateValid = DATE_RE.test(claimedExpiry);

  async function handleCaptured(localUri: string) {
    setCameraOpen(false);
    setBusy(true);
    setError(null);
    try {
      // The upload also creates or resets the document row, so there is nothing
      // to save afterwards - which is the point: evidence and claim arrive
      // together or not at all.
      await uploadDocumentFile(docType, localUri, 'image/jpeg', claimedExpiry);
      onChanged();
    } catch {
      setError("Couldn't upload - check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCorrectDate() {
    setBusy(true);
    setError(null);
    try {
      await api.updateDocument(docType, { claimed_expires_at: claimedExpiry });
      onChanged();
    } catch {
      setError("Couldn't save - try again.");
    } finally {
      setBusy(false);
    }
  }

  // Said in the driver's terms, and it says whose move it is next. A document
  // sitting in review is not the driver's problem to solve, and telling them to
  // "fix" it would send them round in circles.
  function statusLine(): { text: string; tone: 'ok' | 'warn' | 'bad' | 'muted' } {
    if (!doc || !doc.file_url) {
      return { text: 'Not on file — take a photo of it to get started', tone: 'warn' };
    }
    if (doc.review_status === 'rejected') {
      return {
        text: doc.rejection_reason
          ? `Needs re-uploading — ${doc.rejection_reason}`
          : 'Needs re-uploading',
        tone: 'bad',
      };
    }
    if (doc.review_status === 'pending') {
      return { text: 'Uploaded — waiting on an LMX review', tone: 'muted' };
    }
    if (!doc.is_usable) {
      return { text: `Expired ${doc.verified_expires_at ?? ''} — upload the renewed one`, tone: 'bad' };
    }
    return { text: `Verified — valid until ${doc.verified_expires_at}`, tone: 'ok' };
  }

  const status = statusLine();
  const claimDiffers =
    doc && doc.verified_expires_at && doc.claimed_expires_at !== doc.verified_expires_at;

  return (
    <Card style={styles.card}>
      <View style={styles.headRow}>
        <Text style={styles.docLabel}>{DOC_LABELS[docType]}</Text>
        {doc?.is_usable ? <Text style={styles.badgeOk}>OK</Text> : null}
      </View>

      <Text style={[styles.statusText, styles[status.tone]]}>{status.text}</Text>

      {/* Both dates when they disagree. This is what makes a rejection legible:
          "you told us March, the card says January" beats "rejected". */}
      {claimDiffers ? (
        <Text style={styles.hint}>
          You entered {doc?.claimed_expires_at}; the document says {doc?.verified_expires_at}.
        </Text>
      ) : null}

      <TextField
        label="Expiration date on the document"
        placeholder="YYYY-MM-DD"
        value={claimedExpiry}
        onChangeText={setClaimedExpiry}
        keyboardType="numbers-and-punctuation"
        maxLength={10}
        style={styles.dateInput}
      />
      <Text style={styles.hint}>
        LMX checks this against the document itself before you can go online.
      </Text>

      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      {busy ? (
        <ActivityIndicator color={colors.textMuted} style={styles.spinner} />
      ) : (
        <View style={styles.actions}>
          <Pressable
            onPress={() => setCameraOpen(true)}
            disabled={!dateValid}
            style={[styles.primaryAction, !dateValid && styles.primaryActionDisabled]}
          >
            <Text style={styles.primaryActionLabel}>
              {doc?.file_url ? 'Replace photo' : 'Take a photo'}
            </Text>
          </Pressable>
          {doc?.file_url && dateValid && claimedExpiry !== doc.claimed_expires_at ? (
            <Button label="Correct the date only" variant="outline" onPress={handleCorrectDate} />
          ) : null}
        </View>
      )}

      {!dateValid ? <Text style={styles.hint}>Enter the date first, then photograph it.</Text> : null}

      <PhotoCaptureModal
        visible={cameraOpen}
        onCaptured={handleCaptured}
        onCancel={() => setCameraOpen(false)}
      />
    </Card>
  );
}

export function DocumentsScreen() {
  const [documents, setDocuments] = useState<DriverDocument[] | null>(null);

  const load = useCallback(async () => {
    setDocuments(await api.getMyDocuments());
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!documents) {
    return null;
  }

  const byType = new Map(documents.map((d) => [d.doc_type, d]));

  return (
    <ScreenContainer>
      <DocumentCard docType="license" doc={byType.get('license') ?? null} onChanged={load} />
      <DocumentCard docType="insurance" doc={byType.get('insurance') ?? null} onChanged={load} />
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    card: { marginBottom: spacing.lg },
    headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
    docLabel: { ...typography.body, color: colors.textPrimary },
    badgeOk: {
      ...typography.small,
      color: colors.success,
      backgroundColor: colors.successDim,
      paddingHorizontal: spacing.sm,
      paddingVertical: 2,
      borderRadius: radius.sm,
      overflow: 'hidden',
      fontWeight: '700',
    },
    statusText: { ...typography.small, marginTop: 2 },
    ok: { color: colors.success },
    warn: { color: colors.warning },
    bad: { color: colors.danger },
    muted: { color: colors.textMuted },
    hint: { ...typography.small, color: colors.textMuted, marginTop: 2 },
    dateInput: { marginTop: spacing.md },
    actions: { marginTop: spacing.md, gap: spacing.sm },
    primaryAction: {
      backgroundColor: colors.primary,
      borderRadius: radius.md,
      paddingVertical: spacing.md,
      alignItems: 'center',
    },
    primaryActionDisabled: { opacity: 0.5 },
    primaryActionLabel: { ...typography.body, color: colors.primaryText, fontWeight: '700' },
    spinner: { marginTop: spacing.md },
    errorText: { color: colors.danger, marginTop: spacing.sm, fontSize: 13 },
  });
