import { useEffect, useMemo, useState } from 'react';
import { StyleSheet, Text } from 'react-native';

import { api } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { DocType, DriverDocument } from '../api/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

const DOC_LABELS: Record<DocType, string> = {
  license: "Driver's license",
  insurance: 'Vehicle insurance',
};

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function DocumentCard({ docType, doc, onSaved }: { docType: DocType; doc: DriverDocument | null; onSaved: (d: DriverDocument) => void }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [expiresAt, setExpiresAt] = useState(doc?.expires_at ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const expired = doc ? doc.expires_at < todayIso() : false;
  const valid = DATE_RE.test(expiresAt);

  async function handleSave() {
    if (!valid) {
      setError('Enter a date as YYYY-MM-DD.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateDocument(docType, { expires_at: expiresAt });
      onSaved(updated);
    } catch {
      setError('Could not save - try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card style={styles.card}>
      <Text style={styles.docLabel}>{DOC_LABELS[docType]}</Text>
      <Text style={[styles.statusText, !doc && styles.missing, expired && styles.expired]}>
        {doc ? (expired ? `Expired ${doc.expires_at}` : `Valid until ${doc.expires_at}`) : 'Not on file - required to go online'}
      </Text>

      <TextField
        label="Expiration date"
        placeholder="YYYY-MM-DD"
        value={expiresAt}
        onChangeText={setExpiresAt}
        keyboardType="numbers-and-punctuation"
        maxLength={10}
        style={styles.dateInput}
      />
      {error && <Text style={styles.errorText}>{error}</Text>}
      <Button label={doc ? 'Update' : 'Save'} variant="outline" onPress={handleSave} loading={busy} disabled={!expiresAt} />
    </Card>
  );
}

// Screen 1r's document section. No real file upload/OCR in v1 - just the
// expiry date that app/api/driver_routes.py's going-online gate actually
// checks (DriverDocument.expires_at). A photo-upload flow is a fast-follow
// that shouldn't need any API shape changes (file_url already exists).
export function DocumentsScreen() {
  const [documents, setDocuments] = useState<DriverDocument[] | null>(null);

  useEffect(() => {
    (async () => {
      setDocuments(await api.getMyDocuments());
    })();
  }, []);

  function handleSaved(updated: DriverDocument) {
    setDocuments((prev) => {
      const others = (prev ?? []).filter((d) => d.doc_type !== updated.doc_type);
      return [...others, updated];
    });
  }

  if (!documents) {
    return null;
  }

  const byType = new Map(documents.map((d) => [d.doc_type, d]));

  return (
    <ScreenContainer>
      <DocumentCard docType="license" doc={byType.get('license') ?? null} onSaved={handleSaved} />
      <DocumentCard docType="insurance" doc={byType.get('insurance') ?? null} onSaved={handleSaved} />
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    card: { marginBottom: spacing.lg },
    docLabel: { ...typography.body, color: colors.textPrimary },
    statusText: { ...typography.small, color: colors.textMuted },
    dateInput: { marginTop: spacing.md, marginBottom: spacing.sm },
    missing: { color: colors.warning },
    expired: { color: colors.danger },
    errorText: { color: colors.danger, marginBottom: spacing.sm, fontSize: 13 },
  });
