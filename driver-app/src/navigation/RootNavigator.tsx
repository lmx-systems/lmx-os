import { ActivityIndicator, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import { useAuth } from '../auth/AuthContext';
import { AvailableJobsScreen } from '../screens/AvailableJobsScreen';
import { ActiveRouteScreen } from '../screens/ActiveRouteScreen';
import { ArrivedPickupScreen } from '../screens/ArrivedPickupScreen';
import { DocumentsScreen } from '../screens/DocumentsScreen';
import { EarningsScreen } from '../screens/EarningsScreen';
import { EditVehicleScreen } from '../screens/EditVehicleScreen';
import { HomeScreen } from '../screens/HomeScreen';
import { JobDetailScreen } from '../screens/JobDetailScreen';
import { MessageCustomerScreen } from '../screens/MessageCustomerScreen';
import { PaymentMethodScreen } from '../screens/PaymentMethodScreen';
import { ProfileScreen } from '../screens/ProfileScreen';
import { ProofOfDeliveryScreen } from '../screens/ProofOfDeliveryScreen';
import { ScanParcelsScreen } from '../screens/ScanParcelsScreen';
import { SignInScreen } from '../screens/SignInScreen';
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
import { colors } from '../theme';

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

// The job-delivery loop (screens 1d-1m), plus 1p's masked customer
// messaging - tied to whichever stop is active, so it travels with this
// stack rather than living under Profile.
function HomeNavigator() {
  return (
    <HomeStack.Navigator screenOptions={{ headerShown: false }}>
      <HomeStack.Screen name="Home" component={HomeScreen} />
      <HomeStack.Screen name="AvailableJobs" component={AvailableJobsScreen} options={{ headerShown: true, title: 'Available jobs' }} />
      <HomeStack.Screen name="JobDetail" component={JobDetailScreen} options={{ headerShown: true, title: 'Job detail' }} />
      <HomeStack.Screen name="ActiveRoute" component={ActiveRouteScreen} />
      <HomeStack.Screen name="ArrivedPickup" component={ArrivedPickupScreen} options={{ headerShown: true, title: 'Pickup' }} />
      <HomeStack.Screen name="ScanParcels" component={ScanParcelsScreen} options={{ headerShown: true, title: 'Scan parcels' }} />
      <HomeStack.Screen name="ProofOfDelivery" component={ProofOfDeliveryScreen} options={{ headerShown: true, title: 'Delivery' }} />
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

const TAB_ICONS: Record<keyof MainTabParamList, keyof typeof Feather.glyphMap> = {
  HomeTab: 'home',
  EarningsTab: 'dollar-sign',
  ProfileTab: 'user',
};

function MainNavigator() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: { borderTopColor: colors.border },
        tabBarIcon: ({ color, size }) => (
          <Feather name={TAB_ICONS[route.name as keyof MainTabParamList]} color={color} size={size} />
        ),
      })}
    >
      <Tab.Screen name="HomeTab" component={HomeNavigator} options={{ title: 'Home' }} />
      <Tab.Screen name="EarningsTab" component={EarningsNavigator} options={{ title: 'Earnings' }} />
      <Tab.Screen name="ProfileTab" component={ProfileNavigator} options={{ title: 'Profile' }} />
    </Tab.Navigator>
  );
}

// Three states, not two: signed-out -> auth stack; signed-in but
// vehicle/profile setup incomplete (Driver.vehicle_type is null - see
// app/models/driver.py) -> setup screen (1c), which isn't part of a
// navigable stack since there's nowhere else to go until it's done;
// signed-in and set up -> the real app (Home / Earnings / Profile tabs).
export function RootNavigator() {
  const { isLoading, isSignedIn, profile } = useAuth();

  if (isLoading) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.bg }}>
        <ActivityIndicator />
      </View>
    );
  }

  if (!isSignedIn) {
    return <AuthNavigator />;
  }

  if (profile && !profile.vehicle_type) {
    return <VehicleSetupScreen />;
  }

  return <MainNavigator />;
}
