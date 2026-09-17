import { useRef, useState } from 'react';
import { Upload, FileSpreadsheet, X, CheckCircle, ExternalLink, AlertCircle, ShieldCheck, ChevronDown } from 'lucide-react';
import { InsurerLogo } from './InsurerCard.jsx';
import { noteText, FAILURES } from '../lib/notes.js';

function FileRow({ item, insurer, onRemove }) {
  const pct = Math.round((item.fraction || 0) * 100);
  const tone = item.state === 'failed' ? 'bg-red-50 border-red-200' : item.state === 'received' ? 'bg-green-50 border-green-200' : 'bg-blue-50 border-blue-200';
  const text = item.state === 'failed' ? 'text-red-800' : item.state === 'received' ? 'text-green-800' : 'text-blue-800';
  const notes = (item.notes || []).map((n) => noteText(n, { insurerName: insurer.name })).filter(Boolean);
  return (
    <div className={`border rounded px-3 py-2 ${tone}`}>
      <div className="flex items-center gap-2">
        {item.state === 'received' ? <CheckCircle className="w-4 h-4 text-green-600 flex-shrink-0" />
          : item.state === 'failed' ? <AlertCircle className="w-4 h-4 text-red-500 flex-shrink-0" />
          : <div className="w-4 h-4 border-2 border-blue-200 border-t-blue-600 rounded-full animate-spin flex-shrink-0" />}
        <FileSpreadsheet className={`w-4 h-4 flex-shrink-0 ${item.state === 'received' ? 'text-green-600' : item.state === 'failed' ? 'text-red-400' : 'text-blue-500'}`} />
        <span className={`text-xs flex-1 truncate ${text}`} title={item.displayName}>{item.displayName}</span>
        <span className={`text-xs font-medium whitespace-nowrap ${text}`}>
          {item.state === 'stripping' && 'Removing personally identifiable data…'}
          {item.state === 'uploading' && `Sending ${Math.max(0, Math.round(((item.fraction || 0.5) - 0.5) * 200))}%`}
          {item.state === 'received' && 'Received'}
          {item.state === 'failed' && 'Not sent'}
        </span>
        <button onClick={() => onRemove(item.clientId)} className={`${text} opacity-70 hover:opacity-100`} aria-label={`Remove ${item.displayName}`}>
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      {(item.state === 'stripping' || item.state === 'uploading') && (
        <div className="mt-1.5 h-1 rounded bg-blue-100 overflow-hidden"><div className="h-full bg-blue-600 transition-all duration-300" style={{ width: `${pct}%` }} /></div>
      )}
      {item.state === 'failed' && <p className="mt-1 text-xs text-red-700">{FAILURES[item.error] || FAILURES.unreadable_file}</p>}
      {item.state !== 'failed' && (item.removedColumns > 0 || notes.length > 0) && (
        <ul className="mt-1 space-y-0.5">
          {item.removedColumns > 0 && (
            <li className="text-xs text-gray-600 flex items-center gap-1"><ShieldCheck className="w-3 h-3 text-green-700" />{item.removedColumns} column{item.removedColumns === 1 ? '' : 's'} of personal details removed on your computer</li>
          )}
          {notes.map((n, i) => <li key={i} className="text-xs text-amber-700 flex items-start gap-1"><AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />{n}</li>)}
        </ul>
      )}
    </div>
  );
}

export default function FileUploadZone({ insurer, items, onAddFiles, onRemove }) {
  const [dragging, setDragging] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const inputRef = useRef();
  const done = items.length > 0 && items.every((i) => i.state === 'received');

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex items-center gap-3 mb-3">
        <InsurerLogo insurer={insurer} size={7} />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-gray-900">{insurer.name}</div>
          <div className="flex items-center gap-3">
            {insurer.portal_url ? (
              <a href={insurer.portal_url} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-0.5">
                Open portal <ExternalLink className="w-3 h-3" />
              </a>
            ) : null}
            {insurer.steps && insurer.steps.length > 0 && (
              <button onClick={() => setGuideOpen((v) => !v)} className="text-xs text-gray-500 hover:text-gray-800 flex items-center gap-0.5">
                How to download your file <ChevronDown className={`w-3 h-3 transition-transform ${guideOpen ? 'rotate-180' : ''}`} />
              </button>
            )}
          </div>
        </div>
        {done && <CheckCircle className="w-4 h-4 text-green-500" />}
      </div>

      {guideOpen && (
        <div className="mb-3 rounded bg-gray-50 border border-gray-200 px-3 py-2">
          <ol className="list-decimal pl-4 space-y-1 text-xs text-gray-700">
            {insurer.steps.map((s, i) => <li key={i} dangerouslySetInnerHTML={{ __html: s }} />)}
          </ol>
          {insurer.format && <p className="mt-2 text-xs text-gray-500">{insurer.format}</p>}
          {insurer.hint && <p className="mt-1 text-xs text-gray-500">{insurer.hint}</p>}
        </div>
      )}

      {items.length > 0 && <div className="space-y-1.5 mb-2">{items.map((it) => <FileRow key={it.clientId} item={it} insurer={insurer} onRemove={onRemove} />)}</div>}

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length) onAddFiles(insurer.key, e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        className={`border-2 border-dashed rounded cursor-pointer flex items-center justify-center gap-2 py-3 transition-colors ${
          dragging ? 'border-blue-400 bg-blue-50' : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
        }`}
      >
        <Upload className="w-3.5 h-3.5 text-gray-400" />
        <span className="text-xs text-gray-500">{items.length > 0 ? 'Add more files' : 'Drop Excel/CSV or click to browse'}</span>
        <input ref={inputRef} type="file" accept=".xlsx,.xls,.csv" multiple className="hidden"
          onChange={(e) => { if (e.target.files.length) onAddFiles(insurer.key, e.target.files); e.target.value = ''; }} />
      </div>
    </div>
  );
}
