import { useCallback, useMemo, useRef, useState } from 'react';
import { FlatList, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';

import { api } from '../api/client';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { Message } from '../api/types';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

const POLL_INTERVAL_MS = 8000;

// Screen 1q, "Contact support" - reaches LMX dispatch, not the customer
// (see MessageCustomerScreen for that one). If SUPPORT_PHONE_NUMBER isn't
// configured server-side yet (app/config.py), messages are still saved
// here but nobody's actually being texted - dispatch should check this
// with ops before relying on it.
export function SupportScreen() {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    setMessages(await api.getSupportMessages());
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      pollRef.current = setInterval(load, POLL_INTERVAL_MS);
      return () => {
        if (pollRef.current) clearInterval(pollRef.current);
      };
    }, [load]),
  );

  async function handleSend() {
    if (!draft.trim()) return;
    setSending(true);
    try {
      const sent = await api.messageSupport(draft.trim());
      setMessages((prev) => [...prev, sent]);
      setDraft('');
    } finally {
      setSending(false);
    }
  }

  return (
    <ScreenContainer scroll={false}>
      <Text style={styles.introText}>Wrong address, blocked access, safety concern - message dispatch here.</Text>

      <FlatList
        style={styles.list}
        data={messages}
        keyExtractor={(m) => m.message_id}
        renderItem={({ item }) => (
          <View style={[styles.bubble, item.direction === 'outbound' ? styles.outbound : styles.inbound]}>
            <Text style={item.direction === 'outbound' ? styles.outboundText : styles.inboundText}>{item.body}</Text>
          </View>
        )}
      />

      <View style={styles.composerRow}>
        <View style={styles.composerField}>
          <TextField label="" placeholder="Type a message" value={draft} onChangeText={setDraft} />
        </View>
        <Button label="Send" onPress={handleSend} loading={sending} disabled={!draft.trim()} />
      </View>
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    introText: { ...typography.subtitle, color: colors.textSecondary },
    list: { flex: 1, marginTop: spacing.md },
    bubble: { maxWidth: '80%', borderRadius: radius.md, padding: spacing.sm + 2, marginBottom: spacing.sm },
    outbound: { backgroundColor: colors.primary, alignSelf: 'flex-end' },
    inbound: { backgroundColor: colors.border, alignSelf: 'flex-start' },
    outboundText: { color: colors.primaryText },
    inboundText: { color: colors.textPrimary },
    composerRow: { flexDirection: 'row', gap: spacing.sm, alignItems: 'flex-end' },
    composerField: { flex: 1 },
  });
