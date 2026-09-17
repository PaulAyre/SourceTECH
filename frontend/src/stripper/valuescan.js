// Prong C: find PII by what is IN the cells, whatever the column is called.
// No AI. Regexes, checksum validators and name dictionaries only.
import { trimAll } from './normalise.js';
import { DIACRITICS_RE, APOSTROPHE_RE } from './chars.js';

const EMAIL = /[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/;
// Australian numbers written the way people write them (spaces, +61, brackets).
const PHONE_FORMATTED = /(?:\+?61[\s-]?|\(0[2-478]\)[\s-]?|\b0[2-478][\s-])\d(?:[\s-]?\d){7,8}\b|\b04\d{2}[\s-]\d{3}[\s-]\d{3}\b|\b1[38]00[\s-]\d{3}[\s-]\d{3}\b/;
const PHONE_BARE = /^(?:0[2-478]\d{8}|61[2-478]\d{8}|4\d{8})$/;       // 4xxxxxxxx: mobile that lost its leading 0 in Excel

export function isEmail(s) { return EMAIL.test(s); }
export function emailDomain(s) { const m = s.match(EMAIL); return m ? m[0].split('@')[1].toLowerCase() : null; }
export function phoneFormatted(s) { return PHONE_FORMATTED.test(s); }
export function phoneBare(s) { return PHONE_BARE.test(s.replace(/\D/g, '')) && /^[\d\s()+-]+$/.test(s); }

/** ATO tax file number checksum (8 or 9 digits). */
export function tfnValid(s) {
  const d = s.replace(/[\s-]/g, '');
  if (!/^\d{8,9}$/.test(d)) return false;
  const w = d.length === 9 ? [1, 4, 3, 7, 5, 8, 6, 9, 10] : [10, 7, 8, 4, 6, 3, 5, 1];
  let sum = 0;
  for (let i = 0; i < d.length; i++) sum += (+d[i]) * w[i];
  return sum % 11 === 0;
}

/** Medicare card: 10 or 11 digits, first digit 2-6, check digit is digit 9. */
export function medicareValid(s) {
  const d = s.replace(/[\s-/]/g, '');
  if (!/^[2-6]\d{9,10}$/.test(d)) return false;
  const w = [1, 3, 7, 9, 1, 3, 7, 9];
  let sum = 0;
  for (let i = 0; i < 8; i++) sum += (+d[i]) * w[i];
  return sum % 10 === +d[8];
}

export function makeAddressTest(rules) {
  const types = rules.valueScan.streetTypes.join('|');
  const street = new RegExp(`\\b\\d{1,5}[A-Za-z]?(?:\\s*[/-]\\s*\\d{1,5})?\\s+(?:[A-Za-z'.-]+\\s+){1,4}(?:${types})\\b\\.?`, 'i');
  const pobox = /\b(?:p\.?\s?o\.?\s?box|gpo\s?box|locked\s?bag|private\s?bag)\s*\d+/i;
  return (s) => street.test(s) || pobox.test(s);
}

/** Pull "NSW 2000" out of a one-cell address so state and postcode can be kept. */
export function extractStatePostcode(s, rules) {
  const t = trimAll(s);
  if (!t) return null;
  const abbr = rules.valueScan.auStates.join('|');
  let m = t.match(new RegExp(`\\b(${abbr})\\b[\\s,]*(\\d{4})\\b`, 'i'));
  if (m) return { state: m[1].toUpperCase(), postcode: m[2] };
  const lower = t.toLowerCase();
  for (const [name, code] of Object.entries(rules.valueScan.auStateNames)) {
    const i = lower.indexOf(name);
    if (i >= 0) {
      const pc = t.slice(i + name.length).match(/\b(\d{4})\b/);
      if (pc) return { state: code, postcode: pc[1] };
    }
  }
  m = t.match(/\b(\d{4})\s*$/);
  if (m && /[A-Za-z]/.test(t)) return { state: '', postcode: m[1] };
  return null;
}

/**
 * Is this cell somebody's name?
 *  - every word must be a name, an initial, a title or a suffix (so "Grace Period"
 *    and "Life Cover" never hit, because Period and Cover are not names)
 *  - a lone word only counts if it is a STRONG name (not also an English word):
 *    "Jason" hits, "May" / "Mark" / "Rose" / "Brown" alone do not
 *  - two or more name words count when one is strong or is a first name:
 *    "Mark Brown", "BROWN, Rose" hit; "Long Term", "Credit Card" do not"
 */
export function makeNameTest(names, rules) {
  const gs = names.givenStrong, gw = names.givenWeak, fs = names.familyStrong, fw = names.familyWeak;
  const isStrong = (w) => gs.has(w) || fs.has(w);
  const isGiven = (w) => gs.has(w) || gw.has(w);
  const isName = (w) => isStrong(w) || gw.has(w) || fw.has(w);
  return function nameHit(cell) {
    const s = trimAll(cell);
    if (!s || s.length > 60 || /\d/.test(s) || /[@#$%*=<>{}\[\]|\\]/.test(s)) return false;
    const w = s.normalize('NFKD').replace(DIACRITICS_RE, '').toLowerCase()
      .replace(APOSTROPHE_RE, '').split(/[^a-z]+/).filter(Boolean);
    if (w.length === 0 || w.length > 5) return false;
    let nameWords = 0, strong = 0, givenWords = 0;
    for (const x of w) {
      if (rules.titles.includes(x) || rules.suffixes.includes(x) || x === 'and') continue;
      if (x.length === 1) continue;                       // an initial
      if (!isName(x)) return false;                       // one non-name word clears the cell
      nameWords++;
      if (isStrong(x)) strong++;
      if (isGiven(x)) givenWords++;
    }
    if (nameWords === 0) return false;
    if (nameWords === 1) return strong === 1;
    // Two ordinary words that both happen to be surnames ("Long Term") are not a person.
    return strong >= 1 || givenWords >= 1;
  };
}

/**
 * Scan one column's cell texts.
 * @param {string[]} texts  trimmed text of each data cell ('' when empty)
 * @param {'unknown'|'text'|'adviser'|'lenient'} mode
 * @returns {{drop:boolean, reason:string|null, evidence:object, blank:number[]}}
 */
export function scanColumn(texts, mode, ctx) {
  const { rules, nameHit, addressHit } = ctx;
  const cfg = rules.valueScan;
  const ev = { email: 0, personalEmailDomain: 0, phoneFormatted: 0, phoneBare: 0, tfn: 0, medicare: 0, address: 0, name: 0 };
  const hitIdx = { email: [], phoneFormatted: [], address: [], name: [], phoneBare: [], tfn: [], medicare: [] };
  let filled = 0, digitish = 0;

  for (let i = 0; i < texts.length; i++) {
    const s = texts[i];
    if (!s) continue;
    filled++;
    if (isEmail(s)) { ev.email++; hitIdx.email.push(i); if (cfg.personalEmailDomains.includes(emailDomain(s))) ev.personalEmailDomain++; continue; }
    if (mode === 'lenient') continue;                     // ids, money, dates: email check only
    const digits = s.replace(/\D/g, '');
    if (digits.length >= 8 && /^[\d\s()+\-/.]+$/.test(s)) {
      digitish++;
      if (phoneFormatted(s)) { ev.phoneFormatted++; hitIdx.phoneFormatted.push(i); continue; }
      if (phoneBare(s)) { ev.phoneBare++; hitIdx.phoneBare.push(i); }
      if (tfnValid(s)) { ev.tfn++; hitIdx.tfn.push(i); }
      if (medicareValid(s)) { ev.medicare++; hitIdx.medicare.push(i); }
      continue;
    }
    if (phoneFormatted(s)) { ev.phoneFormatted++; hitIdx.phoneFormatted.push(i); continue; }
    if (addressHit(s)) { ev.address++; hitIdx.address.push(i); continue; }
    if (mode !== 'adviser' && nameHit(s)) { ev.name++; hitIdx.name.push(i); }
  }

  const rate = (n) => (filled ? n / filled : 0);
  const enough = digitish >= cfg.unknownColumn.minCellsForRate;

  if (mode === 'unknown') {
    const u = cfg.unknownColumn;
    // Paul's rule: one "Jason" in a random column and the whole column goes.
    if (ev.email) return { drop: true, reason: 'email', evidence: ev, blank: [] };
    if (ev.phoneFormatted) return { drop: true, reason: 'phone', evidence: ev, blank: [] };
    if (ev.address) return { drop: true, reason: 'address', evidence: ev, blank: [] };
    if (ev.name >= u.nameHitsToDrop) return { drop: true, reason: 'name', evidence: ev, blank: [] };
    // Bare digit runs are ambiguous (a 9 digit id passes the TFN checksum 1 time in 11),
    // so these need a rate, not a single hit.
    if (enough && ev.tfn / digitish >= u.checksumRateToDrop) return { drop: true, reason: 'tfn', evidence: ev, blank: [] };
    if (enough && ev.medicare / digitish >= u.checksumRateToDrop) return { drop: true, reason: 'medicare', evidence: ev, blank: [] };
    if (enough && ev.phoneBare / digitish >= u.bareDigitPhoneRateToDrop) return { drop: true, reason: 'phone', evidence: ev, blank: [] };
    return { drop: false, reason: null, evidence: ev, blank: [] };
  }

  // Protected column: never dropped (the valuation needs it). Unsafe CELLS are
  // blanked and the DM is told. The job carries on.
  const blank = [...hitIdx.email, ...hitIdx.phoneFormatted, ...hitIdx.address];
  let reason = blank.length ? 'cells_blanked' : null;
  if (mode === 'text') {
    const p = cfg.protectedTextColumn;
    if (ev.name >= p.minNameHitsToFlag && rate(ev.name) >= p.nameRateToFlag) {
      blank.push(...hitIdx.name);
      reason = 'protected_column_looks_like_names';
    }
  }
  return { drop: false, reason, evidence: ev, blank: [...new Set(blank)].sort((a, b) => a - b) };
}
