// Trimming and normalising. The Python twin is shared/ref_tokens.py and the two
// MUST agree: shared/vectors.json is generated from this file and checked by both.

import { INVISIBLE_RE, DIACRITICS_RE, APOSTROPHE_RE, HYPHEN_RE, HYPHEN_SPLIT_RE } from './chars.js';

/** Trim, always. "Jason" and "Jason" followed by a non-breaking space must come out identical. */
export function trimAll(v) {
  if (v === null || v === undefined) return '';
  return String(v).replace(INVISIBLE_RE, ' ').replace(/\s+/g, ' ').trim();
}

export function isBlank(v, rules) {
  const t = trimAll(v).toLowerCase();
  return t === '' || rules.blankPlaceholders.includes(t);
}

/** Lower case, accents stripped, letters only per word. Returns array of words. */
function words(v) {
  return trimAll(v)
    .normalize('NFKD').replace(DIACRITICS_RE, '')
    .toLowerCase()
    .replace(APOSTROPHE_RE, '')                 // O'Brien -> obrien
    .split(/[^a-z]+/).filter(Boolean);
}

/**
 * Given name (also used for the preferred name, so Bob in one column can match
 * Bob in the other). First word only: insurers are inconsistent about middle
 * names ("John Andrew" vs "John"), and DOB is the confirmation.
 */
export function normaliseGiven(v, rules) {
  if (isBlank(v, rules)) return '';
  const w = words(v).filter((x) => !rules.titles.includes(x));
  const first = w.find((x) => x.length > 1) || w[0] || '';
  return first;
}

/**
 * Family name. whole = every part joined ("Smith-Jones" -> "smithjones",
 * "Van Der Berg" -> "vanderberg"). parts = the hyphenated halves, so
 * "Smith-Jones" can still pair with "Smith".
 */
export function normaliseFamily(v, rules) {
  if (isBlank(v, rules)) return { whole: '', parts: [] };
  const raw = trimAll(v);
  const w = words(raw).filter((x) => !rules.suffixes.includes(x) && !rules.titles.includes(x));
  const whole = w.join('');
  let parts = [];
  if (HYPHEN_RE.test(raw)) {
    parts = raw.split(HYPHEN_SPLIT_RE)
      .map((p) => words(p).filter((x) => !rules.suffixes.includes(x)).join(''))
      .filter((p) => p.length >= 2 && p !== whole);
  }
  return { whole, parts };
}

const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, sept: 9, oct: 10, nov: 11, dec: 12 };

function pad(n, l = 2) { return String(n).padStart(l, '0'); }

function valid(y, m, d) {
  if (!(y >= 1880 && y <= 2100 && m >= 1 && m <= 12 && d >= 1 && d <= 31)) return false;
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

/** Two digit birth years: never in the future. 45 -> 1945, 05 -> 2005 (if not future). */
function expandYear(yy, nowYear) {
  const y2k = 2000 + yy;
  return y2k > nowYear ? 1900 + yy : y2k;
}

/** Excel serial (1900 date system) to y/m/d with no timezone involved. */
export function serialToYmd(serial) {
  if (typeof serial !== 'number' || !isFinite(serial) || serial < 1 || serial > 80000) return null;
  let n = Math.floor(serial);
  if (n > 59) n -= 1;                       // Excel's phantom 29 Feb 1900
  const ms = Date.UTC(1899, 11, 31) + n * 86400000;
  const dt = new Date(ms);
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
}

/**
 * DOB to YYYY-MM-DD, or '' when it cannot be read. Australian day-first for
 * ambiguous slashed dates. Accepts Excel serials, Date objects, ISO, d/m/y,
 * d-MMM-yy, "12 March 1975".
 */
export function normaliseDob(v, rules, nowYear = new Date().getUTCFullYear()) {
  if (v === null || v === undefined) return '';
  if (v instanceof Date) {
    if (isNaN(v.getTime())) return '';
    return `${v.getUTCFullYear()}-${pad(v.getUTCMonth() + 1)}-${pad(v.getUTCDate())}`;
  }
  if (typeof v === 'number') {
    const p = serialToYmd(v);
    return p && valid(p.y, p.m, p.d) ? `${p.y}-${pad(p.m)}-${pad(p.d)}` : '';
  }
  if (isBlank(v, rules)) return '';
  const s = trimAll(v);
  let m;
  if ((m = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T\s].*)?$/))) {
    const [y, mo, d] = [+m[1], +m[2], +m[3]];
    return valid(y, mo, d) ? `${y}-${pad(mo)}-${pad(d)}` : '';
  }
  if ((m = s.match(/^(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{2}|\d{4})(?:\s.*)?$/))) {
    let [d, mo, y] = [+m[1], +m[2], +m[3]];
    if (m[3].length === 2) y = expandYear(y, nowYear);
    if (mo > 12 && d <= 12) [d, mo] = [mo, d];       // clearly month-first
    return valid(y, mo, d) ? `${y}-${pad(mo)}-${pad(d)}` : '';
  }
  if ((m = s.match(/^(\d{1,2})[-/.\s]?([A-Za-z]{3,9})[-/.,\s]*(\d{2}|\d{4})$/))) {
    const mo = MONTHS[m[2].slice(0, 4).toLowerCase()] || MONTHS[m[2].slice(0, 3).toLowerCase()];
    let y = +m[3]; if (m[3].length === 2) y = expandYear(y, nowYear);
    return mo && valid(y, mo, +m[1]) ? `${y}-${pad(mo)}-${pad(+m[1])}` : '';
  }
  if ((m = s.match(/^([A-Za-z]{3,9})[-/.\s]*(\d{1,2})[,\s]+(\d{4})$/))) {
    const mo = MONTHS[m[1].slice(0, 4).toLowerCase()] || MONTHS[m[1].slice(0, 3).toLowerCase()];
    return mo && valid(+m[3], mo, +m[2]) ? `${m[3]}-${pad(mo)}-${pad(+m[2])}` : '';
  }
  if ((m = s.match(/^(\d{4})(\d{2})(\d{2})$/))) {
    return valid(+m[1], +m[2], +m[3]) ? `${m[1]}-${m[2]}-${m[3]}` : '';
  }
  if (/^\d{4,5}(\.\d+)?$/.test(s)) return normaliseDob(parseFloat(s), rules, nowYear);  // serial stored as text
  return '';
}
