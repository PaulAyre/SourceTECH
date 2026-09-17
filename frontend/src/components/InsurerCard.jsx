import { Check } from 'lucide-react';

export function InsurerLogo({ insurer, size = 8 }) {
  const px = size * 4;
  return (
    <div className="rounded flex items-center justify-center text-white font-bold flex-shrink-0"
      style={{ backgroundColor: insurer.color || '#004225', width: px, height: px, fontSize: px <= 24 ? 8 : 10, letterSpacing: '-0.02em' }}>
      {(insurer.badge || insurer.name.charAt(0)).slice(0, 4)}
    </div>
  );
}

export default function InsurerCard({ insurer, selected, onToggle, fileCount = 0 }) {
  return (
    <button
      onClick={() => onToggle(insurer.key)}
      className={`relative flex flex-col items-start p-4 rounded-lg border-2 text-left transition-all w-full ${
        selected ? 'border-blue-600 bg-blue-50' : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50'
      }`}
    >
      {selected && (
        <div className="absolute top-2 right-2 w-5 h-5 rounded-full bg-blue-600 flex items-center justify-center">
          <Check className="w-3 h-3 text-white" />
        </div>
      )}
      <div className="mb-2"><InsurerLogo insurer={insurer} size={8} /></div>
      <div className="text-sm font-semibold text-gray-900">{insurer.name}</div>
      <div className="text-xs text-gray-500 mt-0.5">{fileCount ? `${fileCount} file${fileCount === 1 ? '' : 's'} added` : 'Adviser portal'}</div>
    </button>
  );
}
