export type AuthStackParamList = {
  SignIn: undefined;
  VerifyCode: { phone: string; debugCode: string | null };
};

// The job-delivery loop (screens 1d-1m). Its own stack, separate from
// Profile - a driver mid-route shouldn't lose that navigation state by
// tapping over to the Profile tab and back.
export type HomeStackParamList = {
  Home: undefined;
  AvailableJobs: undefined;
  JobDetail: { offerId: string };
  ActiveRoute: undefined;
  ArrivedPickup: { stopId: string };
  ScanParcels: { stopId: string; parcelCount: number; scannedCount: number };
  ProofOfDelivery: { stopId: string };
};

// Screen 1r, "Profile" and its sub-screens - vehicle edit, documents,
// payment method. Kept out of HomeStackParamList's mental model entirely:
// this is account/compliance stuff, not part of the delivery loop.
export type ProfileStackParamList = {
  ProfileHome: undefined;
  EditVehicle: undefined;
  Documents: undefined;
  PaymentMethod: undefined;
};

export type MainTabParamList = {
  HomeTab: undefined;
  ProfileTab: undefined;
};
