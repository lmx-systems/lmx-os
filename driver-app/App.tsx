import { useEffect, useState } from 'react';
import { DarkTheme, DefaultTheme, NavigationContainer } from '@react-navigation/native';
import type { Theme as NavigationTheme } from '@react-navigation/native';
import {
  Inter_400Regular,
  Inter_600SemiBold,
  Inter_700Bold,
  useFonts,
} from '@expo-google-fonts/inter';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { loadApiBaseUrl } from './src/api/serverUrl';
import { AuthProvider } from './src/auth/AuthContext';
import { RootNavigator } from './src/navigation/RootNavigator';
import { OutboxProvider } from './src/offline/OutboxContext';
import { ThemeProvider, useTheme } from './src/theme';

SplashScreen.preventAutoHideAsync();

export default function App() {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_600SemiBold,
    Inter_700Bold,
  });
  // Which server this install talks to, if somebody has set one. Read before
  // the first request rather than lazily: AuthContext refreshes a token on
  // mount, and a request that went to the build-time default first would fail
  // in a way that looks like bad credentials rather than a wrong address.
  const [serverResolved, setServerResolved] = useState(false);

  useEffect(() => {
    loadApiBaseUrl().finally(() => setServerResolved(true));
  }, []);

  const ready = (fontsLoaded || fontError) && serverResolved;

  useEffect(() => {
    if (ready) {
      SplashScreen.hideAsync();
    }
  }, [ready]);

  if (!ready) {
    return null;
  }

  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <AuthProvider>
          <OutboxProvider>
            <ThemedApp />
          </OutboxProvider>
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}

function ThemedApp() {
  const { mode, colors } = useTheme();
  const navigationTheme: NavigationTheme = {
    ...(mode === 'dark' ? DarkTheme : DefaultTheme),
    colors: {
      ...(mode === 'dark' ? DarkTheme.colors : DefaultTheme.colors),
      primary: colors.primary,
      background: colors.bg,
      card: colors.surface,
      text: colors.textPrimary,
      border: colors.border,
      notification: colors.danger,
    },
  };

  return (
    <NavigationContainer theme={navigationTheme}>
      <RootNavigator />
      <StatusBar style={mode === 'dark' ? 'light' : 'dark'} />
    </NavigationContainer>
  );
}
