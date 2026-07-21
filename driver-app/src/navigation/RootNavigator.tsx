import { ActivityIndicator, View } from 'react-native';
import { DollarSign, Home, User } from 'lucide-react-native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import { useAuth } from '../auth/AuthContext';
import { BiometricLockScreen } from '../screens/BiometricLockScreen';
import { DocumentsScreen } from '../screens/DocumentsScreen';
import { EarningsScreen } from '../screens/EarningsScreen';
import { EditVehicleScreen } from '../screens/EditVehicleScreen';
import { FlagIssueScreen } from '../screens/FlagIssueScreen';
import { MessageCustomerScreen } from '../screens/MessageCustomerScreen';
import { PaymentMethodScreen } from '../screens/PaymentMethodScreen';
import { ProfileScreen } from '../screens/ProfileScreen';
import { SignInScreen } from '../screens/SignInScreen';
import { StopDetailScreen } from '../screens/StopDetailScreen';
import { TodayRouteScreen } from '../screens/TodayRouteScreen';
import { SupportScreen } from '../screens/SupportScreen';
import { TripHistoryScreen } from '../screens/TripHistoryScreen';
import { VehicleSetupScreen } from '../screens/VehicleSetupScreen';
import { VerifyCodeScreen } from '../screens/VerifyCodeScreen';
import type {
  AuthStackParamList,
  EarningsStackParamList,
  HomeStackParamList,
  MainTabParamList,
  ProfileStackParamList,
} from './types';
import { useThemeColors } from '../theme';

const AuthStack = createNativeStackNavigator<AuthStackParamList>();
const HomeStack = createNativeStackNavigator<HomeStackParamList>();
const ProfileStack = createNativeStackNavigator<ProfileStackParamList>();
const EarningsStack = createNativeStackNavigator<EarningsStackParamList>();
const Tab = createBottomTabNavigator<MainTabParamList>();

function AuthNavigator() {
  return (
    <AuthStack.Navigator screenOptions={{ headerShown: false }}>
      <AuthStack.Screen name="SignIn" component={SignInScreen} />
      <AuthStack.Screen name="VerifyCode" component={VerifyCodeScreen} />
    </AuthStack.Navigator>
  );
}

// The job-delivery loop, plus masked customer messaging - tied to
// whichever stop is active, so it travels with this stack rather than
// living under Profile. Consolidated per the wireframe redesign: Home
// covers what used to be three screens (offers/route state shown inline,
// not pushed), StopDetail covers what used to be three more
// (arrive/scan/POD collapsed into one state-driven screen).
function HomeNavigator() {
  return (
    <HomeStack.Navigator screenOptions={{ headerShown: false }}>
      <HomeStack.Screen name="Home" component={TodayRouteScreen} />
      <HomeStack.Screen name="StopDetail" component={StopDetailScreen} options={{ headerShown: true, title: 'Stop' }} />
      <HomeStack.Screen name="FlagIssue" component={FlagIssueScreen} options={{ headerShown: true, title: 'Flag an issue' }} />
      <HomeStack.Screen name="MessageCustomer" component={MessageCustomerScreen} options={{ headerShown: true, title: 'Message' }} />
    </HomeStack.Navigator>
  );
}

// Screen 1r, "Profile", its edit sub-screens (vehicle, documents, payment
// method - Phase 2), plus 1q's "Contact support" (Phase 3) - account/help
// territory, not part of the delivery loop.
function ProfileNavigator() {
  return (
    <ProfileStack.Navigator screenOptions={{ headerShown: true }}>
      <ProfileStack.Screen name="ProfileHome" component={ProfileScreen} options={{ title: 'Profile' }} />
      <ProfileStack.Screen name="EditVehicle" component={EditVehicleScreen} options={{ title: 'Edit vehicle' }} />
      <ProfileStack.Screen name="Documents" component={DocumentsScreen} options={{ title: 'Documents' }} />
      <ProfileStack.Screen name="PaymentMethod" component={PaymentMethodScreen} options={{ title: 'Payment method' }} />
      <ProfileStack.Screen name="Support" component={SupportScreen} options={{ title: 'Contact support' }} />
    </ProfileStack.Navigator>
  );
}

// Screens 1n/1o, "Earnings" - Phase 3. Its own tab: earnings isn't part
// of the delivery loop (Home) or account/compliance settings (Profile).
function EarningsNavigator() {
  return (
    <EarningsStack.Navigator screenOptions={{ headerShown: true }}>
      <EarningsStack.Screen name="EarningsHome" component={EarningsScreen} options={{ title: 'Earnings' }} />
      <EarningsStack.Screen name="TripHistory" component={TripHistoryScreen} options={{ title: 'Trip history' }} />
    </EarningsStack.Navigator>
  );
}

// lucide-react-native, not @expo/vector-icons/Feather - matches the icon
// set adopted on the web apps (lucide-react) for a consistent icon
// language across all three LMX OS surfaces.
const TAB_ICONS: Record<keyof MainTabParamList, typeof Home> = {
  HomeTab: Home,
  EarningsTab: DollarSign,
  ProfileTab: User,
};

function MainNavigator() {
  const colors = useThemeColors();
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: { borderTopColor: colors.border, backgroundColor: colors.surface },
        tabBarIcon: ({ color, size }) => {
          const Icon = TAB_ICONS[route.name as keyof MainTabParamList];
          return <Icon color={color} size={size} />;
        },
      })}
    >
      <Tab.Screen name="HomeTab" component={HomeNavigator} options={{ title: 'Home' }} />
      <Tab.Screen name="EarningsTab" component={EarningsNavigator} options={{ title: 'Earnings' }} />
      <Tab.Screen name="ProfileTab" component={ProfileNavigator} options={{ title: 'Profile' }} />
    </Tab.Navigator>
  );
}

// Four states, not two: signed-out -> auth stack; a stored session exists
// but biometric unlock failed/was cancelled -> lock screen (distinct from
// signed-out - the token is still there, just gated); signed-in but
// vehicle/profile setup incomplete (Driver.vehicle_type is null - see
// app/models/driver.py) -> setup screen (1c), which isn't part of a
// navigable stack since there's nowhere else to go until it's done;
// signed-in and set up -> the real app (Home / Earnings / Profile tabs).
export function RootNavigator() {
  const { isLoading, isSignedIn, needsBiometricRetry, profile } = useAuth();
  const colors = useThemeColors();

  if (isLoading) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.bg }}>
        <ActivityIndicator />
      </View>
    );
  }

  if (needsBiometricRetry) {
    return <BiometricLockScreen />;
  }

  if (!isSignedIn) {
    return <AuthNavigator />;
  }

  if (profile && !profile.vehicle_type) {
    return <VehicleSetupScreen />;
  }

  return <MainNavigator />;
}
