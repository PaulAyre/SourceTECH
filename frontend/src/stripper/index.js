// Public entry point. Pure function: bytes in, clean bytes + report out.
// Runs the same in a Web Worker, on the main thread, and in Node (tests, server re-check).
import { parseWorkbook, buildWorkbook, StripError } from './workbook.js';
import { stripSheet } from './strip.js';
import { makeHasher } from './tokens.js';
import { makeNameTest, makeAddressTest, isEmail, phoneFormatted } from './valuescan.js';

export { StripError } from './workbook.js';
export { makeHasher } from './tokens.js';
export * as normalise from './normalise.js';
export { classifyHeader, tokenizeHeader } from './headers.js';
export { splitFullName } from './names.js';

/** names.json -> Sets. Do this once per worker, not once per file. */
export function loadNames(json) {
  return {
    givenStrong: new Set(json.givenStrong), givenWeak: new Set(json.givenWeak),
    familyStrong: new Set(json.familyStrong), familyWeak: new Set(json.familyWeak),
  };
}

/**
 * @param {object} p
 * @param {Uint8Array} p.bytes      the vendor's original file. NEVER leaves this function.
 * @param {string} p.filename       original name (used for the type only, never sent on)
 * @param {string} [p.insurerKey]   tile the file was dropped on, e.g. "aia"
 * @param {number} [p.sequence]     nth file for that insurer, for the clean name
 * @param {object} p.rules          pii_rules.json
 * @param {object} p.names          loadNames(names.json)
 * @param {string} p.appKey         public app key for the browser half of the token hash
 * @param {object} [p.profile]      { expected: { "Header": "standard_field" } }
 * @param {function} [p.onProgress] (stage, fraction)
 */
export async function stripFile(p) {
  const t0 = Date.now();
  const progress = p.onProgress || (() => {});
  progress('reading', 0.05);
  const { sheets, isCsv } = parseWorkbook(p.bytes, p.filename);

  const nameHit = makeNameTest(p.names, p.rules);
  const addressHit = makeAddressTest(p.rules);
  const hasher = await makeHasher(p.appKey, p.rules.tokens.hexLength);
  const ctx = { rules: p.rules, names: p.names, nameHit, addressHit, hasher, profile: p.profile || null, isCsv };

  const out = [], sheetReports = [], notes = [];
  const visible = sheets.filter((s) => !s.hidden);
  const hiddenCount = sheets.length - visible.length;
  if (hiddenCount) notes.push({ code: 'hidden_sheets_removed', count: hiddenCount });

  for (let i = 0; i < visible.length; i++) {
    progress('removing_personal_details', 0.1 + 0.75 * (i / visible.length));
    const s = visible[i];
    if (!s.rows.length) continue;
    const { rows, report } = await stripSheet(s.rows, ctx);
    if (!rows) { notes.push({ code: 'sheet_without_a_table_removed', count: 1 }); continue; }
    // a sheet tab can carry a client's name
    const name = (nameHit(s.name) || isEmail(s.name) || phoneFormatted(s.name)) ? `Sheet${out.length + 1}` : s.name;
    out.push({ name, rows });
    sheetReports.push(report);
  }
  if (!out.length) throw new StripError('no_table_found', 'No policy table was found in this file');

  progress('rebuilding', 0.9);
  const cleanBytes = buildWorkbook(out);
  const cleanName = `${(p.insurerKey || 'upload').replace(/[^a-z0-9_-]/gi, '')}_${p.sequence || 1}.xlsx`;
  progress('done', 1);

  const report = {
    rulesVersion: p.rules.version, cleanName, sheets: sheetReports, notes,
    blankToken: hasher.blank, ms: Date.now() - t0,
    bytesIn: p.bytes.length, bytesOut: cleanBytes.length,
  };
  return { cleanBytes, cleanName, report, safeReport: safeReport(report) };
}

/**
 * What may be sent to the server and shown in the DM email. Counts and codes only:
 * a removed column's header can itself be personal (a pivoted export titled with
 * client names), so removed headers never leave the browser.
 */
export function safeReport(report) {
  const merge = new Map();
  const add = (n) => { const k = n.code; const cur = merge.get(k) || { code: k, count: 0 }; cur.count += n.count || 1; if (n.fields) cur.fields = n.fields; merge.set(k, cur); };
  report.notes.forEach(add);
  let removed = 0, kept = 0, rows = 0, cellsBlanked = 0;
  const byReason = {};
  for (const s of report.sheets) {
    s.notes.forEach(add);
    removed += s.removed.length; kept += s.kept.length; rows += (s.policyRows ?? s.dataRows);
    for (const r of s.removed) byReason[r.reason] = (byReason[r.reason] || 0) + 1;
    for (const b of s.blankedCells) cellsBlanked += b.count;
  }
  return {
    rulesVersion: report.rulesVersion, cleanName: report.cleanName, sheets: report.sheets.length, rows,
    columnsKept: kept, columnsRemoved: removed, removedByReason: byReason, cellsBlanked,
    tokens: report.sheets.map((s) => s.tokens), notes: [...merge.values()], ms: report.ms,
  };
}
