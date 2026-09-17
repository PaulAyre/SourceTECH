// Header classification. Token based, never substring, and PII wins over KEEP.
// (The old server stripper matched substrings with KEEP winning, so "Policy Owner"
// survived because it contains "policy", and "Manager" matched "age".)
import { trimAll } from './normalise.js';
import { NUL } from './chars.js';

export function tokenizeHeader(text, rules) {
  let s = trimAll(text);
  s = s.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/([A-Za-z])(\d)/g, '$1 $2').replace(/(\d)([A-Za-z])/g, '$1 $2');
  const raw = s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().split(' ').filter(Boolean);
  const out = [];
  for (const t of raw) {
    const glued = rules.gluedHeaderWords[t];
    if (glued) out.push(...glued.split(' ')); else out.push(t);
  }
  return out;
}

function findSeq(tokens, seq) {
  outer: for (let i = 0; i + seq.length <= tokens.length; i++) {
    for (let j = 0; j < seq.length; j++) if (tokens[i + j] !== seq[j]) continue outer;
    return i;
  }
  return -1;
}

function anySeq(tokens, seqs) {
  // longest first so ["date","of","birth"] beats ["date"]
  const sorted = [...seqs].sort((a, b) => b.length - a.length);
  for (const s of sorted) if (findSeq(tokens, s) >= 0) return s;
  return null;
}

const OWNER_TOKENS = ['owner', 'owners', 'holder', 'policyholder', 'payer', 'payor', 'proposer', 'applicant'];
const FIRST_TOKENS = ['first', 'given', 'forename', 'christian', 'fname'];
const LAST_TOKENS = ['last', 'surname', 'family', 'lname', 'sname'];
const PREF_TOKENS = ['preferred', 'pref', 'nickname', 'known', 'alias', 'salutation'];
const DROP_PART_TOKENS = ['middle', 'title', 'initial', 'initials', 'maiden', 'suffix', 'second'];

/**
 * @returns {{cls:'protected'|'pii'|'unknown', kind?:string, valueKind?:string,
 *            evidence?:string, role?:'insured'|'owner', part?:'first'|'last'|'pref'|'full'|null}}
 */
export function classifyHeader(text, rules) {
  const tokens = tokenizeHeader(text, rules);
  if (tokens.length === 0) return { cls: 'unknown', tokens, empty: true };
  const role = tokens.some((t) => OWNER_TOKENS.includes(t)) ? 'owner' : 'insured';

  // 1. Things that always survive, whoever they belong to ("Life Insured Date of Birth").
  for (const o of rules.protectedOverrides) {
    if (anySeq(tokens, o.seqs)) return { cls: 'protected', kind: o.kind, valueKind: o.valueKind, role, tokens };
  }

  // 2. Consume protected phrases so their words cannot trip the PII check ("Sum Insured").
  let rest = [...tokens];
  const phrases = [...rules.protectedPhrases].sort((a, b) => b.length - a.length);
  for (const p of phrases) {
    let i;
    while ((i = findSeq(rest, p)) >= 0) rest.splice(i, p.length, NUL);
  }

  // 3. PII wins.
  for (const g of rules.pii) {
    const hit = anySeq(rest, g.seqs);
    if (!hit) continue;
    if (g.evidence === 'name' && (hit[0] === 'name' || hit[0] === 'names') && hit.length === 1) {
      // "Product Name", "Adviser Name" are not people we are protecting.
      const personish = rules.pii.find((x) => x.evidence === 'person');
      const isPerson = anySeq(rest, personish.seqs);
      if (!isPerson && tokens.some((t) => rules.nameExemptQualifiers.includes(t))) continue;
    }
    let part = null;
    // "Policy Owner Address" is an address, not a name: if any OTHER kind of PII is
    // also named in the header, that kind wins and the column is never a name source.
    if (g.evidence === 'name' || g.evidence === 'person') {
      const other = rules.pii.find((x) => x.evidence !== 'name' && x.evidence !== 'person' && anySeq(rest, x.seqs));
      if (other) return { cls: 'pii', evidence: other.evidence, role, part: null, tokens };
    }
    if (g.evidence === 'name' || g.evidence === 'person') {
      if (tokens.some((t) => DROP_PART_TOKENS.includes(t))) part = null;
      else if (tokens.some((t) => PREF_TOKENS.includes(t))) part = 'pref';
      else if (tokens.some((t) => FIRST_TOKENS.includes(t))) part = 'first';
      else if (tokens.some((t) => LAST_TOKENS.includes(t))) part = 'last';
      else if (tokens.some((t) => ['number', 'no', 'id', 'code', 'type', 'count', 'ref', 'reference'].includes(t))) part = null;
      else part = 'full';
    }
    return { cls: 'pii', evidence: g.evidence, role, part, tokens };
  }

  // 4. Known, needed columns.
  for (const p of rules.protected) {
    if (anySeq(tokens, p.seqs)) return { cls: 'protected', kind: p.kind, valueKind: p.valueKind, role, tokens };
  }
  return { cls: 'unknown', tokens };
}

/** How header-like is this row? Used to find the header under title rows. */
export function headerScore(rowTexts, rules) {
  let known = 0, texty = 0, filled = 0;
  for (const t of rowTexts) {
    const s = trimAll(t);
    if (!s) continue;
    filled++;
    if (/[A-Za-z]/.test(s) && !/^\d/.test(s) && s.length <= 60) texty++;
    const c = classifyHeader(s, rules);
    if (c.cls !== 'unknown') known++;
  }
  return { known, texty, filled };
}
