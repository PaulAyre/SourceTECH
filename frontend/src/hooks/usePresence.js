import { useEffect, useRef } from 'react';
import { URL_CODE } from '../lib/api.js';

/**
 * 3.1.0: tells the SourceTECH admin, live, that the vendor is on the page, which insurers
 * they have ticked and how far each file has got. Stage and progress only: never a file
 * name or anything from inside a file. Sent shortly after every change and every 8
 * seconds; on leaving the page a beacon says so.
 */
export function usePresence({ step, selected, items }) {
  const latest = useRef(null);
  const url = `/${URL_CODE}/presence`;
  latest.current = {
    step,
    selected,
    // this visit's files only: earlier visits' files are already on the admin's slots
    items: items.filter((i) => i.state !== 'duplicate' && !i.fromEarlier).map((i) => ({ id: i.clientId, insurer: i.insurerKey, state: i.state, fraction: Math.round((i.fraction || 0) * 100) / 100 })),
  };

  const send = () => {
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(latest.current), keepalive: true }).catch(() => {});
  };

  // on every change, debounced
  const signature = JSON.stringify(latest.current);
  useEffect(() => {
    const t = setTimeout(send, 600);
    return () => clearTimeout(t);
  }, [signature]); // eslint-disable-line react-hooks/exhaustive-deps

  // heartbeat, and a goodbye when the page closes
  useEffect(() => {
    const beat = setInterval(send, 8000);
    const bye = () => {
      const body = new Blob([JSON.stringify({ ...latest.current, left: true })], { type: 'application/json' });
      navigator.sendBeacon(url, body);
    };
    window.addEventListener('pagehide', bye);
    return () => { clearInterval(beat); window.removeEventListener('pagehide', bye); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
