import { Clock, CheckCircle, AlertCircle } from 'lucide-react';
import { InsurerLogo } from '../components/InsurerCard.jsx';
import { noteText } from '../lib/notes.js';

const fmt = (n) => new Intl.NumberFormat('en-AU').format(n || 0);

export default function Uploads({ config, uploads, onNew }) {
  const { items, stats } = uploads;
  const insurerOf = (k) => config.insurers.find((i) => i.key === k) || { key: config.other_key, name: 'Other / unassigned', badge: '?', color: '#6b7280' };
  const insurers = new Set(items.filter((i) => i.state === 'received').map((i) => i.insurerKey)).size;
  const last = config.latest_submission;
  return (
    <div className="p-8 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div><h1 className="text-2xl font-semibold text-gray-900">Your Uploads</h1><p className="text-sm text-gray-500 mt-1">Everything InsurancePLUS has received through this link</p></div>
        <button onClick={onNew} className="px-4 py-2 rounded text-sm font-medium text-white" style={{ backgroundColor: '#004225' }}>New Upload</button>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        {[{ label: 'Files received', value: fmt(stats.received) }, { label: 'Insurers', value: fmt(insurers) }, { label: 'Policies', value: fmt(stats.policies) },
          { label: 'Last submitted', value: last ? new Date(last.submitted_at.replace(' ', 'T')).toLocaleDateString('en-AU') : '—' }].map((c) => (
          <div key={c.label} className="bg-white border border-gray-200 rounded-lg p-4"><div className="text-xs text-gray-500">{c.label}</div><div className="text-xl font-semibold text-gray-900 mt-1">{c.value}</div></div>
        ))}
      </div>
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead><tr className="bg-gray-50 border-b border-gray-200">
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500">File</th><th className="px-6 py-3 text-left text-xs font-medium text-gray-500">Insurer</th>
            <th className="px-6 py-3 text-right text-xs font-medium text-gray-500">Policies</th><th className="px-6 py-3 text-left text-xs font-medium text-gray-500">Status</th>
          </tr></thead>
          <tbody className="divide-y divide-gray-100">
            {items.map((it) => {
              const ins = insurerOf(it.insurerKey);
              const notes = (it.notes || []).map((n) => noteText(n, { insurerName: ins.name })).filter(Boolean);
              return (
                <tr key={it.clientId} className="hover:bg-gray-50 align-top">
                  <td className="px-6 py-3 text-gray-900">{it.displayName}{notes.map((n, i) => <div key={i} className="text-xs text-amber-700 mt-0.5">{n}</div>)}</td>
                  <td className="px-6 py-3"><div className="flex items-center gap-2"><InsurerLogo insurer={ins} size={6} /><span className="text-gray-700">{ins.name}</span></div></td>
                  <td className="px-6 py-3 text-right text-gray-700">{it.serverFile ? fmt(it.serverFile.policy_count) : '…'}</td>
                  <td className="px-6 py-3">
                    {it.state === 'received' && <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded bg-green-50 text-green-700"><CheckCircle className="w-3 h-3" />Received</span>}
                    {(it.state === 'stripping' || it.state === 'uploading') && <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded bg-blue-50 text-blue-700"><Clock className="w-3 h-3" />In progress</span>}
                    {it.state === 'failed' && <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded bg-red-50 text-red-700"><AlertCircle className="w-3 h-3" />Not sent</span>}
                  </td>
                </tr>
              );
            })}
            {items.length === 0 && <tr><td colSpan={4} className="px-6 py-10 text-center text-xs text-gray-400">Nothing yet. Start a new upload.</td></tr>}
          </tbody>
        </table>
      </div>
      {last && <p className="mt-4 text-xs text-gray-500">Last submission reference <span className="font-mono text-gray-700">{last.reference}</span></p>}
    </div>
  );
}
