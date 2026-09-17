import { useEffect, useState } from 'react';
import Layout from './components/Layout.jsx';
import Wizard from './pages/Wizard.jsx';
import Uploads from './pages/Uploads.jsx';
import { getConfig } from './lib/api.js';
import { useUploads } from './hooks/useUploads.js';

function Shell({ config }) {
  const uploads = useUploads(config);
  const [view, setView] = useState('upload');
  return (
    <Layout view={view} onNavigate={setView} vendorName={config.vendor_name} version={config.version}>
      {/* both stay mounted so files keep processing while the vendor looks around */}
      <div className={view === 'upload' ? '' : 'hidden'}><Wizard config={config} uploads={uploads} /></div>
      <div className={view === 'uploads' ? '' : 'hidden'}><Uploads config={config} uploads={uploads} onNew={() => setView('upload')} /></div>
    </Layout>
  );
}

export default function App() {
  const [config, setConfig] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { getConfig().then(setConfig).catch((e) => setError(e.status === 404 ? 'This link is invalid or has expired.' : 'We could not load this page. Please refresh.')); }, []);
  if (error) return <div className="fixed inset-0 flex items-center justify-center bg-gray-50"><div className="bg-white border border-gray-200 rounded-lg p-8 text-sm text-gray-700 max-w-sm text-center">{error}</div></div>;
  if (!config) return <div className="fixed inset-0 flex items-center justify-center"><div className="w-8 h-8 border-4 border-slate-200 border-t-slate-800 rounded-full animate-spin" /></div>;
  return <Shell config={config} />;
}
