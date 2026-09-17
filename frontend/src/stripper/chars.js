// Character classes built from code points, so the source files stay plain ASCII.
// (Invisible characters pasted straight into a regex literal are unreadable, and a
// literal U+2028 inside one is a syntax error.)
const cp = (n) => String.fromCodePoint(n);
const cls = (ranges) => ranges.map(([a, b]) => (b === undefined || a === b ? cp(a) : cp(a) + '-' + cp(b))).join('');

// control chars, DEL, NBSP, ogham space, en/em spaces, zero-width + bidi marks,
// line/paragraph separators, narrow NBSP, math space, word joiner, ideographic space, BOM
export const INVISIBLE_RE = new RegExp('[' + cls([[0x00, 0x1f], [0x7f], [0xa0], [0x1680], [0x2000, 0x200f], [0x2028, 0x202f], [0x205f], [0x2060], [0x3000], [0xfeff]]) + ']', 'g');
// combining diacritical marks (after NFKD)
export const DIACRITICS_RE = new RegExp('[' + cls([[0x0300, 0x036f]]) + ']', 'g');
// apostrophes and their typographic cousins: O'Brien -> obrien
export const APOSTROPHE_RE = new RegExp('[' + cls([[0x27], [0x60], [0x2018], [0x2019], [0x02bc]]) + ']', 'g');
// hyphen and the dashes people paste in from Word
export const HYPHEN_RE = new RegExp('[' + cp(0x2d) + cls([[0x2010, 0x2015]]) + ']');
export const HYPHEN_SPLIT_RE = new RegExp('[' + cp(0x2d) + cls([[0x2010, 0x2015]]) + ']');
export const UNIT_SEP = cp(0x1f);      // separates domain from value inside the hash input
export const NUL = cp(0x00);           // placeholder for consumed header tokens
