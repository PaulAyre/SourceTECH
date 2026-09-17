// Every call the vendor page makes. The server only ever receives the REBUILT,
// stripped workbook and a counts-only report. The original file never leaves the browser.
export const URL_CODE = window.location.pathname.split('/').filter(Boolean)[0] || '';
const base = `/${URL_CODE}`;

async function json(res) {
  let body = {};
  try { body = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) throw Object.assign(new Error(body.error || `Request failed (${res.status})`), { code: body.code, status: res.status });
  return body;
}

export const getConfig = () => fetch(`${base}/app-config`, { cache: 'no-store' }).then(json);
export const getStatus = () => fetch(`${base}/status`, { cache: 'no-store' }).then(json);
export const deleteFile = (id) => fetch(`${base}/files/${id}`, { method: 'DELETE' }).then(json);
export const submitIntent = (clientIds) => fetch(`${base}/submit-intent`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_ids: clientIds }) }).then(json);
export const submitFinalize = () => fetch(`${base}/submit-finalize`, { method: 'POST' }).then(json);

/** XHR (not fetch) so the vendor gets a real upload percentage. */
export function uploadClean({ cleanFile, insurerKey, clientId, safeReport, onProgress }) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', cleanFile, cleanFile.name);
    form.append('insurer', insurerKey);
    form.append('client_id', clientId);
    form.append('safe_report', JSON.stringify(safeReport || {}));
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${base}/clean-upload`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch (_) { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(Object.assign(new Error(body.error || `Upload failed (${xhr.status})`), { code: body.code, status: xhr.status }));
    };
    xhr.onerror = () => reject(Object.assign(new Error('Connection lost'), { code: 'network' }));
    xhr.send(form);
  });
}
