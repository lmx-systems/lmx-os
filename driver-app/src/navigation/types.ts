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
  // The background-location disclosure (DRV-1/DRV-2,
  // docs/BACKGROUND_LOCATION_CONSENT.md §4.4). Reachable from Profile rather
  // than only shown once, because a driver who declined has to be able to
  // change their mind - and one who granted has to be able to see what they
  // agreed to without digging through system settings.
  LocationDisclosure: undefined;
  // Which LMX OS this install talks to (src/api/serverUrl.ts). Not hidden
  // behind a debug flag: pointing a real install at staging is a pilot need,
  // and the build-time default is useless on a handset.
  Server: undefined;
  EditVehicle: undefined;
  // The phones this account is signed in on (S1). Under Profile rather than
  // behind a debug flag: it is the driver's own security surface, and an admin
  // could already see these while the driver could not.
  Devices: undefined;
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
