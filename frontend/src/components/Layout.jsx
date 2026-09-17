import { RefreshCw, LayoutDashboard, ShieldCheck } from 'lucide-react';

const NAV = [
  { key: 'upload', label: 'New Upload', icon: RefreshCw },
  { key: 'uploads', label: 'Your Uploads', icon: LayoutDashboard },
];

export default function Layout({ view, onNavigate, vendorName, version, children }) {
  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden">
      <aside className="w-56 flex-shrink-0 flex flex-col" style={{ backgroundColor: '#004225' }}>
        <div className="px-6 py-6 border-b border-white/10">
          <div className="flex items-center gap-2">
            <img src="/static/images/insuranceplus_wordmark_white.svg" alt="insurance+" className="h-6 w-auto"
              onError={(e) => { e.currentTarget.onerror = null; e.currentTarget.src = '/static/images/iPlus_white.png'; }} />
          </div>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          {NAV.map(({ key, label, icon: Icon }) => (
            <button key={key} onClick={() => onNavigate(key)}
              className={`flex items-center gap-3 px-3 py-2.5 rounded text-sm font-medium transition-colors w-full text-left ${
                view === key ? 'bg-white/20 text-white' : 'text-white/60 hover:text-white hover:bg-white/10'
              }`}>
              <Icon className="w-4 h-4 flex-shrink-0" />
              {label}
            </button>
          ))}
        </nav>
        <div className="px-6 py-4 border-t border-white/10">
          {vendorName && <div className="text-xs text-white/80 font-medium truncate" title={vendorName}>{vendorName}</div>}
          <div className="mt-1 flex items-center gap-1.5 text-[11px] text-white/50"><ShieldCheck className="w-3 h-3" /> Secure link{version ? ` · v${version}` : ''}</div>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
