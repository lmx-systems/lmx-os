import type { NavigatorScreenParams } from '@react-navigation/native';

export type AuthStackParamList = {
  SignIn: undefined;
  VerifyCode: { phone: string; debugCode: string | null };
};

// The job-delivery loop (screens 1d-1m, plus 1p's masked customer
// messaging - tied to a specific active stop, so it lives here rather
// than under Profile). Its own stack, separate from Profile - a driver
// mid-route shouldn't lose that navigation state by tapping over to the
// Profile tab and back.
//
// Consolidated per the wireframe redesign: Home now covers what used to be
// three screens (Home/AvailableJobs/ActiveRoute - offers and route state
// live inline on one screen, not separate pushed routes), and StopDetail
// covers what used to be three more (ArrivedPickup/ScanParcels/
// ProofOfDelivery - one state-driven screen instead of a step-by-step
// pushed sequence).
export type HomeStackParamList = {
  Home: undefined;
  StopDetail: { stopId: string };
  FlagIssue: { stopId: string };
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
  // The driver's own scorecard (docs/ROADMAP.md W4). Under Earnings rather than Profile:
  // it is a fact about the work done, which is what the rest of this tab is.
  Scorecard: undefined;
};

// Typed as nested navigator params rather than `undefined` so a screen in one
// tab can send the driver to a screen in another - the compliance banner on
// Home needs to reach Documents under Profile, and `undefined` made that
// unexpressible without a cast.
export type MainTabParamList = {
  HomeTab: NavigatorScreenParams<HomeStackParamList> | undefined;
  EarningsTab: NavigatorScreenParams<EarningsStackParamList> | undefined;
  ProfileTab: NavigatorScreenParams<ProfileStackParamList> | undefined;
};
