import { useContext, useMemo } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, View } from 'react-native';
import { BottomTabBarHeightContext } from '@react-navigation/bottom-tabs';
import { HeaderShownContext } from '@react-navigation/elements';
import type { ReactNode } from 'react';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { Edge } from 'react-native-safe-area-context';

import { spacing, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

export function ScreenContainer({ children, scroll = true }: { children: ReactNode; scroll?: boolean }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  // A navigation header already sits below the status bar, and the tab bar
  // already clears the home indicator. Taking those insets again doubled them:
  // a ~60px empty band above every screen with a header.
  const headerShown = useContext(HeaderShownContext);
  const tabBarHeight = useContext(BottomTabBarHeightContext);
  const edges: Edge[] = ['left', 'right'];
  if (!headerShown) edges.push('top');
  if (!tabBarHeight) edges.push('bottom');
  // Not scrolling still keeps the gutter: these screens hold their own list,
  // and without it Home's route and the support thread ran to the screen edge.
  const Body = scroll
    ? ScrollView
    : (props: { children: ReactNode }) => <View style={styles.fixedContent}>{props.children}</View>;
  return (
    <SafeAreaView style={styles.safeArea} edges={edges}>
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

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    safeArea: { flex: 1, backgroundColor: colors.bg },
    flex: { flex: 1 },
    scrollContent: { padding: spacing.lg, flexGrow: 1 },
    fixedContent: { flex: 1, paddingHorizontal: spacing.lg, paddingTop: spacing.lg },
  });
