"""SourceTECH v3 vendor API: the browser strips, the server never sees a raw file.

HARD RULES enforced here
  1. CUSTOMER FACING: no valuation data in any response. Every response below is
     built from named, whitelisted fields. Never return dict(row).
  2. Only browser-stripped workbooks are accepted. Each one is re-checked on
     arrival (strip_shared/shared/pii_recheck.py) BEFORE it is kept, and its
     relationship codes get the second, secret hash. A file that cannot be
     re-checked is deleted, never stored.
  3. Nothing stops the vendor doing the job: problems become note codes and a
     loud internal alert, not rejections.
"""
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import jsonify, request

_SHARED = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'strip_shared', 'shared')
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
import ref_tokens  # noqa: E402
import pii_recheck  # noqa: E402

logger = logging.getLogger(__name__)

MAX_CLEAN_UPLOAD_BYTES = int(os.environ.get('MAX_CLEAN_UPLOAD_MB', '60')) * 1024 * 1024
SUBMIT_GRACE_MINUTES = int(os.environ.get('SUBMIT_GRACE_MINUTES', '20'))
ALERT_EMAIL = os.environ.get('ALERT_EMAIL', '')
_CODE_RE = re.compile(r'^[a-z][a-z0-9_]{1,60}$')
_CLIENT_ID_RE = re.compile(r'^[A-Za-z0-9_-]{6,64}$')


def ref_token_secret() -> str:
    return os.environ.get('REF_TOKEN_SECRET', '')


def _safe_notes(raw) -> list:
    """Note CODES only. Anything that is not a plain code is dropped, so free text
    (which could carry a figure or a name) can never reach the vendor's screen."""
    out = []
    for n in raw or []:
        if not isinstance(n, dict):
            continue
        code = n.get('code')
        if not isinstance(code, str) or not _CODE_RE.match(code):
            continue
        item = {'code': code}
        if isinstance(n.get('count'), int) and 0 <= n['count'] < 10_000_000:
            item['count'] = n['count']
        out.append(item)
    return out[:40]


def register_v3(app, d):
    """d: namespace of helpers from app.py (get_db, INSURERS, ...)."""

    def migrate():
        db = d.get_db()
        d.add_column_if_missing_public(db, 'vendor_files', 'sha256', 'TEXT')
        d.add_column_if_missing_public(db, 'vendor_files', 'client_id', 'TEXT')
        d.add_column_if_missing_public(db, 'vendor_files', 'note_codes', 'TEXT')
        d.add_column_if_missing_public(db, 'vendor_files', 'strip_report', 'TEXT')
        db.execute('''
            CREATE TABLE IF NOT EXISTS submission_intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER NOT NULL,
                reference TEXT NOT NULL UNIQUE,
                client_ids TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                deadline_at DATETIME NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                FOREIGN KEY (vendor_id) REFERENCES vendors(id)
            )''')
        db.commit()
        db.close()

    migrate()

    def vendor_or_404(db, url_code):
        return db.execute("SELECT * FROM vendors WHERE url_code = ?", (url_code,)).fetchone()

    def file_out(row) -> dict:
        """WHITELIST. The only file fields a vendor ever sees."""
        try:
            notes = json.loads(row['note_codes']) if row['note_codes'] else []
        except (ValueError, TypeError):
            notes = []
        return {
            'id': row['id'],
            'name': row['original_filename'],
            'insurer': row['insurer'],
            'insurer_name': d.insurer_label(row['insurer']),
            'policy_count': row['policy_count'] or 0,
            'uploaded_at': row['uploaded_at'],
            'client_id': row['client_id'],
            'notes': notes,
        }

    def alert(subject: str, body: str):
        """Fail loudly. A finding here means the browser stripper missed something."""
        logger.error("STRIP ALERT: %s | %s", subject, body)
        if ALERT_EMAIL:
            try:
                d.send_email(ALERT_EMAIL, f"[SourceTECH ALERT] {subject}", f"<pre>{body}</pre>")
            except Exception as e:  # the alert failing must not fail the upload
                logger.error("STRIP ALERT email failed: %s", e)

    # ------------------------------------------------------------------ config
    @app.route('/<url_code>/app-config')
    def v3_app_config(url_code):
        db = d.get_db()
        vendor = vendor_or_404(db, url_code)
        if not vendor:
            db.close()
            return jsonify({'error': 'Invalid link'}), 404
        rows = db.execute("SELECT * FROM vendor_files WHERE vendor_id = ? ORDER BY uploaded_at ASC, id ASC", (vendor['id'],)).fetchall()
        latest = db.execute('''SELECT reference, submitted_at, file_count, pavtech_status FROM submissions
                               WHERE vendor_id = ? ORDER BY submitted_at DESC LIMIT 1''', (vendor['id'],)).fetchone()
        pending = db.execute('''SELECT reference, created_at, client_ids FROM submission_intents
                                WHERE vendor_id = ? AND state = 'pending' ORDER BY id DESC LIMIT 1''', (vendor['id'],)).fetchone()
        db.close()

        preselected = []
        if 'selected_insurers' in vendor.keys() and vendor['selected_insurers']:
            try:
                preselected = d.clean_insurer_keys(json.loads(vendor['selected_insurers']))
            except (ValueError, TypeError):
                preselected = []

        profiles = d.insurer_profiles()
        insurers = [{
            'key': i['key'], 'name': i['name'], 'badge': i.get('badge', ''), 'color': i.get('color', '#004225'),
            'portal_url': i.get('portal_url', ''), 'steps': i.get('steps', []), 'format': i.get('format', ''), 'hint': i.get('hint', ''),
            # header NAMES only: what the insurer's own export calls its columns
            'alternatives': [sorted(m.keys()) for m in profiles.get(i['key'], []) if isinstance(m, dict) and m],
        } for i in d.INSURERS]

        rules = ref_tokens.load_rules()
        return jsonify({
            'version': d.APP_VERSION,
            'vendor_name': vendor['vendor_name'],
            'app_key': ref_tokens.APP_KEY,
            'rules_version': rules.get('version'),
            'insurers': insurers,
            'other_key': d.OTHER_KEY,
            'preselected': preselected,
            'files': [file_out(r) for r in rows],
            'state': d.vendor_processing_state(vendor['status']),
            'latest_submission': ({
                'reference': latest['reference'], 'submitted_at': latest['submitted_at'],
                'file_count': latest['file_count'], 'state': d.vendor_processing_state(latest['pavtech_status']),
            } if latest else None),
            'pending_submission': ({'reference': pending['reference'], 'submitted_at': pending['created_at'],
                                    'expected': len(json.loads(pending['client_ids']))} if pending else None),
            'grace_minutes': SUBMIT_GRACE_MINUTES,
        })

    # ------------------------------------------------------------ clean upload
    @app.route('/<url_code>/clean-upload', methods=['POST'])
    def v3_clean_upload(url_code):
        db = d.get_db()
        vendor = vendor_or_404(db, url_code)
        if not vendor:
            db.close()
            return jsonify({'error': 'Invalid link'}), 404

        f = request.files.get('file')
        if not f:
            db.close()
            return jsonify({'error': 'No file provided', 'code': 'no_file'}), 400
        insurer_key = (request.form.get('insurer') or '').strip().lower()
        if insurer_key not in d.INSURER_KEYS:
            insurer_key = d.OTHER_KEY
        client_id = request.form.get('client_id') or ''
        if not _CLIENT_ID_RE.match(client_id):
            client_id = secrets.token_urlsafe(9)
        try:
            report_in = json.loads(request.form.get('safe_report') or '{}')
        except ValueError:
            report_in = {}

        data = f.read(MAX_CLEAN_UPLOAD_BYTES + 1)
        if len(data) > MAX_CLEAN_UPLOAD_BYTES:
            db.close()
            return jsonify({'error': 'File too large', 'code': 'too_large'}), 413
        if data[:2] != b'PK':
            # Only a rebuilt .xlsx is ever accepted. A raw .csv/.xls means the browser
            # stripper did not run; it is refused WITHOUT being written to disk.
            db.close()
            alert('Non-stripped upload refused', f'vendor={url_code} first_bytes={data[:8]!r}')
            return jsonify({'error': 'Only files prepared by this page can be received', 'code': 'not_stripped'}), 415

        sha = hashlib.sha256(data).hexdigest()
        dup = db.execute("SELECT * FROM vendor_files WHERE vendor_id = ? AND sha256 = ?", (vendor['id'], sha)).fetchone()
        if dup:
            if dup['client_id'] != client_id:
                db.execute("UPDATE vendor_files SET client_id = ? WHERE id = ?", (client_id, dup['id']))
                db.commit()
                dup = db.execute("SELECT * FROM vendor_files WHERE id = ?", (dup['id'],)).fetchone()
            out = file_out(dup)
            db.close()
            maybe_finalize_for(vendor['id'])
            return jsonify({'success': True, 'duplicate': True, 'file': out})

        vendor_dir = d.get_vendor_upload_dir(url_code)
        tmp_path = vendor_dir / f".incoming_{secrets.token_hex(8)}.xlsx"
        tmp_path.write_bytes(data)
        del data
        try:
            res = pii_recheck.recheck_and_rehash(str(tmp_path), ref_tokens.APP_KEY, ref_token_secret())
        except ValueError as e:
            tmp_path.unlink(missing_ok=True)
            db.close()
            return jsonify({'error': 'No policy table was found in this file', 'code': str(e) or 'no_table_found'}), 422
        except Exception as e:  # unreadable / corrupt: never keep a file we could not check
            tmp_path.unlink(missing_ok=True)
            db.close()
            logger.error("clean-upload recheck crashed for %s: %s", url_code, e)
            return jsonify({'error': 'This file could not be read', 'code': 'unreadable_file'}), 422

        notes = _safe_notes(report_in.get('notes'))
        real_findings = [x for x in res['findings'] if x.get('kind') != 'config']
        if real_findings:
            notes.append({'code': 'server_removed_personal_details', 'count': sum(x.get('count', 1) for x in real_findings)})
            alert('Browser stripper missed personal details',
                  f"vendor={url_code} insurer={insurer_key} rules={report_in.get('rulesVersion')} findings={json.dumps(real_findings)}")
        if res['token_columns_dropped']:
            alert('REF_TOKEN_SECRET not set', f'vendor={url_code}: relationship codes were DROPPED, not stored. Set REF_TOKEN_SECRET (32+ chars).')

        # The v2 validator assumes the header is on row 1 and false-alarms on insurer
        # exports with title rows, so it is not used here. PavTECH does the real checking.
        policy_count = res.get('rows', 0)

        n = db.execute("SELECT COUNT(*) FROM vendor_files WHERE vendor_id = ? AND insurer = ?", (vendor['id'], insurer_key)).fetchone()[0] + 1
        final_name = f"{insurer_key}_{n}.xlsx"
        while (vendor_dir / final_name).exists():
            n += 1
            final_name = f"{insurer_key}_{n}.xlsx"
        final_path = vendor_dir / final_name
        tmp_path.rename(final_path)

        strip_report = {k: report_in.get(k) for k in ('rulesVersion', 'rows', 'columnsKept', 'columnsRemoved', 'removedByReason', 'cellsBlanked', 'ms')
                        if isinstance(report_in.get(k), (int, float, str, dict))}
        strip_report['server_findings'] = real_findings
        strip_report['tokens_rehashed'] = res['tokens_rehashed']

        db.execute('''
            INSERT INTO vendor_files
            (vendor_id, filename, original_filename, file_path, file_size, status, validation_warnings,
             pii_report, policy_count, processing_summary, insurer, sha256, client_id, note_codes, strip_report)
            VALUES (?, ?, ?, ?, ?, 'valid', '[]', ?, ?, '[]', ?, ?, ?, ?, ?)
        ''', (vendor['id'], final_name, final_name, str(final_path), final_path.stat().st_size,
              json.dumps({'client_side': True, 'columns_removed': strip_report.get('columnsRemoved')}),
              policy_count, insurer_key, sha, client_id, json.dumps(notes), json.dumps(strip_report)))
        db.execute("UPDATE vendors SET last_submission_at = CURRENT_TIMESTAMP WHERE id = ?", (vendor['id'],))
        db.commit()
        row = db.execute("SELECT * FROM vendor_files WHERE vendor_id = ? AND sha256 = ?", (vendor['id'], sha)).fetchone()
        out = file_out(row)
        deal_id = vendor['deal_id'] if 'deal_id' in vendor.keys() else None
        db.close()

        if deal_id:
            d.notify_data_received(deal_id, file_url=d.upload_url(url_code))
        maybe_finalize_for(vendor['id'])
        return jsonify({'success': True, 'duplicate': False, 'file': out})

    # ---------------------------------------------------------------- submit
    def start_processing(vendor_id: int, reference: str, missing: int = 0, expected: int = 0):
        db = d.get_db()
        vendor = db.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        files, _total = d.build_working_set(db, vendor_id)
        file_paths = [Path(f['file_path']) for f in files if Path(f['file_path']).exists()]
        extra = []
        if missing:
            extra.append(f"{missing} of {expected} files the vendor added never finished uploading (their browser was closed or lost connection). "
                         f"This run used the {len(file_paths)} that arrived. The vendor's link invites them to add the rest, which re-runs it.")
        for f in files:
            try:
                for nte in json.loads(f['note_codes'] or '[]'):
                    if nte.get('code') in ('server_removed_personal_details', 'needed_column_held_names', 'file_needs_a_look', 'expected_columns_missing'):
                        extra.append(f"{f['original_filename']}: {nte['code'].replace('_', ' ')}" + (f" ({nte['count']})" if nte.get('count') else ''))
            except (ValueError, TypeError):
                pass
        if not file_paths:
            db.execute("UPDATE vendors SET status = 'error' WHERE id = ?", (vendor_id,))
            db.execute("INSERT INTO submissions (vendor_id, file_count, pavtech_status, validation_errors, reference) VALUES (?, 0, 'error', ?, ?)",
                       (vendor_id, 'No files arrived before the submit window closed', reference))
            db.commit()
            vd = dict(vendor)
            db.close()
            d.send_dm_notification(to_email=vd['dm_email'], dm_name=vd['dm_name'], vendor_name=vd['vendor_name'], url_code=vd['url_code'],
                                   valuation=None, error='The vendor pressed Submit but none of their files finished uploading.', extra_notes=extra)
            return
        db.execute("UPDATE vendors SET status = 'processing', last_submission_at = ? WHERE id = ?", (datetime.now().isoformat(), vendor_id))
        db.commit()
        vd, fl = dict(vendor), [dict(f) for f in files]
        db.close()
        threading.Thread(target=d.process_batch_with_pavtech, args=(vd, file_paths, fl, reference, extra), daemon=True).start()

    def claim(reference: str, new_state: str) -> bool:
        """Atomic: only one caller (finalize, last upload, timer, or another gunicorn worker) wins."""
        db = d.get_db()
        cur = db.execute("UPDATE submission_intents SET state = ? WHERE reference = ? AND state = 'pending'", (new_state, reference))
        db.commit()
        won = cur.rowcount == 1
        db.close()
        return won

    def arrived(db, vendor_id: int, client_ids: list) -> int:
        if not client_ids:
            return 0
        q = ','.join('?' for _ in client_ids)
        return db.execute(f"SELECT COUNT(DISTINCT client_id) FROM vendor_files WHERE vendor_id = ? AND client_id IN ({q})", (vendor_id, *client_ids)).fetchone()[0]

    def maybe_finalize_for(vendor_id: int):
        db = d.get_db()
        intents = db.execute("SELECT * FROM submission_intents WHERE vendor_id = ? AND state = 'pending'", (vendor_id,)).fetchall()
        ready = []
        for it in intents:
            ids = json.loads(it['client_ids'])
            if arrived(db, vendor_id, ids) >= len(ids):
                ready.append((it['reference'], len(ids)))
        db.close()
        for reference, n in ready:
            if claim(reference, 'finalized'):
                start_processing(vendor_id, reference, 0, n)

    def sweep_overdue():
        """Submit was pressed, the tab died: run with what arrived and tell the DM."""
        db = d.get_db()
        rows = db.execute("SELECT * FROM submission_intents WHERE state = 'pending' AND deadline_at <= ?", (datetime.now().isoformat(),)).fetchall()
        todo = []
        for it in rows:
            ids = json.loads(it['client_ids'])
            todo.append((it['vendor_id'], it['reference'], len(ids) - arrived(db, it['vendor_id'], ids), len(ids)))
        db.close()
        for vendor_id, reference, missing, expected in todo:
            if claim(reference, 'timed_out' if missing else 'finalized'):
                start_processing(vendor_id, reference, max(missing, 0), expected)

    @app.route('/<url_code>/submit-intent', methods=['POST'])
    def v3_submit_intent(url_code):
        db = d.get_db()
        vendor = vendor_or_404(db, url_code)
        if not vendor:
            db.close()
            return jsonify({'error': 'Invalid link'}), 404
        body = request.get_json(silent=True) or {}
        client_ids = [c for c in (body.get('client_ids') or []) if isinstance(c, str) and _CLIENT_ID_RE.match(c)][:200]
        if not client_ids:
            db.close()
            return jsonify({'error': 'Nothing to submit yet', 'code': 'no_files'}), 400
        now = datetime.now()
        reference = f"ST-{now.strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"
        # a newer Submit replaces any intent still waiting
        db.execute("UPDATE submission_intents SET state = 'superseded' WHERE vendor_id = ? AND state = 'pending'", (vendor['id'],))
        db.execute("INSERT INTO submission_intents (vendor_id, reference, client_ids, deadline_at) VALUES (?, ?, ?, ?)",
                   (vendor['id'], reference, json.dumps(client_ids), (now + timedelta(minutes=SUBMIT_GRACE_MINUTES)).isoformat()))
        db.commit()
        vid = vendor['id']
        db.close()
        t = threading.Timer(SUBMIT_GRACE_MINUTES * 60 + 5, sweep_overdue)
        t.daemon = True
        t.start()
        maybe_finalize_for(vid)
        return jsonify({'success': True, 'reference': reference, 'submitted_at': now.isoformat(), 'expected': len(client_ids)})

    @app.route('/<url_code>/submit-finalize', methods=['POST'])
    def v3_submit_finalize(url_code):
        """The browser says every file is up. Idempotent; the last upload usually got here first."""
        db = d.get_db()
        vendor = vendor_or_404(db, url_code)
        if not vendor:
            db.close()
            return jsonify({'error': 'Invalid link'}), 404
        vid = vendor['id']
        db.close()
        maybe_finalize_for(vid)
        return jsonify({'success': True})

    sweep_overdue()          # on boot: pick up anything a restart interrupted
    return {'sweep_overdue': sweep_overdue, 'maybe_finalize_for': maybe_finalize_for}
