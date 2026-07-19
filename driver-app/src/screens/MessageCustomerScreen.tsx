import { useCallback, useRef, useState } from 'react';
import { FlatList, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { Message } from '../api/types';
import type { HomeStackParamList } from '../navigation/types';
import { colors, radius, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<HomeStackParamList, 'MessageCustomer'>;

const POLL_INTERVAL_MS = 5000;

// Screen 1p, "Message customer" - masked SMS (app/models/message.py):
// the customer only ever sees LMX's shared number, never the driver's
// personal phone, and this screen never sees the customer's real number
// either (the API response has no phone field at all - see MessageView).
export function MessageCustomerScreen({ route }: Props) {
  const { stopId, contactName } = route.params;
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    setMessages(await api.getCustomerMessages(stopId));
  }, [stopId]);

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
      const sent = await api.messageCustomer(stopId, draft.trim());
      setMessages((prev) => [...prev, sent]);
      setDraft('');
    } finally {
      setSending(false);
    }
  }

  return (
    <ScreenContainer scroll={false}>
      <Text style={typography.subtitle}>{contactName ? `To ${contactName}` : 'Masked SMS - your number stays private'}</Text>

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

const styles = StyleSheet.create({
  list: { flex: 1, marginTop: spacing.md },
  bubble: { maxWidth: '80%', borderRadius: radius.md, padding: spacing.sm + 2, marginBottom: spacing.sm },
  outbound: { backgroundColor: colors.primary, alignSelf: 'flex-end' },
  inbound: { backgroundColor: colors.border, alignSelf: 'flex-start' },
  outboundText: { color: colors.primaryText },
  inboundText: { color: colors.textPrimary },
  composerRow: { flexDirection: 'row', gap: spacing.sm, alignItems: 'flex-end' },
  composerField: { flex: 1 },
});
