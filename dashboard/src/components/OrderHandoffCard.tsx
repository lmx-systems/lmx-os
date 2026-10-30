import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { HandoffCard } from '../lib/types'

/**
 * The drop-off door, on one card (Proof Map O12, `app/identity/handoff_card.py`).
 *
 * What an operator would be handed to serve this order: where the vehicle stops,
 * the walk, the door, what is in the way, where a drone could set down, the
 * hours, the photos. Opened beside AGT-4's explanation because both are about
 * one order.
 *
 * **Not captured is said, never filled in.** A blank would read as "nothing
 * there", which is a captured fact of its own, so an unknown field says "not
 * captured" and the list of them closes the card as what is still to collect.
 * No machine verdict is shown: that is `DE-14`'s, and it is not built.
 */
export function OrderHandoffCard({ orderId }: { orderId: string }) {
  const [card, setCard] = useState<HandoffCard | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api
      .orderHandoffCard(orderId)
      .then((c) => live && setCard(c))
      .catch(() => live && setError('Could not read the handoff record.'))
    return () => {
      live = false
    }
  }, [orderId])

  if (error) return <p className="ml-2 px-2.5 py-1 text-[12px] text-[var(--red)]">{error}</p>
  if (!card) {
    return <p className="ml-2 px-2.5 py-1 text-[12px] text-[var(--text-muted)]">Reading the door…</p>
  }

  const p = card.point
  const photos = Object.entries(card.photos).filter(
    (entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1].startsWith('http'),
  )

  return (
    <div className="mb-1 ml-2 border-l-2 border-[var(--border)] px-2.5 py-1.5">
      <p className="text-[12px] font-semibold text-[var(--text-primary)]">
        The door
        <span className="ml-2 font-normal text-[var(--text-muted)]">
          {card.address ?? 'No drop-off place resolved yet'}
          {card.collected_on && ` · captured ${card.collected_on}`}
        </span>
      </p>

      {card.location_id && (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12px]">
          <Fact label="Handoff">{word(HANDOFF_TYPE, p.handoff_type)}</Fact>
          <Fact label="Vehicle stops">
            {p.stop_type === null && p.stop_legal === null
              ? null
              : `${word(STOP_TYPE, p.stop_type) ?? 'place not captured'}${
                  p.stop_legal === null ? '' : p.stop_legal ? ', legal' : ', not legal'
                }`}
          </Fact>
          <Fact label="Walk to the door">
            {p.walk_distance_m === null ? null : `${Math.round(p.walk_distance_m)} m`}
          </Fact>
          <Fact label="Steps">{word(STEPS, p.steps_or_ramps)}</Fact>
          <Fact label="Sidewalk all the way">{yesNo(p.continuous_sidewalk)}</Fact>
          <Fact label="In the way">{list(p.obstructions, OBSTRUCTION)}</Fact>
          <Fact label="Drone set-down">
            {p.drone_open_ground === null
              ? null
              : p.drone_open_ground
                ? `open ground${p.drone_ground_distance_m === null ? '' : `, ${Math.round(p.drone_ground_distance_m)} m from the door`}`
                : 'no open ground'}
          </Fact>
          <Fact label="Overhead">{list(p.overhead, OVERHEAD)}</Fact>
          <Fact label="Hours">{hours(card.receiving_hours)}</Fact>
          <Fact label="Who receives">{card.who_receives?.replace(/_/g, ' ') ?? null}</Fact>
          <Fact label="Pins">
            {p.stop_lat === null || p.door_lat === null
              ? null
              : `stop ${p.stop_lat.toFixed(5)}, ${p.stop_lng?.toFixed(5)} · door ${p.door_lat.toFixed(5)}, ${p.door_lng?.toFixed(5)}`}
          </Fact>
        </dl>
      )}

      {photos.length > 0 && (
        <div className="mt-1.5 flex gap-1.5">
          {photos.map(([slot, url]) => (
            <a key={slot} href={url} target="_blank" rel="noreferrer" title={PHOTO_SLOT[slot] ?? slot}>
              <img
                src={url}
                alt={PHOTO_SLOT[slot] ?? slot}
                className="h-14 w-14 rounded-[var(--radius-sm)] border border-[var(--border)] object-cover"
              />
            </a>
          ))}
        </div>
      )}

      {card.missing.length > 0 && (
        <p className="mt-1.5 text-[12px] text-[var(--text-secondary)]">
          <span className="font-medium text-[var(--needs)]">Still to collect: </span>
          {card.missing.map((name) => MISSING[name] ?? name.replace(/_/g, ' ')).filter(unique).join(', ')}
        </p>
      )}
    </div>
  )
}

function Fact({ label, children }: { label: string; children: string | null }) {
  return (
    <>
      <dt className="text-[var(--text-muted)]">{label}</dt>
      <dd className={children === null ? 'text-[var(--text-muted)]' : 'text-[var(--text-primary)]'}>
        {children ?? 'not captured'}
      </dd>
    </>
  )
}

const HANDOFF_TYPE: Record<string, string> = {
  counter: 'counter',
  roll_up_bay: 'roll-up bay',
  loading_dock: 'loading dock',
  side_door: 'side door',
  job_site_trailer: 'job-site trailer',
  mailroom: 'mailroom',
}
const STOP_TYPE: Record<string, string> = {
  curb: 'at the curb',
  lot: 'in the lot',
  alley: 'in the alley',
  loading_zone: 'in a loading zone',
  dock_apron: 'on the dock apron',
}
const STEPS: Record<string, string> = {
  none: 'step-free',
  steps: 'steps',
  ramp: 'ramp',
  steps_and_ramp: 'steps and a ramp',
}
const OBSTRUCTION: Record<string, string> = {
  gate: 'a gate',
  fence: 'a fence',
  code_or_buzzer: 'a code or buzzer',
  parked_vehicles: 'parked vehicles',
}
const OVERHEAD: Record<string, string> = { wires: 'wires', canopy: 'a canopy', trees: 'trees' }
const PHOTO_SLOT: Record<string, string> = {
  approach: 'The approach',
  stop_point: 'Where the vehicle stops',
  path: 'The path to the door',
  handoff: 'The handoff spot',
}
// Gold field to what a person goes and finds out. The two halves of a pin are
// one thing to collect.
const MISSING: Record<string, string> = {
  handoff_type: 'which door',
  door_lat: 'the door pin',
  door_lng: 'the door pin',
  stop_lat: 'the stop pin',
  stop_lng: 'the stop pin',
  stop_type: 'where the vehicle stops',
  stop_legal: 'whether stopping there is legal',
  walk_distance_m: 'the walk',
  steps_or_ramps: 'steps',
  continuous_sidewalk: 'the sidewalk',
  obstructions: 'what is in the way',
  drone_open_ground: 'open ground for a drone',
  overhead: 'what is overhead',
  photo_approach: 'the approach photo',
  photo_stop_point: 'the stop photo',
  photo_path: 'the path photo',
  photo_handoff: 'the handoff photo',
}
const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

function word(words: Record<string, string>, value: string | null): string | null {
  return value === null ? null : (words[value] ?? value.replace(/_/g, ' '))
}

function yesNo(value: boolean | null): string | null {
  return value === null ? null : value ? 'yes' : 'no'
}

function list(values: string[] | null, words: Record<string, string>): string | null {
  if (values === null) return null
  return values.length === 0 ? 'nothing' : values.map((v) => words[v] ?? v).join(', ')
}

// `{"mon": [["08:00", "17:00"]], "sun": []}`: an absent day is not stated, an
// empty one is closed (`app/identity/profile.py`).
function hours(stated: Record<string, unknown> | null): string | null {
  if (!stated) return null
  const parts = DAYS.filter((day) => day in stated).map((day) => {
    const spans = stated[day] as [string, string][]
    return `${day[0].toUpperCase()}${day.slice(1)} ${
      spans.length === 0 ? 'closed' : spans.map(([a, b]) => `${a}–${b}`).join(', ')
    }`
  })
  return parts.length === 0 ? null : parts.join(' · ')
}

function unique(value: string, index: number, all: string[]): boolean {
  return all.indexOf(value) === index
}
