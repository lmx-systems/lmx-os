import type { SiteFacts } from '../api/types';

/**
 * The site's place facts as door-survey questions (docs/ROADMAP_1.5.md DE-15).
 *
 * A record's end counts only when its site's kind of business, setting and
 * shared-address answer are known, and a fact filled from public data at the
 * desk holds the record at bronze until somebody standing there confirms it.
 * The server says which facts to ask (`ask`): the unknown ones and the desk's
 * guesses. A guess is shown as one of the options, so confirming it is one tap
 * - and the confirmation is sent, because that is what lifts the cap.
 *
 * The codes are the server's vocabulary (app/models/location.py), not a
 * parallel one: an unknown code is refused with a 422 rather than stored.
 */

export interface SiteFactOption {
  value: string | boolean;
  label: string;
  // The value already on the site - the desk's guess - so a tap confirms it.
  guess: boolean;
}

export interface SiteFactQuestion {
  key: 'kind_of_site' | 'setting' | 'shares_address';
  prompt: string;
  options: SiteFactOption[];
  // "Not sure" is offered and sends nothing: unknown stays unknown.
  notSure: boolean;
}

export const KIND_LABELS: Record<string, string> = {
  independent_repair_or_tire: 'Repair or tyre shop',
  body_shop: 'Body shop',
  dealership_counter: 'Dealership counter',
  parts_store_counter: 'Parts store counter',
  fleet_or_transport_yard: 'Fleet or transport yard',
  municipal_fleet_garage: 'Public fleet garage',
  gas_station_with_bays: 'Fuel station with bays',
};

export const SETTING_LABELS: Record<string, string> = {
  city_core: 'City centre',
  suburb: 'Suburb',
};

export function siteFactQuestions(facts: SiteFacts | null | undefined): SiteFactQuestion[] {
  if (!facts) return [];
  const questions: SiteFactQuestion[] = [];
  if (facts.ask.includes('shares_address')) {
    questions.push({
      key: 'shares_address',
      prompt: 'Does another business share this address?',
      options: [
        { value: true, label: 'Yes', guess: facts.shares_address === true },
        { value: false, label: 'No', guess: facts.shares_address === false },
      ],
      notSure: true,
    });
  }
  if (facts.ask.includes('kind_of_site')) {
    questions.push({
      key: 'kind_of_site',
      prompt: facts.kind_of_site ? 'What kind of business is this? Our guess is marked.' : 'What kind of business is this?',
      options: Object.entries(KIND_LABELS).map(([value, label]) => ({
        value,
        label,
        guess: facts.kind_of_site === value,
      })),
      notSure: true,
    });
  }
  if (facts.ask.includes('setting')) {
    questions.push({
      key: 'setting',
      prompt: facts.setting ? 'City centre or suburb? Our guess is marked.' : 'City centre or suburb?',
      options: Object.entries(SETTING_LABELS).map(([value, label]) => ({
        value,
        label,
        guess: facts.setting === value,
      })),
      notSure: true,
    });
  }
  return questions;
}
