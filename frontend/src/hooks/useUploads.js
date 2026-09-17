import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import rules from '../stripper/pii_rules.json';
import namesUrl from '../stripper/names.json?url';
import { createStripQueue } from '../stripper/queue.js';
import { uploadClean, deleteFile } from '../lib/api.js';

const newClientId = () => 'c' + Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('');

/**
 * Files start being stripped the instant they are dropped, in Web Workers, and are
 * sent as soon as they are clean. The page never waits and the vendor can keep
 * dropping files. Item states: stripping -> uploading -> received | failed.
 */
export function useUploads(config) {
  const [items, setItems] = useState([]);
  const queueRef = useRef(null);
  const removed = useRef(new Set());
  const patch = useCallback((clientId, p) => setItems((prev) => prev.map((it) => (it.clientId === clientId ? { ...it, ...(typeof p === 'function' ? p(it) : p) } : it))), []);

  // files already received in an earlier visit
  useEffect(() => {
    if (!config) return;
    setItems((prev) => (prev.length ? prev : config.files.map((f) => ({
      clientId: f.client_id || `server-${f.id}`, insurerKey: f.insurer || config.other_key, displayName: f.name, size: null,
      state: 'received', fraction: 1, serverFile: f, notes: f.notes || [], removedColumns: null, fromEarlier: true,
    }))));
  }, [config]);

  const getQueue = useCallback(async () => {
    if (queueRef.current) return queueRef.current;
    const names = await fetch(namesUrl).then((r) => r.json());
    queueRef.current = createStripQueue({
      rules, names, appKey: config.app_key,
      makeWorker: () => new Worker(new URL('../stripper/worker.js', import.meta.url), { type: 'module' }),
    });
    return queueRef.current;
  }, [config]);

  const add = useCallback((insurerKey, fileList) => {
    const insurer = config.insurers.find((i) => i.key === insurerKey);
    for (const file of Array.from(fileList)) {
      const clientId = newClientId();
      setItems((prev) => [...prev, { clientId, insurerKey, displayName: file.name, size: file.size, state: 'stripping', stage: 'queued', fraction: 0, notes: [], removedColumns: null }]);
      getQueue().then((q) => {
        const job = q.add(file, { insurerKey, profile: insurer && insurer.alternatives && insurer.alternatives.length ? { alternatives: insurer.alternatives } : null });
        job.on('progress', (m) => patch(clientId, { stage: m.stage, fraction: 0.5 * m.fraction }));
        return job.done;
      }).then((res) => {
        if (removed.current.has(clientId)) return null;
        const removedColumns = res.report.sheets.reduce((n, s) => n + s.removed.length, 0);
        patch(clientId, { state: 'uploading', fraction: 0.5, removedColumns, notes: res.safeReport.notes });
        return uploadClean({ cleanFile: res.cleanFile, insurerKey, clientId, safeReport: res.safeReport, onProgress: (f) => patch(clientId, { fraction: 0.5 + 0.5 * f }) });
      }).then((body) => {
        if (!body) return;
        if (removed.current.has(clientId)) { deleteFile(body.file.id).catch(() => {}); return; }
        patch(clientId, { state: 'received', fraction: 1, serverFile: body.file, notes: body.file.notes || [] });
      }).catch((e) => {
        if (!removed.current.has(clientId)) patch(clientId, { state: 'failed', error: e.code || 'unreadable_file', fraction: 1 });
      });
    }
  }, [config, getQueue, patch]);

  const remove = useCallback((clientId) => {
    removed.current.add(clientId);
    setItems((prev) => {
      const it = prev.find((x) => x.clientId === clientId);
      if (it && it.serverFile) deleteFile(it.serverFile.id).catch(() => {});
      return prev.filter((x) => x.clientId !== clientId);
    });
  }, []);

  const stats = useMemo(() => {
    const live = items.filter((i) => i.state !== 'failed');
    const outstanding = items.filter((i) => i.state === 'stripping' || i.state === 'uploading').length;
    const progress = live.length ? live.reduce((s, i) => s + (i.fraction || 0), 0) / live.length : 0;
    return { total: items.length, live: live.length, outstanding, received: items.filter((i) => i.state === 'received').length, failed: items.filter((i) => i.state === 'failed').length, progress,
      policies: items.reduce((s, i) => s + ((i.serverFile && i.serverFile.policy_count) || 0), 0) };
  }, [items]);

  // closing the tab would lose files that are still being prepared on this computer
  useEffect(() => {
    if (!stats.outstanding) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [stats.outstanding]);

  return { items, add, remove, stats };
}
