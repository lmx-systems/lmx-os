import { ActivityIndicator, View } from 'react-native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import { useAuth } from '../auth/AuthContext';
import { AvailableJobsScreen } from '../screens/AvailableJobsScreen';
import { ActiveRouteScreen } from '../screens/ActiveRouteScreen';
import { ArrivedPickupScreen } from '../screens/ArrivedPickupScreen';
import { HomeScreen } from '../screens/HomeScreen';
import { JobDetailScreen } from '../screens/JobDetailScreen';
import { ProofOfDeliveryScreen } from '../screens/ProofOfDeliveryScreen';
import { ScanParcelsScreen } from '../screens/ScanParcelsScreen';
import { SignInScreen } from '../screens/SignInScreen';
import { VehicleSetupScreen } from '../screens/VehicleSetupScreen';
import { VerifyCodeScreen } from '../screens/VerifyCodeScreen';
import type { AuthStackParamList, MainStackParamList } from './types';
import { colors } from '../theme';

const AuthStack = createNativeStackNavigator<AuthStackParamList>();
const MainStack = createNativeStackNavigator<MainStackParamList>();

function AuthNavigator() {
  return (
    <AuthStack.Navigator screenOptions={{ headerShown: false }}>
      <AuthStack.Screen name="SignIn" component={SignInScreen} />
      <AuthStack.Screen name="VerifyCode" component={VerifyCodeScreen} />
    </AuthStack.Navigator>
  );
}

function MainNavigator() {
  return (
    <MainStack.Navigator screenOptions={{ headerShown: false }}>
      <MainStack.Screen name="Home" component={HomeScreen} />
      <MainStack.Screen name="AvailableJobs" component={AvailableJobsScreen} options={{ headerShown: true, title: 'Available jobs' }} />
      <MainStack.Screen name="JobDetail" component={JobDetailScreen} options={{ headerShown: true, title: 'Job detail' }} />
      <MainStack.Screen name="ActiveRoute" component={ActiveRouteScreen} />
      <MainStack.Screen name="ArrivedPickup" component={ArrivedPickupScreen} options={{ headerShown: true, title: 'Pickup' }} />
      <MainStack.Screen name="ScanParcels" component={ScanParcelsScreen} options={{ headerShown: true, title: 'Scan parcels' }} />
      <MainStack.Screen name="ProofOfDelivery" component={ProofOfDeliveryScreen} options={{ headerShown: true, title: 'Delivery' }} />
    </MainStack.Navigator>
  );
}

// Three states, not two: signed-out -> auth stack; signed-in but
// vehicle/profile setup incomplete (Driver.vehicle_type is null - see
// app/models/driver.py) -> setup screen (1c), which isn't part of a
// navigable stack since there's nowhere else to go until it's done;
// signed-in and set up -> the real app.
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
