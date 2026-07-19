export type AuthStackParamList = {
  SignIn: undefined;
  VerifyCode: { phone: string; debugCode: string | null };
};

// The job-delivery loop (screens 1d-1m, plus 1p's masked customer
// messaging - tied to a specific active stop, so it lives here rather
// than under Profile). Its own stack, separate from Profile - a driver
// mid-route shouldn't lose that navigation state by tapping over to the
// Profile tab and back.
export type HomeStackParamList = {
  Home: undefined;
  AvailableJobs: undefined;
  JobDetail: { offerId: string };
  ActiveRoute: undefined;
  ArrivedPickup: { stopId: string };
  ScanParcels: { stopId: string; parcelCount: number; scannedCount: number };
  ProofOfDelivery: { stopId: string };
  MessageCustomer: { stopId: string; contactName: string | null };
};

// Screen 1r, "Profile" and its sub-screens - vehicle edit, documents,
// payment method, plus 1q's "Contact support" (account/help territory,
// not part of the delivery loop, so it lives here rather than Home).
export type ProfileStackParamList = {
  ProfileHome: undefined;
  EditVehicle: undefined;
  Documents: undefined;
  PaymentMethod: undefined;
  Support: undefined;
};

// Screens 1n/1o - its own tab rather than nested under Home or Profile,
// since earnings is neither part of the delivery loop nor account/
// compliance settings.
export type EarningsStackParamList = {
  EarningsHome: undefined;
  TripHistory: undefined;
};

export type MainTabParamList = {
  HomeTab: undefined;
  EarningsTab: undefined;
  ProfileTab: undefined;
};
