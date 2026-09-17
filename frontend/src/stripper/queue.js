// Background strip queue for the page. Files start stripping the moment they are
// dropped, the page never blocks, and more files can be dropped at any time.
//
//   const q = createStripQueue({ rules, names, appKey, makeWorker: () => new Worker(new URL('./worker.js', import.meta.url), { type: 'module' }) });
//   const job = q.add(file, { insurerKey: 'aia', profile });
//   job.on('progress', ({ stage, fraction }) => ...); const { cleanFile, safeReport } = await job.done;
//   await q.idle();     // resolves when nothing is queued or running (submit can be pressed before this)

export function createStripQueue({ rules, names, appKey, makeWorker, size }) {
  const max = Math.max(1, Math.min(size || 4, (globalThis.navigator && navigator.hardwareConcurrency ? navigator.hardwareConcurrency - 1 : 2)));
  const workers = [];        // { w, busy, ready }
  const pending = [];        // jobs waiting for a worker
  const jobs = new Map();
  const seq = {};            // per insurer file counter -> aia_1.xlsx, aia_2.xlsx
  let nextId = 1, idleWaiters = [];

  function spawn() {
    const w = makeWorker();
    const slot = { w, busy: null, ready: false };
    w.onmessage = (ev) => {
      const m = ev.data;
      if (m.type === 'ready') { slot.ready = true; pump(); return; }
      const job = jobs.get(m.id); if (!job) return;
      if (m.type === 'progress') { job.stage = m.stage; job.fraction = m.fraction; job.emit('progress', m); return; }
      slot.busy = null;
      if (m.type === 'done') {
        job.state = 'stripped';
        const cleanFile = new File([m.buffer], m.cleanName, { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        job.resolve({ cleanFile, cleanName: m.cleanName, report: m.report, safeReport: m.safeReport });
      } else {
        job.state = 'failed'; job.error = { code: m.code, message: m.message };
        job.reject(Object.assign(new Error(m.message), { code: m.code }));
      }
      job.emit('state', job.state);
      pump();
    };
    w.onerror = (e) => {        // a crashed worker must not strand its file
      const job = slot.busy && jobs.get(slot.busy);
      slot.busy = null; slot.dead = true; try { w.terminate(); } catch (_) { /* already gone */ }
      if (job) { job.state = 'failed'; job.error = { code: 'worker_crashed', message: String(e.message || e) }; job.reject(Object.assign(new Error('worker crashed'), { code: 'worker_crashed' })); job.emit('state', job.state); }
      pump();
    };
    w.postMessage({ type: 'init', rules, names, appKey });
    workers.push(slot);
    return slot;
  }

  function pump() {
    for (let i = workers.length - 1; i >= 0; i--) if (workers[i].dead) workers.splice(i, 1);
    while (pending.length) {
      let slot = workers.find((s) => s.ready && !s.busy);
      if (!slot && workers.length < max) { spawn(); break; }     // it will call pump() when ready
      if (!slot) break;
      const job = pending.shift();
      slot.busy = job.id; job.state = 'stripping'; job.emit('state', job.state);
      job.file.arrayBuffer().then((buffer) => {
        slot.w.postMessage({ type: 'strip', id: job.id, buffer, filename: job.file.name, insurerKey: job.insurerKey, sequence: job.sequence, profile: job.profile }, [buffer]);
      }).catch((e) => { slot.busy = null; job.state = 'failed'; job.reject(Object.assign(new Error(String(e)), { code: 'unreadable_file' })); job.emit('state', job.state); pump(); });
    }
    if (!pending.length && workers.every((s) => !s.busy)) { const w = idleWaiters; idleWaiters = []; w.forEach((f) => f()); }
  }

  function add(file, { insurerKey = 'upload', profile = null } = {}) {
    const id = nextId++;
    seq[insurerKey] = (seq[insurerKey] || 0) + 1;
    const listeners = {};
    const job = { id, file, insurerKey, profile, sequence: seq[insurerKey], state: 'queued', stage: null, fraction: 0, error: null,
      on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); return job; },
      emit(evt, data) { (listeners[evt] || []).forEach((fn) => fn(data)); } };
    job.done = new Promise((resolve, reject) => { job.resolve = resolve; job.reject = reject; });
    job.done.catch(() => {});          // the page reads job.state; an unhandled rejection must not kill anything
    jobs.set(id, job); pending.push(job); pump();
    return job;
  }

  return {
    add,
    jobs: () => [...jobs.values()],
    outstanding: () => [...jobs.values()].filter((j) => j.state === 'queued' || j.state === 'stripping').length,
    idle: () => (pending.length || workers.some((s) => s.busy) ? new Promise((r) => idleWaiters.push(r)) : Promise.resolve()),
    destroy: () => workers.forEach((s) => { try { s.w.terminate(); } catch (_) { /* already gone */ } }),
  };
}
