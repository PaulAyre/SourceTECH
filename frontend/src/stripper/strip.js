// The three-pronged strip for one sheet.
//   A  expected columns for this insurer (profile)
//   B  PII header dictionary, across every column
//   C  value scan of every remaining cell
// Output is REBUILT from surviving cells only. Nothing is edited in place, so
// hidden sheets, comments, formulas, hyperlinks and document properties cannot
// ride along.
import { trimAll, isBlank, normaliseGiven, normaliseFamily, normaliseDob } from './normalise.js';
import { classifyHeader, headerScore } from './headers.js';
import { scanColumn, isEmail, phoneFormatted, extractStatePostcode } from './valuescan.js';
import { splitFullName } from './names.js';

export function cellText(cell) {
  if (!cell || cell.v === undefined || cell.v === null) return '';
  if (cell.t === 's') return trimAll(cell.v);
  if (cell.t === 'n') return trimAll(cell.w !== undefined ? cell.w : cell.v);
  if (cell.t === 'b') return cell.v ? 'TRUE' : 'FALSE';
  if (cell.t === 'd') return cell.v instanceof Date ? cell.v.toISOString().slice(0, 10) : trimAll(cell.v);
  if (cell.t === 'e') return '';
  return trimAll(cell.v);
}

/** Copy only the value. Formulas, comments, links, styles and rich text are left behind. */
function cleanCell(cell) {
  if (!cell || cell.v === undefined || cell.v === null || cell.t === 'z') return null;
  const out = { t: cell.t, v: cell.v };
  if (cell.z && (cell.t === 'n' || cell.t === 'd')) out.z = cell.z;
  return out;
}

function findHeaderRow(rows, rules) {
  const limit = Math.min(rows.length, rules.valueScan.maxHeaderSearchRows);
  let best = -1, bestKnown = 0;
  for (let r = 0; r < limit; r++) {
    const row = rows[r] || [];
    const s = headerScore(row.map(cellText), rules);
    if (s.filled >= 2 && s.texty / s.filled >= 0.6 && s.known >= 2 && s.known > bestKnown) { best = r; bestKnown = s.known; }
  }
  if (best >= 0) return best;
  for (let r = 0; r < limit - 1; r++) {                    // nothing recognised: first wide, texty row with data under it
    const s = headerScore((rows[r] || []).map(cellText), rules);
    if (s.filled >= 3 && s.texty / s.filled >= 0.8 && (rows[r + 1] || []).some((c) => cellText(c))) return r;
  }
  return -1;
}

const LENIENT_KINDS = new Set(['id', 'number', 'date', 'code']);

/**
 * @param {Array<Array<object|null>>} rows  dense SheetJS cells
 * @param {object} ctx {rules, names, nameHit, addressHit, hasher, profile, isCsv}
 */
export async function stripSheet(rows, ctx) {
  const { rules, hasher, profile } = ctx;
  const report = { headerRow: -1, dataRows: 0, kept: [], removed: [], blankedCells: [], extracted: [], notes: [], tokens: null };

  const h = findHeaderRow(rows, rules);
  if (h < 0) return { rows: null, report: { ...report, notes: [{ code: 'no_table_found' }] } };
  report.headerRow = h;

  const ncols = rows.reduce((m, r) => Math.max(m, r ? r.length : 0), 0);
  const data = rows.slice(h + 1);
  report.dataRows = data.length;
  // profile.expected = { "Header text": "standard_field" }, or profile.alternatives = [ {...}, {...} ] when one
  // tile covers more than one export layout (Zurich and OnePath share a tile): the closest layout is used.
  const asMap = (m) => (Array.isArray(m) ? Object.fromEntries(m.map((k) => [k, k])) : m);      // a plain list of header names is fine too
  let expected = (profile && profile.expected && asMap(profile.expected)) || null;
  if (profile && Array.isArray(profile.alternatives) && profile.alternatives.length) {
    const have = new Set((rows[h] || []).map((c) => cellText(c).toLowerCase()));
    const score = (m) => Object.keys(m).filter((k) => have.has(trimAll(k).toLowerCase())).length;
    expected = [...profile.alternatives.map(asMap)].sort((x, y) => score(y) - score(x))[0];
  }
  if (expected && !Object.keys(expected).length) expected = null;
  const expectedKeys = expected ? Object.keys(expected).map((k) => trimAll(k).toLowerCase()) : [];

  // ---- classify every column -------------------------------------------------
  const cols = [];
  for (let c = 0; c < ncols; c++) {
    const header = cellText((rows[h] || [])[c]);
    const texts = data.map((r) => cellText(r && r[c]));
    const filled = texts.reduce((n, t) => n + (t ? 1 : 0), 0);
    const cls = classifyHeader(header, rules);
    const col = { c, header, texts, filled, cls, action: 'keep', prong: null, reason: null, blank: new Set() };
    col.isExpected = expectedKeys.includes(header.toLowerCase());

    if (!header && filled === 0) { col.action = 'drop'; col.reason = 'empty'; cols.push(col); continue; }

    // A header can itself be PII (pivoted exports use client names as column titles).
    if (header && (isEmail(header) || phoneFormatted(header) || ctx.nameHit(header)) && cls.cls !== 'protected') {
      col.action = 'drop'; col.prong = 'C'; col.reason = 'header_is_pii'; cols.push(col); continue;
    }

    if (cls.cls === 'pii') {
      col.action = 'drop'; col.prong = col.isExpected ? 'A' : 'B'; col.reason = cls.evidence;
      if (col.isExpected) report.notes.push({ code: 'expected_column_removed_as_pii', count: 1 });
    } else if (cls.cls === 'protected') {
      const mode = cls.valueKind === 'adviser' ? 'adviser' : LENIENT_KINDS.has(cls.valueKind) ? 'lenient' : 'text';
      const res = scanColumn(texts, mode, ctx);
      col.blank = new Set(res.blank);
      if (res.blank.length) {
        report.blankedCells.push({ header, count: res.blank.length, reason: res.reason });
        report.notes.push({ code: res.reason === 'protected_column_looks_like_names' ? 'needed_column_held_names' : 'cells_removed_from_needed_column', count: res.blank.length });
      }
    } else {
      const res = scanColumn(texts, 'unknown', ctx);
      if (res.drop) { col.action = 'drop'; col.prong = 'C'; col.reason = res.reason; }
    }
    cols.push(col);
  }

  // ---- state and postcode out of one-cell addresses --------------------------
  const kept = () => cols.filter((x) => x.action === 'keep');
  const hasKind = (k) => kept().some((x) => x.cls.cls === 'protected' && x.cls.kind === k && x.filled > 0);
  const addressCols = cols.filter((x) => x.action === 'drop' && x.reason === 'address').reverse();
  const extra = [];                                           // appended columns: {header, values[]}
  if (addressCols.length && (!hasKind('state') || !hasKind('postcode'))) {
    const st = [], pc = []; let got = 0;
    for (let r = 0; r < data.length; r++) {
      let found = null;
      for (const a of addressCols) { found = extractStatePostcode(a.texts[r], rules); if (found) break; }
      st.push(found ? found.state : ''); pc.push(found ? found.postcode : ''); if (found) got++;
    }
    if (got) {
      if (!hasKind('state')) { extra.push({ header: 'State', values: st }); report.extracted.push('State'); }
      if (!hasKind('postcode')) { extra.push({ header: 'Postcode', values: pc }); report.extracted.push('Postcode'); }
      report.notes.push({ code: 'state_postcode_taken_from_address', count: got });
    }
  }

  // ---- relationship tokens ---------------------------------------------------
  // A "real" row is one with a policy number, when the file has that column (totals and spacer rows do not count).
  const polCol = cols.find((x) => x.action === 'keep' && x.cls.cls === 'protected' && x.cls.kind === 'policy_number' && x.filled > 0);
  const TOTALS = /^(grand\s*)?(sub\s*)?totals?\b|^sum\b|^count\b|^average\b/i;      // "Total" often sits in the policy number column
  const active = data.map((r, i) => (polCol ? !!polCol.texts[i] && !TOTALS.test(polCol.texts[i]) : cols.some((x) => x.action === 'keep' && x.texts[i])));
  report.policyRows = active.filter(Boolean).length;
  const tokenCols = await buildTokens(cols, data, ctx, report, active);

  // ---- CSV: infer numbers the way pandas.read_csv would ----------------------
  const numericCsv = new Set();
  if (ctx.isCsv) {
    for (const col of kept()) {
      const vals = col.texts.filter((t) => t && !isBlank(t, rules));
      if (vals.length && vals.every((t) => /^-?\d{1,15}(\.\d+)?$/.test(t))) numericCsv.add(col.c);
    }
  }

  // ---- rebuild ---------------------------------------------------------------
  const keptCols = kept();
  const out = [];
  let preHeaderBlanked = 0;
  for (let r = 0; r < rows.length; r++) {
    const src = rows[r] || [];
    const row = [];
    if (r < h) {
      // Title rows above the header: keep the layout (PavTECH reads it), lose anything personal.
      for (const col of keptCols) {
        const cell = src[col.c]; const t = cellText(cell);
        const rhs = t.includes(':') ? trimAll(t.split(':').slice(1).join(':')) : t;
        if (t && (isEmail(t) || phoneFormatted(t) || ctx.addressHit(t) || ctx.nameHit(t) || (rhs !== t && ctx.nameHit(rhs)))) { row.push(null); preHeaderBlanked++; }
        else row.push(cleanCell(cell));
      }
    } else if (r === h) {
      for (const col of keptCols) row.push({ t: 's', v: col.header });
      for (const e of extra) row.push({ t: 's', v: e.header });
      for (const t of tokenCols) row.push({ t: 's', v: t.header });
    } else {
      const i = r - h - 1;
      for (const col of keptCols) {
        if (col.blank.has(i)) { row.push(null); continue; }
        let cell = cleanCell(src[col.c]);
        if (cell && numericCsv.has(col.c) && cell.t === 's') {
          const t = trimAll(cell.v);
          cell = t && !isBlank(t, rules) ? { t: 'n', v: Number(t) } : null;
        }
        row.push(cell);
      }
      for (const e of extra) row.push(e.values[i] ? { t: 's', v: e.values[i] } : null);
      const rowHasData = row.some((x) => x);
      for (const t of tokenCols) row.push(rowHasData && t.values[i] ? { t: 's', v: t.values[i] } : null);
    }
    out.push(row);
  }
  if (preHeaderBlanked) report.notes.push({ code: 'personal_details_removed_above_header', count: preHeaderBlanked });

  // ---- report ----------------------------------------------------------------
  for (const col of cols) {
    if (col.action === 'keep') report.kept.push(col.header);
    else if (col.reason !== 'empty') report.removed.push({ header: col.header, prong: col.prong, reason: col.reason });
  }
  if (expected) {
    const have = cols.map((x) => x.header.toLowerCase());
    const missing = Object.keys(expected).filter((k) => !have.includes(trimAll(k).toLowerCase()));
    // Only tell the vendor "this doesn't look like the usual export" when most of it is missing.
    // A column or two short is normal drift: noted for us, not shown to them.
    const total = Object.keys(expected).length;
    if (missing.length) report.notes.push({ code: missing.length / total > 0.5 ? 'expected_columns_missing' : 'some_expected_columns_missing', count: missing.length, fields: missing.map((k) => expected[k]) });
    const surprise = cols.filter((x) => x.action === 'keep' && x.cls.cls === 'unknown' && !x.isExpected).length;
    if (surprise) report.notes.push({ code: 'unexpected_columns_present', count: surprise });
  }
  const needs = ['dob', 'policy_number', 'status'];
  for (const k of needs) if (!hasKind(k) && !(k === 'dob' && hasKind('age'))) report.notes.push({ code: `${k}_column_not_found` });

  return { rows: out, report };
}

async function buildTokens(cols, data, ctx, report, active) {
  const { rules, hasher, names } = ctx;
  const D = rules.tokens.domains;
  const pick = (role, part) => cols.filter((x) => x.cls.cls === 'pii' && (x.cls.evidence === 'name' || x.cls.evidence === 'person') && x.cls.role === role && x.cls.part === part && x.filled > 0);
  const dobCol = (role) => cols.find((x) => x.cls.cls === 'protected' && x.cls.kind === 'dob' && x.cls.role === role && x.filled > 0);
  const result = [];
  const stats = {};

  for (const role of ['insured', 'owner']) {
    const firstC = pick(role, 'first')[0], lastC = pick(role, 'last')[0], prefC = pick(role, 'pref')[0];
    const fullC = pick(role, 'full').sort((a, b) => b.filled - a.filled)[0];
    // DOB with no owner wording belongs to the life insured.
    const dC = dobCol(role) || (role === 'insured' ? cols.find((x) => x.cls.cls === 'protected' && x.cls.kind === 'dob' && x.filled > 0) : null);
    if (role === 'owner' && !firstC && !lastC && !fullC) continue;          // no owner columns: no owner tokens

    const n = data.length;
    const given = new Array(n), family = new Array(n), parts = new Array(n), pref = new Array(n), dob = new Array(n), conf = new Array(n);
    for (let i = 0; i < n; i++) {
      let f = firstC ? firstC.texts[i] : '', l = lastC ? lastC.texts[i] : '', c = 'high';
      if (!firstC && !lastC && fullC) { const s = splitFullName(fullC.texts[i], rules, names); f = s.first; l = s.last; c = s.confidence; }
      else if (fullC && isBlank(f, rules) && isBlank(l, rules)) { const s = splitFullName(fullC.texts[i], rules, names); f = s.first; l = s.last; c = s.confidence; }
      else if (!firstC && !lastC && !fullC) c = 'none';
      given[i] = normaliseGiven(f, rules);
      const fam = normaliseFamily(l, rules);
      family[i] = fam.whole; parts[i] = fam.parts;
      pref[i] = prefC ? normaliseGiven(prefC.texts[i], rules) : '';
      const rawDob = dC ? (data[i] && data[i][dC.c]) : null;
      dob[i] = rawDob ? normaliseDob(rawDob.t === 'n' ? rawDob.v : cellText(rawDob), rules) : '';
      if (c === 'high' && !given[i] && !family[i]) c = 'blank';
      conf[i] = c;
    }
    await hasher.warm(D.given, [...given, ...pref]);
    await hasher.warm(D.family, [...family, ...parts.flat()]);
    await hasher.warm(D.dob, dob);

    const hv = { first: [], last: [], parts: [], pref: [], dob: [] };
    for (let i = 0; i < n; i++) {
      hv.first.push(await hasher.hash(D.given, given[i]));
      hv.last.push(await hasher.hash(D.family, family[i]));
      hv.parts.push(parts[i].length ? (await Promise.all(parts[i].map((p) => hasher.hash(D.family, p)))).join(';') : hasher.blank);
      hv.pref.push(await hasher.hash(D.given, pref[i]));
      hv.dob.push(await hasher.hash(D.dob, dob[i]));
    }
    const names6 = role === 'insured' ? rules.tokens.columns : rules.tokens.ownerColumns;
    result.push({ header: names6[0], values: hv.first }, { header: names6[1], values: hv.last }, { header: names6[2], values: hv.parts },
      { header: names6[3], values: hv.pref }, { header: names6[4], values: hv.dob }, { header: names6[5], values: conf });
    const blanks = (arr) => arr.reduce((k, v, i) => k + (active[i] && !v ? 1 : 0), 0);
    stats[role] = { rows: active.filter(Boolean).length, blankFirst: blanks(given), blankLast: blanks(family), blankPref: blanks(pref), blankDob: blanks(dob),
      lowConfidence: conf.filter((x, i) => active[i] && x === 'low').length, joint: conf.filter((x, i) => active[i] && x === 'joint').length };
  }
  report.tokens = stats;
  const s = stats.insured;
  if (s) {
    if (s.blankDob) report.notes.push({ code: 'rows_without_date_of_birth', count: s.blankDob });
    if (s.lowConfidence) report.notes.push({ code: 'names_split_with_low_confidence', count: s.lowConfidence });
    if (s.joint) report.notes.push({ code: 'joint_names_in_one_cell', count: s.joint });
  }
  return result;
}
