// Split a one-cell name ("SMITH, John A", "Mr John Smith", "John & Mary Smith")
// into first and last. When unsure it says so and carries on: never blocks.
import { trimAll, isBlank } from './normalise.js';

function clean(tokens, rules) {
  return tokens.filter((t) => {
    const l = t.toLowerCase().replace(/\./g, '');
    return l && !rules.titles.includes(l) && !rules.suffixes.includes(l);
  });
}

/**
 * @returns {{first:string,last:string,confidence:'high'|'low'|'joint'|'blank'}}
 */
export function splitFullName(raw, rules, names) {
  if (isBlank(raw, rules)) return { first: '', last: '', confidence: 'blank' };
  let s = trimAll(raw).replace(/\(.*?\)/g, ' ').replace(/\s+/g, ' ').trim();   // "(deceased)", "(nee X)"
  let confidence = 'high';

  // Two people in one cell. The first person is the one we token; say so.
  const joint = s.split(/\s+(?:&|and|\+)\s+|\s*\/\s*|\s*;\s*/i).filter(Boolean);
  let sharedLast = '';
  if (joint.length > 1) {
    confidence = 'joint';
    const lastPart = clean(joint[joint.length - 1].split(/[\s,]+/), rules);
    const firstPart = clean(joint[0].split(/[\s,]+/), rules);
    if (firstPart.length === 1 && lastPart.length >= 2 && !joint[0].includes(',')) sharedLast = lastPart[lastPart.length - 1];
    s = joint[0];
  }

  if (s.includes(',')) {                                   // "SMITH, John Andrew"
    const [a, b] = s.split(',', 2);
    const last = clean(a.trim().split(/\s+/), rules).join(' ');
    const given = clean((b || '').trim().split(/\s+/), rules);
    if (!given.length) return { first: '', last, confidence: confidence === 'joint' ? 'joint' : 'low' };
    return { first: given[0], last, confidence };
  }

  const t = clean(s.split(/\s+/), rules);
  if (t.length === 0) return { first: '', last: '', confidence: 'blank' };
  if (t.length === 1) {
    if (sharedLast) return { first: t[0], last: sharedLast, confidence };
    return { first: '', last: t[0], confidence: confidence === 'joint' ? 'joint' : 'low' };
  }

  let first = t[0], last = t[t.length - 1];
  // Surname particles: "John van der Berg", "Maria de la Cruz"
  const particles = ['van', 'von', 'de', 'del', 'della', 'der', 'den', 'di', 'da', 'dos', 'du', 'la', 'le', 'st', 'bin', 'binti', 'al', 'el', 'mac', 'mc', 'ter', 'ten'];
  let i = t.length - 2;
  while (i >= 1 && particles.includes(t[i].toLowerCase())) { last = t[i] + ' ' + last; i--; }

  // "SMITH JOHN" with no comma: only swap when the dictionaries are clear about it.
  if (names && s === s.toUpperCase() && t.length === 2) {
    const a = t[0].toLowerCase(), b = t[1].toLowerCase();
    const aFam = names.familyStrong.has(a) || names.familyWeak.has(a);
    const aGiv = names.givenStrong.has(a) || names.givenWeak.has(a);
    const bGiv = names.givenStrong.has(b) || names.givenWeak.has(b);
    const bFam = names.familyStrong.has(b) || names.familyWeak.has(b);
    if (aFam && !aGiv && bGiv && !bFam) { first = t[1]; last = t[0]; }
    if (confidence === 'high' && !(aGiv && bFam && !aFam)) confidence = 'low';
  }
  return { first, last, confidence };
}
