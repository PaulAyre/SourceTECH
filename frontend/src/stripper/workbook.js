// Read a workbook into dense cell rows, and write a brand new one.
import * as XLSX from 'xlsx';
import { trimAll } from './normalise.js';

export class StripError extends Error {
  constructor(code, message) { super(message || code); this.code = code; }
}

export function parseWorkbook(u8, filename = '') {
  const ext = (filename.split('.').pop() || '').toLowerCase();
  const isCsv = ext === 'csv' || ext === 'txt' || ext === 'tsv';
  let wb;
  try {
    const opts = {
      dense: true, cellNF: true, cellText: true, cellDates: false,
      cellFormula: false, cellHTML: false, cellStyles: false, bookVBA: false,
      raw: isCsv,                       // CSV: keep text exactly; numbers are inferred later, the way pandas does
    };
    // SheetJS reads CSV bytes as Latin-1, which mangles UTF-8 ("Jason" + NBSP became
    // "Jason" + A-circumflex and changed the name hash). Decode it ourselves.
    wb = isCsv ? XLSX.read(decodeText(u8), { ...opts, type: 'string' }) : XLSX.read(u8, { ...opts, type: 'array' });
  } catch (e) {
    const msg = String((e && e.message) || e);
    if (/password|encrypt/i.test(msg)) throw new StripError('password_protected', 'This file is password protected');
    throw new StripError('unreadable_file', 'This file could not be read');
  }
  const meta = (wb.Workbook && wb.Workbook.Sheets) || [];
  const sheets = wb.SheetNames.map((name, i) => ({
    name, hidden: !!(meta[i] && meta[i].Hidden), rows: (wb.Sheets[name] && wb.Sheets[name]['!data']) || [],
  }));
  return { sheets, isCsv };
}

/** UTF-8 (with or without BOM) first; if the bytes are not valid UTF-8, Windows-1252, which is what Excel on Windows saves. UTF-16 by BOM. */
export function decodeText(u8) {
  if (u8.length >= 2 && u8[0] === 0xff && u8[1] === 0xfe) return new TextDecoder('utf-16le').decode(u8.subarray(2));
  if (u8.length >= 2 && u8[0] === 0xfe && u8[1] === 0xff) return new TextDecoder('utf-16be').decode(u8.subarray(2));
  try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: false }).decode(u8); } catch (e) { return new TextDecoder('windows-1252').decode(u8); }
}

export function buildWorkbook(sheets) {
  const wb = XLSX.utils.book_new();
  sheets.forEach((s, i) => {
    const rows = s.rows;
    const ncols = rows.reduce((m, r) => Math.max(m, r ? r.length : 0), 0);
    const ws = { '!data': rows.map((r) => (r || []).map((c) => c || undefined)) };
    ws['!ref'] = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: Math.max(rows.length - 1, 0), c: Math.max(ncols - 1, 0) } });
    XLSX.utils.book_append_sheet(wb, ws, safeSheetName(s.name, i));
  });
  wb.Props = { Title: '', Author: '', LastAuthor: '', Company: '', Comments: '' };
  const out = XLSX.write(wb, { type: 'array', bookType: 'xlsx', compression: true, Props: wb.Props });
  return new Uint8Array(out);
}

function safeSheetName(name, i) {
  const n = trimAll(name).replace(/[\\/?*[\]:]/g, ' ').slice(0, 31);
  return n || `Sheet${i + 1}`;
}
