export type AuthStackParamList = {
  SignIn: undefined;
  VerifyCode: { phone: string; debugCode: string | null };
};

export type MainStackParamList = {
  Home: undefined;
  AvailableJobs: undefined;
  JobDetail: { offerId: string };
  ActiveRoute: undefined;
  ArrivedPickup: { stopId: string };
  ScanParcels: { stopId: string; parcelCount: number };
  ProofOfDelivery: { stopId: string };
};
