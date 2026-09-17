import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, ChevronLeft, ShieldCheck, CheckCircle, Send, Plus, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import InsurerCard, { InsurerLogo } from '../components/InsurerCard.jsx';
import FileUploadZone from '../components/FileUploadZone.jsx';
import { submitIntent, submitFinalize, getStatus } from '../lib/api.js';

const STEPS = ['Select Portals', 'Upload Data', 'Submitted'];
const GREEN = '#004225';
const fmt = (n) => new Intl.NumberFormat('en-AU').format(n || 0);

export default function Wizard({ config, uploads }) {
  const { items, add, remove, stats } = uploads;
  const other = useMemo(() => ({ key: config.other_key, name: 'Other / unassigned', badge: '?', color: '#6b7280', portal_url: '', steps: [] }), [config]);
  const [step, setStep] = useState(0);
  const [selected, setSelected] = useState(() => {
    const withFiles = config.files.map((f) => f.insurer).filter(Boolean);
    return [...new Set([...config.preselected, ...withFiles])].filter((k) => k !== config.other_key);
  });
  const [submission, setSubmission] = useState(config.pending_submission || null);
  const [serverState, setServerState] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const finalized = useRef(false);

  useEffect(() => { if (config.pending_submission) setStep(2); else if (config.files.length) setStep(1); }, [config]);

  const toggle = (key) => setSelected((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  const shown = config.insurers.filter((i) => selected.includes(i.key));
  const itemsFor = (key) => items.filter((i) => i.insurerKey === key);
  const sendable = items.filter((i) => i.state !== 'failed');

  // Submit is available the moment there is anything to send, finished or not.
  const submit = async () => {
    setBusy(true); setError('');
    try {
      const res = await submitIntent(sendable.map((i) => i.clientId));
      finalized.current = false;
      setSubmission(res); setServerState('processing'); setStep(2);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  // Once everything has landed, tell the server (the last upload usually got there first).
  useEffect(() => {
    if (step !== 2 || !submission || stats.outstanding > 0 || finalized.current) return;
    finalized.current = true;
    submitFinalize().catch(() => { finalized.current = false; });
  }, [step, submission, stats.outstanding]);

  // A file that fails AFTER Submit must not leave the server waiting for it.
  const failedCount = stats.failed;
  const lastFailed = useRef(failedCount);
  useEffect(() => {
    if (step === 2 && submission && failedCount > lastFailed.current && sendable.length) {
      submitIntent(sendable.map((i) => i.clientId)).then((res) => { finalized.current = false; setSubmission(res); }).catch(() => {});
    }
    lastFailed.current = failedCount;
  }, [failedCount]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (step !== 2 || !submission) return undefined;
    let stop = false;
    const tick = async () => {
      try {
        const s = await getStatus();
        if (stop) return;
        const mine = s.latest_submission && s.latest_submission.reference === submission.reference;
        setServerState(mine ? s.latest_submission.state : 'processing');
        if (mine && s.latest_submission.state !== 'processing') return;
      } catch (_) { /* keep polling */ }
      if (!stop) setTimeout(tick, 3000);
    };
    tick();
    return () => { stop = true; };
  }, [step, submission]);

  const byInsurer = useMemo(() => {
    const m = new Map();
    for (const it of items.filter((i) => i.state !== 'failed')) {
      const ins = config.insurers.find((x) => x.key === it.insurerKey) || other;
      const cur = m.get(ins.key) || { insurer: ins, files: 0, policies: 0, pending: 0 };
      cur.files += 1; cur.policies += (it.serverFile && it.serverFile.policy_count) || 0; if (it.state !== 'received') cur.pending += 1;
      m.set(ins.key, cur);
    }
    return [...m.values()];
  }, [items, config, other]);

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-gray-900">New Upload</h1>
        <p className="text-sm text-gray-500 mt-1">Aggregate inforce data from your insurer portals</p>
      </div>

      <div className="flex items-center gap-0 mb-8">
        {STEPS.map((s, i) => (
          <div key={s} className="flex items-center">
            <div className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium ${i === step ? 'text-white' : i < step ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-400'}`}
              style={i === step ? { backgroundColor: GREEN } : {}}>
              <span className="w-4 h-4 rounded-full border flex items-center justify-center" style={{ fontSize: '10px' }}>{i < step ? '✓' : i + 1}</span>
              {s}
            </div>
            {i < STEPS.length - 1 && <div className="w-6 h-px bg-gray-200 mx-1" />}
          </div>
        ))}
      </div>

      {step === 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Select insurer portals to upload ({selected.length} selected)</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {config.insurers.map((ins) => <InsurerCard key={ins.key} insurer={ins} selected={selected.includes(ins.key)} onToggle={toggle} fileCount={itemsFor(ins.key).length} />)}
          </div>
        </div>
      )}

      {step === 1 && (
        <div>
          <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 mb-5 flex items-start gap-2">
            <ShieldCheck className="w-4 h-4 text-blue-600 mt-0.5 flex-shrink-0" />
            <p className="text-xs text-blue-800">
              Drop the inforce Excel or CSV export from each portal. Personally identifiable data is removed <strong>on your computer</strong> before
              anything is sent: names, contact details and addresses never leave it. Names are replaced with one-way codes. Keep adding files while the others finish.
            </p>
          </div>
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Upload inforce data files</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {shown.map((ins) => <FileUploadZone key={ins.key} insurer={ins} items={itemsFor(ins.key)} onAddFiles={add} onRemove={remove} />)}
            <FileUploadZone insurer={other} items={itemsFor(config.other_key)} onAddFiles={add} onRemove={remove} />
          </div>
        </div>
      )}

      {step === 2 && submission && (
        <div className="max-w-2xl mx-auto">
          <div className="bg-white border border-gray-200 rounded-lg p-8 text-center">
            <div className="w-12 h-12 rounded-full bg-green-100 flex items-center justify-center mx-auto mb-4"><CheckCircle className="w-6 h-6 text-green-600" /></div>
            <h1 className="text-xl font-semibold text-gray-900 mb-2">Submitted to InsurancePLUS</h1>
            <p className="text-sm text-gray-500 mb-6">
              {stats.outstanding > 0 ? 'Your files are still being prepared and sent. Keep this page open until the bar is full.'
                : serverState === 'processed' ? 'Everything has arrived and been processed. Your Deal Manager has been notified.'
                : serverState === 'needs_attention' ? 'Everything has arrived safely. Your Deal Manager will be in touch.'
                : 'Everything has arrived. Your Deal Manager will be notified once your files have been processed.'}
            </p>

            {stats.outstanding > 0 && (
              <div className="mb-6 text-left">
                <div className="flex justify-between text-xs text-gray-500 mb-1"><span>Removing personally identifiable data and sending</span><span>{stats.received} of {stats.live} files · {Math.round(stats.progress * 100)}%</span></div>
                <div className="h-2 rounded bg-gray-100 overflow-hidden"><div className="h-full transition-all duration-300" style={{ width: `${Math.round(stats.progress * 100)}%`, backgroundColor: GREEN }} /></div>
              </div>
            )}

            <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 text-left space-y-2 mb-6">
              <div className="flex justify-between text-sm"><span className="text-gray-500">Reference</span><span className="font-mono font-medium text-gray-900">{submission.reference}</span></div>
              <div className="flex justify-between text-sm"><span className="text-gray-500">Submitted</span><span className="text-gray-900">{new Date(submission.submitted_at).toLocaleString('en-AU')}</span></div>
              <div className="flex justify-between text-sm"><span className="text-gray-500">Files</span><span className="text-gray-900">{fmt(stats.live)}</span></div>
              <div className="flex justify-between text-sm"><span className="text-gray-500">Policies</span><span className="text-gray-900">{stats.outstanding ? '…' : fmt(stats.policies)}</span></div>
            </div>

            <div className="text-left mb-6">
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">By insurer</h3>
              <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                <table className="w-full text-sm"><tbody className="divide-y divide-gray-100">
                  {byInsurer.map((r) => (
                    <tr key={r.insurer.key}>
                      <td className="px-4 py-2.5"><div className="flex items-center gap-2"><InsurerLogo insurer={r.insurer} size={6} /><span className="text-gray-900 font-medium">{r.insurer.name}</span></div></td>
                      <td className="px-4 py-2.5 text-right text-gray-700">{r.files} file{r.files === 1 ? '' : 's'}</td>
                      <td className="px-4 py-2.5 text-right text-gray-700">{r.pending ? <span className="text-blue-600">sending…</span> : `${fmt(r.policies)} policies`}</td>
                    </tr>
                  ))}
                </tbody></table>
              </div>
            </div>

            {stats.failed > 0 && (
              <div className="mb-6 p-3 bg-amber-50 border border-amber-200 rounded flex items-start gap-2 text-xs text-amber-800 text-left">
                <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />{stats.failed} file{stats.failed === 1 ? '' : 's'} could not be sent. Everything else went through. Go back to see which, and add {stats.failed === 1 ? 'it' : 'them'} again.
              </div>
            )}
            <Button variant="outline" onClick={() => setStep(1)} className="flex items-center gap-2 text-sm mx-auto"><Plus className="w-4 h-4" />Add more files</Button>
          </div>
        </div>
      )}

      {error && <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded text-xs text-red-700">{error}</div>}

      {step < 2 && (
        <div className="flex justify-between items-center mt-8">
          <Button variant="outline" onClick={() => setStep((s) => s - 1)} disabled={step === 0} className="flex items-center gap-2"><ChevronLeft className="w-4 h-4" />Back</Button>
          {step === 0 ? (
            <Button onClick={() => setStep(1)} className="flex items-center gap-2 text-white" style={{ backgroundColor: GREEN }}>Continue<ChevronRight className="w-4 h-4" /></Button>
          ) : (
            <div className="flex items-center gap-3">
              {stats.outstanding > 0 && <span className="text-xs text-gray-500">{stats.outstanding} still being prepared. You can submit now.</span>}
              <Button onClick={submit} disabled={!sendable.length || busy} className="flex items-center gap-2 text-white" style={{ backgroundColor: sendable.length ? GREEN : undefined }}>
                <Send className="w-4 h-4" />{busy ? 'Submitting…' : submission ? 'Submit again' : 'Submit'}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
