// Web Worker entry. One file at a time per worker; the pool lives in queue.js.
// The vendor's original bytes come in here and never go anywhere else: only the
// rebuilt clean workbook and a counts-only report are posted back.
import { stripFile, loadNames, StripError } from './index.js';

let ready = null;      // { rules, names, appKey }

self.onmessage = async (ev) => {
  const m = ev.data;
  try {
    if (m.type === 'init') {
      ready = { rules: m.rules, names: loadNames(m.names), appKey: m.appKey };
      self.postMessage({ type: 'ready' });
      return;
    }
    if (m.type === 'strip') {
      if (!ready) throw new StripError('not_ready', 'worker not initialised');
      const res = await stripFile({
        bytes: new Uint8Array(m.buffer), filename: m.filename, insurerKey: m.insurerKey, sequence: m.sequence, profile: m.profile,
        rules: ready.rules, names: ready.names, appKey: ready.appKey,
        onProgress: (stage, fraction) => self.postMessage({ type: 'progress', id: m.id, stage, fraction }),
      });
      const buf = res.cleanBytes.buffer.slice(res.cleanBytes.byteOffset, res.cleanBytes.byteOffset + res.cleanBytes.byteLength);
      // `report` (with removed header names) stays on this machine for the vendor's own screen;
      // `safeReport` is the only one that may be sent to the server.
      self.postMessage({ type: 'done', id: m.id, cleanName: res.cleanName, buffer: buf, report: res.report, safeReport: res.safeReport }, [buf]);
    }
  } catch (e) {
    self.postMessage({ type: 'error', id: m.id, code: (e && e.code) || 'strip_failed', message: String((e && e.message) || e) });
  }
};
