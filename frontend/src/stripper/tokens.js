// Relationship tokens, browser half. HMAC-SHA256 via WebCrypto, which exists in
// browsers, Web Workers and Node 19+. The app key is public by nature (it ships
// in the page). The server applies a SECOND keyed hash with a secret before
// anything is stored, see shared/ref_tokens.py.

import { UNIT_SEP } from './chars.js';

const enc = new TextEncoder();
const SEP = UNIT_SEP;

function hex(buf, len) {
  const b = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, '0');
  return s.slice(0, len);
}

/**
 * Returns { hash(domain, value), blank } where blank is REF_BLANK: the code for
 * "we looked and there was nothing". One constant across every column.
 * REF_BLANK must never be allowed to match anything downstream.
 */
export async function makeHasher(appKey, hexLength = 32) {
  if (!appKey || String(appKey).length < 16) throw new Error('appKey missing or too short');
  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (!subtle) throw new Error('WebCrypto not available');
  const key = await subtle.importKey('raw', enc.encode(appKey), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const cache = new Map();
  const blank = hex(await subtle.sign('HMAC', key, enc.encode('')), hexLength);

  async function hash(domain, value) {
    if (!value) return blank;
    const k = domain + SEP + value;
    let h = cache.get(k);
    if (!h) {
      h = hex(await subtle.sign('HMAC', key, enc.encode(k)), hexLength);
      cache.set(k, h);
    }
    return h;
  }

  /** Hash many unique values at once; far fewer awaits on big books. */
  async function warm(domain, values) {
    const todo = [...new Set(values)].filter((v) => v && !cache.has(domain + SEP + v));
    await Promise.all(todo.map((v) => hash(domain, v)));
  }

  return { hash, warm, blank };
}
