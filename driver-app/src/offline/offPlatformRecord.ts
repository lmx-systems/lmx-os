import AsyncStorage from '@react-native-async-storage/async-storage';

import type {
  GigJobIntake,
  GigPlatform,
  ItemFactsBody,
  ItemSizeClass,
  OffPlatformEnd,
  OffPlatformRecordBody,
  PinBody,
} from '../api/types';
import type { Pin } from '../location/currentPin';

/**
 * A delivery made for a gig platform, logged as a golden record
 * (docs/ROADMAP_1.5.md DE-7).
 *
 * The form is kept on the phone as a draft until the door is logged, because
 * the two halves happen in two places - the item at the counter, the door at
 * the drop-off - often an hour and a dead zone apart. What reaches the server
 * goes through the outbox like every other stop action; the draft is only the
 * screen remembering where the driver got to.
 */

/** A random v4 UUID. The phone names the record, so it can queue behind it offline. */
export function uuidV4(random: () => number = Math.random): string {
  const hex = Array.from({ length: 32 }, () => Math.floor(random() * 16).toString(16));
  hex[12] = '4';
  hex[16] = ((parseInt(hex[16], 16) & 0x3) | 0x8).toString(16);
  const s = hex.join('');
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20)}`;
}

export interface JobForm {
  platform: GigPlatform | null;
  ref: string;
  pickupAddress: string;
  dropoffAddress: string;
  // Times of day, HH:MM, today.
  pickupFrom: string;
  pickupTo: string;
  deliverBy: string;
  // Dollars, as typed.
  pay: string;
}

export interface ItemForm {
  description: string;
  weightKg: string;
  size: ItemSizeClass | null;
  hazmat: boolean | null;
}

export interface OffPlatformDraft {
  recordId: string;
  pickupStopId: string;
  // A job already in the store, or null when it is typed on the form.
  gigJobId: string | null;
  jobLabel: string;
  form: JobForm;
  item: ItemForm;
  pickedUpAt: string | null;
  deliveredAt: string | null;
  // Once anything is queued the job is fixed: a different job would be a
  // different record, and the server refuses a second record under this id.
  sent?: boolean;
  startedAt: string;
}

export const EMPTY_JOB: JobForm = {
  platform: null,
  ref: '',
  pickupAddress: '',
  dropoffAddress: '',
  pickupFrom: '',
  pickupTo: '',
  deliverBy: '',
  pay: '',
};

export const EMPTY_ITEM: ItemForm = { description: '', weightKg: '', size: null, hazmat: null };

export function newDraft(now: Date = new Date()): OffPlatformDraft {
  return {
    recordId: uuidV4(),
    pickupStopId: uuidV4(),
    gigJobId: null,
    jobLabel: '',
    form: { ...EMPTY_JOB },
    item: { ...EMPTY_ITEM },
    pickedUpAt: null,
    deliveredAt: null,
    startedAt: now.toISOString(),
  };
}

/** `HH:MM` today in the phone's time zone, as an ISO instant; null if it is not a time. */
export function timeToday(hhmm: string, now: Date = new Date()): string | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  const at = new Date(now);
  at.setHours(hours, minutes, 0, 0);
  return at.toISOString();
}

/** The typed job as the server takes it, or what is wrong with it. */
export function jobIntake(form: JobForm, now: Date = new Date()): GigJobIntake | string {
  if (!form.platform) return 'Which platform was it?';
  if (!form.ref.trim()) return "The platform's job number";
  if (!form.pickupAddress.trim()) return 'The pickup address';
  if (!form.dropoffAddress.trim()) return 'The drop-off address';
  const open = timeToday(form.pickupFrom, now);
  const close = timeToday(form.pickupTo, now);
  if (!open || !close) return 'The pickup window, as HH:MM';
  if (close < open) return 'The pickup window closes before it opens';
  const deliverBy = form.deliverBy.trim() ? timeToday(form.deliverBy, now) : undefined;
  if (deliverBy === null) return 'Deliver by, as HH:MM';
  const dollars = Number(form.pay.replace('$', '').replace(',', '.'));
  if (!form.pay.trim() || !Number.isFinite(dollars) || dollars < 0) return 'The pay, in dollars';
  return {
    source_platform: form.platform,
    platform_job_ref: form.ref.trim(),
    pickup_address: form.pickupAddress.trim(),
    dropoff_address: form.dropoffAddress.trim(),
    pickup_window_open: open,
    pickup_window_close: close,
    ...(deliverBy ? { dropoff_window_close: deliverBy } : {}),
    pay_cents: Math.round(dollars * 100),
    intake_source: 'manual',
  };
}

/** The item fields the driver filled in; a blank stays unknown. */
export function itemFacts(item: ItemForm): ItemFactsBody | string {
  const facts: ItemFactsBody = {};
  if (item.description.trim()) facts.item_description = item.description.trim();
  if (item.weightKg.trim()) {
    const kg = Number(item.weightKg.replace(',', '.'));
    if (!Number.isFinite(kg) || kg < 0 || kg > 2000) return 'A weight in kilograms, 0 to 2,000.';
    facts.item_weight_kg = kg;
  }
  if (item.size) facts.item_size_class = item.size;
  if (item.hazmat !== null) facts.item_hazmat_or_liquid = item.hazmat;
  return facts;
}

export function pinBody(pin: Pin | null): PinBody | undefined {
  return pin ? { lat: pin.lat, lng: pin.lng, accuracy_m: pin.accuracy_m } : undefined;
}

/** One send: the job named, plus whatever this moment adds. */
export function recordBody(
  draft: OffPlatformDraft,
  job: GigJobIntake | null,
  extra: { item?: ItemFactsBody; pickup?: OffPlatformEnd; dropoff?: OffPlatformEnd },
): OffPlatformRecordBody {
  return {
    pickup_stop_id: draft.pickupStopId,
    ...(draft.gigJobId ? { gig_job_id: draft.gigJobId } : job ? { job } : {}),
    ...extra,
  };
}

const STORAGE_KEY = 'lmx_off_platform_drafts_v1';

export async function loadDrafts(): Promise<OffPlatformDraft[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as OffPlatformDraft[]) : [];
  } catch {
    return [];
  }
}

export async function saveDraft(draft: OffPlatformDraft): Promise<void> {
  const drafts = (await loadDrafts()).filter((d) => d.recordId !== draft.recordId);
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify([...drafts, draft]));
}

export async function removeDraft(recordId: string): Promise<void> {
  const drafts = (await loadDrafts()).filter((d) => d.recordId !== recordId);
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(drafts));
}
