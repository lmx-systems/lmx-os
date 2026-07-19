import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet } from 'react-native';
import type { ReactNode } from 'react';
import { SafeAreaView } from 'react-native-safe-area-context';

import { colors, spacing } from '../theme';

export function ScreenContainer({ children, scroll = true }: { children: ReactNode; scroll?: boolean }) {
  const Body = scroll ? ScrollView : (props: { children: ReactNode }) => <>{props.children}</>;
  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <Body contentContainerStyle={scroll ? styles.scrollContent : undefined} style={!scroll ? styles.flex : undefined}>
          {children}
        </Body>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.bg },
  flex: { flex: 1 },
  scrollContent: { padding: spacing.lg, flexGrow: 1 },
});
