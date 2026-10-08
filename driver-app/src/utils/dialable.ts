/**
 * The number to hand the phone's dialer or messages app, from a recipient's
 * phone as the sender typed it - or null when there isn't one we can trust.
 *
 * Senders type numbers every way: "(512) 555-0142", "+44 (0)20 7946 0018",
 * "512-555-0142 ext 31". An extension is cut off (the dialer can't use it, and
 * gluing its digits on dials the wrong number); a "(0)" after a country code is
 * a trunk prefix that must not be dialled. Letters mean it isn't a number at all,
 * and fewer than seven digits isn't a whole one - both return null, and the
 * screen shows the text as written instead of dialling a guess.
 */
export function dialable(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const withoutExtension = raw.replace(/\s*(?:x|ext\.?|extension|;ext=|#)\s*\d+\s*$/i, '');
  if (/[a-z]/i.test(withoutExtension)) return null;
  const withoutTrunk = withoutExtension.replace(/^(\s*\+\d{1,3})\s*\(0\)/, '$1');
  const cleaned = withoutTrunk.replace(/[^\d+]/g, '').replace(/(?!^)\+/g, '');
  const digits = cleaned.replace(/\D/g, '');
  return digits.length >= 7 ? cleaned : null;
}
