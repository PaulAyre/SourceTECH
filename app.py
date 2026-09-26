"""
SourceTECH - Vendor Portfolio Upload Portal
Main Flask application
"""
from flask import Flask, request, render_template, redirect, url_for, jsonify, session, send_from_directory
from functools import wraps
from werkzeug.utils import secure_filename
from validator import validate_portfolio_file
from pii_stripper import strip_pii
from pavtech_client import PavTechClient
from excel_parser import extract_valuation_summary
from email_service import send_dm_notification, send_email
from dealtech_client import notify_data_received
import sqlite3
import secrets
import os
import json
import difflib
from pathlib import Path
from datetime import datetime
import threading
import logging
import shutil

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Single source of truth for the app version: /health, page titles and the
# static-asset cache-buster all read this.
APP_VERSION = '3.0.2'  # 3.0.2: admin vendor page links each run to the PavTECH web app; 3.0.1: DealTECH data-received webhook path fixed


def pavtech_run_url(vendor_name, batch_id, base=None):
    """3.0.2 (Paul, 26 Sep 2026): deep link into the PavTECH web app for one run. PavTECH v3.353
    restores a run from the URL hash (#vendor=<run directory>&batch=<batch id>); the run
    directory is this vendor's name as SourceTECH sent it."""
    from urllib.parse import quote
    if not vendor_name or not batch_id:
        return ''
    root = (base or PAVTECH_API_URL or '').rstrip('/')
    return f"{root}/#vendor={quote(str(vendor_name))}&batch={quote(str(batch_id))}"

# v3: the vendor page is a built React app (static/app) that strips personal
# details IN THE BROWSER. Set SOURCETECH_UI=legacy to roll back to the v2 server
# rendered page AND re-enable the raw /upload endpoint. In v3 mode the server
# refuses raw files: it never sees, and never stores, an un-stripped upload.
LEGACY_UI = os.environ.get('SOURCETECH_UI', '').strip().lower() == 'legacy'


@app.context_processor
def inject_app_version():
    return {'app_version': APP_VERSION}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
PAVTECH_API_URL = os.environ.get('PAVTECH_API_URL', 'http://localhost:5000')
UPLOADS_DIR = os.environ.get('UPLOADS_DIR', 'uploads')  # On Render set to /data/uploads (persistent disk); local dev falls back to ./uploads
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme')  # Change in production!
# Shared secret for the DealTECH <-> SourceTECH server-to-server API. When set,
# the /api/vendors* endpoints require a matching X-Webhook-Secret header (and the
# DealTECH callback in dealtech_client.py sends it). When unset (dev), the API is
# open and a warning is logged.
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', '')
# Public base URL of THIS SourceTECH service, used to build absolute upload links.
PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')

# Ensure directories exist
Path(UPLOADS_DIR).mkdir(parents=True, exist_ok=True)

# Database lives inside uploads dir for persistence (both local and Render)
DATABASE = os.environ.get('DATABASE', str(Path(UPLOADS_DIR) / 'sourcetech.db'))
TEMP_DIR = os.environ.get('TEMP_DIR', str(Path(UPLOADS_DIR) / 'temp'))
Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)

pavtech = PavTechClient(PAVTECH_API_URL, temp_dir=TEMP_DIR)


# ─────────────────────────────────────────────────────────────
# INSURER CATALOGUE (single source of truth)
# Extracted verbatim from the vendor upload page's Download Guide sidebar.
# Drives: the adviser upload page (checkboxes + per-insurer panels), the admin
# pre-selection checkboxes, and validation of insurer tags coming back on upload
# and via the DealTECH bridge. If an insurer's portal_url is empty, the UI renders
# a disabled "portal link TBC" button rather than a broken/fabricated link.
# ─────────────────────────────────────────────────────────────
INSURERS = [
    {
        "key": "aia", "name": "AIA Australia", "badge": "AIA", "color": "#c8102e",
        "search": "aia australia",
        "portal_url": "https://myaia.aia.com.au/en/login",
        "steps": [
            "Log in to <strong>AIA Adviser Portal</strong>",
            "From the left menu, click <strong>Policies</strong>",
            "Select <strong>In-force</strong> to view active policies",
            "Click <strong>Export</strong> to download as Excel/CSV",
            "Drop the downloaded file in the box below",
        ],
        "format": "Formats: XLS, CSV, PDF",
        "hint": "",
    },
    {
        "key": "tal", "name": "TAL (incl. Asteron)", "badge": "TAL", "color": "#003087",
        "search": "tal tower asteron",
        "portal_url": "https://adviser.tal.com.au/",
        "steps": [
            "Log in to <strong>TAL Adviser Centre</strong>",
            "Navigate to <strong>Inforce Management</strong>",
            "View your in-force dashboard with all active policies",
            "Use <strong>Secure File Transfer</strong> to export data, or ask your TAL BDM for a data extract",
            "Drop the downloaded file in the box below",
        ],
        "format": "Formats: Dashboard view, Secure File Transfer",
        "hint": "Asteron Life policies are now managed through TAL Adviser Centre since 2021.",
    },
    {
        "key": "zurich", "name": "Zurich / OnePath", "badge": "ZUR", "color": "#003399",
        "search": "zurich onepath",
        "portal_url": "https://advisers.zurich.com.au/resources/adviser-portal",
        "steps": [
            "Log in to <strong>The Adviser Portal</strong> (combined Zurich + OnePath view)",
            "Navigate to <strong>Portfolio Insights</strong>",
            "View your in-force book by policies, premium, and sum insured",
            "Export policy data to PDF or request a data extract from your BDM",
            "Drop the downloaded file in the box below",
        ],
        "format": "Formats: PDF, Portfolio reports",
        "hint": "OnePath life insurance is now fully integrated under Zurich. MFA required.",
    },
    {
        "key": "mlc", "name": "MLC Life (Acenda)", "badge": "MLC", "color": "#e31837",
        "search": "mlc acenda nippon",
        "portal_url": "https://partner.acenda.com.au",
        "steps": [
            "Log in to <strong>Acenda Adviser Portal</strong>",
            "Choose <strong>Adviser login</strong> (top right)",
            "Go to the <strong>Reporting tab</strong> to generate a client report",
            "Download the report",
            "Drop the downloaded file in the box below",
        ],
        "format": "Formats: Client reports",
        "hint": "MLC Limited is now Acenda (formerly Nippon Life Insurance AU/NZ).",
    },
    {
        "key": "metlife", "name": "MetLife Australia", "badge": "MET", "color": "#00a94f",
        "search": "metlife",
        "portal_url": "https://www.metlife.com.au/login/",
        "steps": [
            "Log in to the <strong>MetLife Adviser Portal</strong>",
            "Navigate to your <strong>client portfolio</strong> section",
            "Generate and download your in-force report",
            "Drop the downloaded file in the box below",
        ],
        "format": "Formats: Contact BDM for export",
        "hint": "",
    },
    {
        "key": "clearview", "name": "ClearView", "badge": "CLV", "color": "#0077c8",
        "search": "clearview",
        "portal_url": "https://adviserportal.clearview.com.au/Profile/Login",
        "steps": [
            "Log in to the <strong>ClearView Adviser Portal</strong>",
            "Access reporting for ClearChoice and LifeSolutions products",
            "Generate your in-force report from the reporting section",
            "Download as Excel or CSV",
            "Drop the downloaded file in the box below",
        ],
        "format": "Formats: PDF, Excel, CSV (via SSRS)",
        "hint": "",
    },
    {
        "key": "resolution", "name": "Resolution Life (ex-AMP)", "badge": "RES", "color": "#5c2d91",
        "search": "resolution life amp",
        "portal_url": "https://advisor.resolutionlife.com.au/CentralPortalsLogin/NewLoginRLANZ",
        "steps": [
            "Log in to <strong>My Resolution Life</strong>",
            "View dashboard with Renewal &amp; Overdue notices",
            "Select <strong>View &gt; Statements and correspondence</strong>",
            "Select the relevant product and download documents",
            "Drop the downloaded file in the box below",
        ],
        "format": "Formats: Statements (PDF)",
        "hint": "Now part of Acenda Group. The old AMP Planner Portal no longer works for Resolution Life products. MFA is enforced.",
    },
    {
        "key": "bt", "name": "BT Financial Group", "badge": "BT", "color": "#d5002b",
        "search": "bt westpac financial group panorama",
        "portal_url": "https://www.panoramaadviser.com.au",
        "steps": [
            "Log in to <strong>BT Panorama</strong> adviser site",
            "Navigate to the <strong>Reporting</strong> section",
            "Generate your in-force insurance policy report",
            "Download the report",
            "Drop the downloaded file in the box below",
        ],
        "format": "Formats: Xplan integration, platform reports",
        "hint": "BT insurance admin has transferred to Australian Group Insurances (AGI) since Aug 2025.",
    },
    {
        "key": "neos", "name": "NobleOak / NEOS", "badge": "NEO", "color": "#2e5090",
        "search": "nobleoak neos futura",
        "portal_url": "https://portal.neoslife.com.au/",
        "steps": [
            "Log in to the <strong>NEOS Adviser Portal</strong>",
            "View your integrated dashboard of all plans",
            "Export your in-force policy data",
            "Drop the downloaded file in the box below",
        ],
        "format": "Formats: Contact adviser services",
        "hint": "NobleOak advised channel operates through NEOS / Futura Protection platforms.",
    },
]

INSURER_KEYS = {ins["key"] for ins in INSURERS}
INSURER_NAME_BY_KEY = {ins["key"]: ins["name"] for ins in INSURERS}

# Special catch-all tag for files that do not map to any named insurer. Uploaded
# via the always-available "Other / unassigned" slot; a valid tag value so
# downstream (PavTECH/DealTECH) can see the file is deliberately unclassified.
OTHER_KEY = "other"
OTHER_LABEL = "Other / unassigned"


def insurer_label(key):
    """Human label for a stored insurer tag (catalogue name, 'Other / unassigned', or None)."""
    if not key:
        return None
    if key == OTHER_KEY:
        return OTHER_LABEL
    return INSURER_NAME_BY_KEY.get(key)


def clean_insurer_keys(raw) -> list:
    """Normalise + validate a list of insurer keys against the catalogue.

    Accepts a list (or None). Unknown keys are dropped. Order follows the
    catalogue so the stored/rendered order is stable. Returns a list of valid
    keys (possibly empty), never raises on bad input.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    try:
        wanted = {str(k).strip().lower() for k in raw if str(k).strip()}
    except TypeError:
        return []
    return [ins["key"] for ins in INSURERS if ins["key"] in wanted]


def get_db():
    """Get database connection with row factory."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    """Initialize the database schema."""
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_code TEXT UNIQUE NOT NULL,
            vendor_name TEXT NOT NULL,
            dm_email TEXT NOT NULL,
            dm_name TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            status TEXT DEFAULT 'pending',
            last_submission_at DATETIME,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            original_filename TEXT,
            cleaned_filename TEXT,
            pavtech_session_id TEXT,
            pavtech_batch_id TEXT,
            policy_count INTEGER,
            file_count INTEGER DEFAULT 1,
            validation_errors TEXT,
            pavtech_status TEXT,
            master_document_path TEXT,
            valuation_summary TEXT,
            processing_log TEXT,
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        );

        CREATE TABLE IF NOT EXISTS vendor_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'uploaded',
            validation_warnings TEXT,
            pii_report TEXT,
            policy_count INTEGER,
            processing_summary TEXT,
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        );

        CREATE INDEX IF NOT EXISTS idx_vendors_url_code ON vendors(url_code);
        CREATE INDEX IF NOT EXISTS idx_submissions_vendor_id ON submissions(vendor_id);
        CREATE INDEX IF NOT EXISTS idx_vendor_files_vendor_id ON vendor_files(vendor_id);
    ''')

    # Idempotent migration: link a vendor to its DealTECH deal (SourceTECH has no
    # migration framework, so we ALTER-if-missing). These let the DealTECH->
    # SourceTECH create API persist the deal, and the upload callback target it.
    # Each ALTER is wrapped so a failure is LOGGED LOUDLY and re-raised. A
    # migration that cannot apply must crash startup, never silently no-op.
    def add_column_if_missing(table: str, column: str, ddl: str):
        cols = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column in cols:
            return
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            logger.info("Migration: added %s.%s", table, column)
        except Exception as exc:
            logger.error("MIGRATION FAILED adding %s.%s: %s", table, column, exc)
            raise

    add_column_if_missing("vendors", "deal_id", "deal_id INTEGER")
    add_column_if_missing("vendors", "hubspot_deal_id", "hubspot_deal_id TEXT")
    # selected_insurers: JSON array of insurer keys the admin/DealTECH pre-ticked
    # for this vendor. Pre-checks the adviser's page; adviser can still change it.
    add_column_if_missing("vendors", "selected_insurers", "selected_insurers TEXT")
    # insurer: which insurer catalogue key an uploaded file belongs to (or NULL
    # for a plain/untagged upload). Insurer-specific downstream PavTECH parsing.
    add_column_if_missing("vendor_files", "insurer", "insurer TEXT")
    # reference: human-quotable submission reference (ST-YYYYMMDD-XXXX), generated
    # at submit time and shown on the vendor's receipt so support conversations
    # ("what's your reference?") can pin down the exact submission.
    add_column_if_missing("submissions", "reference", "reference TEXT")

    db.commit()
    db.close()
    logger.info("Database initialized")


# Initialize database on startup
with app.app_context():
    init_db()


# ─────────────────────────────────────────────────────────────
# ADMIN AUTHENTICATION
# ─────────────────────────────────────────────────────────────

def admin_required(f):
    """Decorator to require admin authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page."""
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        error = 'Invalid password'
    return render_template('admin/login.html', error=error)


@app.route('/admin/logout')
def admin_logout():
    """Admin logout."""
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


# ─────────────────────────────────────────────────────────────
# ADMIN DASHBOARD
# ─────────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard - overview of vendors and submissions."""
    db = get_db()

    # Get stats
    stats = {
        'total_vendors': db.execute("SELECT COUNT(*) FROM vendors").fetchone()[0],
        'pending_vendors': db.execute("SELECT COUNT(*) FROM vendors WHERE status = 'pending'").fetchone()[0],
        'processing_vendors': db.execute("SELECT COUNT(*) FROM vendors WHERE status = 'processing'").fetchone()[0],
        'complete_vendors': db.execute("SELECT COUNT(*) FROM vendors WHERE status = 'complete'").fetchone()[0],
    }

    # Get recent submissions
    recent_submissions = db.execute('''
        SELECT s.*, v.vendor_name, v.dm_name, v.url_code
        FROM submissions s
        JOIN vendors v ON s.vendor_id = v.id
        ORDER BY s.submitted_at DESC
        LIMIT 10
    ''').fetchall()

    db.close()
    return render_template('admin/dashboard.html', stats=stats, recent_submissions=recent_submissions)


@app.route('/admin/vendors')
@admin_required
def admin_vendors():
    """List all vendors."""
    db = get_db()
    vendors = db.execute('''
        SELECT v.*,
               COUNT(s.id) as submission_count,
               MAX(s.submitted_at) as last_submission
        FROM vendors v
        LEFT JOIN submissions s ON v.id = s.vendor_id
        GROUP BY v.id
        ORDER BY v.created_at DESC
    ''').fetchall()
    db.close()
    return render_template('admin/vendors.html', vendors=vendors)


@app.route('/admin/vendors/create', methods=['GET', 'POST'])
@admin_required
def admin_create_vendor():
    """Create a new vendor upload link."""
    if request.method == 'POST':
        vendor_name = request.form.get('vendor_name', '').strip()
        dm_email = request.form.get('dm_email', '').strip()
        dm_name = request.form.get('dm_name', '').strip()
        notes = request.form.get('notes', '').strip()
        selected = clean_insurer_keys(request.form.getlist('insurers'))
        selected_json = json.dumps(selected)

        if not vendor_name or not dm_email or not dm_name:
            return render_template('admin/create_vendor.html',
                                   error='All fields are required',
                                   form=request.form,
                                   insurers=INSURERS,
                                   selected_insurers=selected)

        # Generate unique URL code
        url_code = secrets.token_urlsafe(6)[:8].upper()

        db = get_db()
        try:
            db.execute('''
                INSERT INTO vendors (url_code, vendor_name, dm_email, dm_name, created_by, notes, selected_insurers)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (url_code, vendor_name, dm_email, dm_name, 'admin', notes, selected_json))
            db.commit()
        except sqlite3.IntegrityError:
            # URL code collision - try again
            url_code = secrets.token_urlsafe(6)[:8].upper()
            db.execute('''
                INSERT INTO vendors (url_code, vendor_name, dm_email, dm_name, created_by, notes, selected_insurers)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (url_code, vendor_name, dm_email, dm_name, 'admin', notes, selected_json))
            db.commit()

        db.close()

        # Redirect to success page showing the link
        return redirect(url_for('admin_vendor_created', url_code=url_code))

    return render_template('admin/create_vendor.html', insurers=INSURERS, selected_insurers=[])


@app.route('/admin/vendors/created/<url_code>')
@admin_required
def admin_vendor_created(url_code):
    """Show the created vendor link."""
    db = get_db()
    vendor = db.execute("SELECT * FROM vendors WHERE url_code = ?", (url_code,)).fetchone()
    db.close()

    if not vendor:
        return redirect(url_for('admin_vendors'))

    # Build the full upload URL
    upload_url = request.url_root.rstrip('/') + '/' + url_code

    return render_template('admin/vendor_created.html', vendor=vendor, upload_url=upload_url)


@app.route('/admin/vendors/<url_code>/live.json')
@admin_required
def admin_vendor_live(url_code):
    """v2.5.0 (Paul, 24 Sep 2026): what the vendor is doing RIGHT NOW, for the admin page's
    live panel. Every file as it lands (status, insurer, policy count, the validation
    warnings and PII columns removed, i.e. the same issues PavTECH's first stage raises),
    every submission with its PavTECH status. Polled every 5 seconds by vendor_detail.html."""
    db = get_db()
    vendor = db.execute("SELECT * FROM vendors WHERE url_code = ?", (url_code,)).fetchone()
    if not vendor:
        db.close()
        return jsonify({'error': 'no such vendor'}), 404
    files = db.execute("SELECT * FROM vendor_files WHERE vendor_id = ? ORDER BY uploaded_at DESC", (vendor['id'],)).fetchall()
    subs = db.execute("SELECT * FROM submissions WHERE vendor_id = ? ORDER BY submitted_at DESC", (vendor['id'],)).fetchall()
    db.close()

    def _j(v, default):
        try:
            return json.loads(v) if v else default
        except Exception:
            return default

    out_files = []
    for f in files:
        d = dict(f)
        pii = _j(d.get('pii_report'), {})
        out_files.append({
            'filename': d.get('original_filename') or d.get('filename'),
            'insurer': d.get('insurer'),
            'status': d.get('status'),
            'uploaded_at': d.get('uploaded_at'),
            'policy_count': d.get('policy_count'),
            'size': d.get('file_size'),
            'warnings': _j(d.get('validation_warnings'), []),
            'pii_removed': pii.get('columns_removed', []),
            'pii_anonymized': pii.get('columns_anonymized', []),
            'summary': _j(d.get('processing_summary'), []),
        })
    out_subs = [{
        'submitted_at': dict(s_)['submitted_at'], 'reference': dict(s_).get('reference'),
        'pavtech_status': dict(s_).get('pavtech_status'), 'pavtech_batch_id': dict(s_).get('pavtech_batch_id'),
        'file_count': dict(s_).get('file_count'), 'policy_count': dict(s_).get('policy_count'),
        'errors': dict(s_).get('validation_errors'),
    } for s_ in subs]
    return jsonify({
        'vendor': {'name': vendor['vendor_name'], 'status': vendor['status'],
                   'last_submission_at': vendor['last_submission_at'],
                   'hubspot_deal_id': vendor['hubspot_deal_id'] if 'hubspot_deal_id' in vendor.keys() else None},
        'files': out_files, 'submissions': out_subs,
        'server_time': datetime.utcnow().isoformat() + 'Z',
    })


@app.route('/admin/vendors/<url_code>')
@admin_required
def admin_vendor_detail(url_code):
    """View vendor details and submission history."""
    db = get_db()
    vendor = db.execute("SELECT * FROM vendors WHERE url_code = ?", (url_code,)).fetchone()

    if not vendor:
        db.close()
        return redirect(url_for('admin_vendors'))

    submissions = db.execute('''
        SELECT * FROM submissions
        WHERE vendor_id = ?
        ORDER BY submitted_at DESC
    ''', (vendor['id'],)).fetchall()

    db.close()

    upload_url = request.url_root.rstrip('/') + '/' + url_code
    return render_template('admin/vendor_detail.html', vendor=vendor, submissions=submissions, upload_url=upload_url,
                           pavtech_base=(PAVTECH_API_URL or '').rstrip('/'), pavtech_run_url=pavtech_run_url)


@app.route('/admin/vendors/<url_code>/delete', methods=['POST'])
@admin_required
def admin_vendor_delete(url_code):
    """v2.4.2 (23 Sep 2026): remove a vendor and everything under it. Admin only; the
    upload link stops resolving (404). Used to clear the July test vendors; there was
    no delete anywhere before."""
    import shutil
    db = get_db()
    vendor = db.execute("SELECT * FROM vendors WHERE url_code = ?", (url_code,)).fetchone()
    if not vendor:
        db.close()
        return redirect(url_for('admin_vendors'))
    vid = vendor['id']
    db.execute("DELETE FROM vendor_files WHERE vendor_id = ?", (vid,))
    db.execute("DELETE FROM submissions WHERE vendor_id = ?", (vid,))
    db.execute("DELETE FROM vendors WHERE id = ?", (vid,))
    db.commit()
    db.close()
    try:
        shutil.rmtree(Path(UPLOADS_DIR) / url_code, ignore_errors=True)
    except Exception as e:
        app.logger.warning(f"vendor {url_code} deleted from DB but upload dir not removed: {e}")
    app.logger.warning(f"ADMIN DELETE vendor {url_code} ({vendor['vendor_name']})")
    return redirect(url_for('admin_vendors'))


# ─────────────────────────────────────────────────────────────
# VENDOR PORTFOLIO MANAGER
# ─────────────────────────────────────────────────────────────

def get_vendor_upload_dir(url_code: str) -> Path:
    """Get the upload directory for a vendor."""
    vendor_dir = Path(UPLOADS_DIR) / url_code
    vendor_dir.mkdir(parents=True, exist_ok=True)
    return vendor_dir


def fuzzy_match_filename(new_filename: str, existing_filenames: list, threshold: float = 0.6) -> tuple:
    """
    Find potential filename matches using fuzzy matching.
    Returns (best_match, similarity_score) or (None, 0) if no good match.
    """
    if not existing_filenames:
        return None, 0

    # Normalize filenames for comparison (lowercase, remove extension)
    def normalize(fn):
        return Path(fn).stem.lower().replace('_', ' ').replace('-', ' ')

    new_norm = normalize(new_filename)

    best_match = None
    best_score = 0

    for existing in existing_filenames:
        existing_norm = normalize(existing)
        # Use SequenceMatcher for fuzzy matching
        score = difflib.SequenceMatcher(None, new_norm, existing_norm).ratio()
        if score > best_score:
            best_score = score
            best_match = existing

    if best_score >= threshold:
        return best_match, best_score
    return None, 0


def get_vendor_files(vendor_id: int) -> list:
    """Get all files for a vendor from database."""
    db = get_db()
    files = db.execute('''
        SELECT * FROM vendor_files
        WHERE vendor_id = ?
        ORDER BY uploaded_at DESC
    ''', (vendor_id,)).fetchall()
    db.close()
    return [dict(f) for f in files]


def build_working_set(db, vendor_id: int) -> tuple:
    """The deduped file set a Submit would send to PavTECH.

    All uploads newest first, keeping only the latest upload of each ORIGINAL
    filename (re-uploading an edited file with the same name replaces the older
    one). Shared by the Review endpoint and the Submit action so the review
    shows exactly what will be processed. Returns (working_rows, total_uploads).
    """
    all_files = db.execute('''
        SELECT * FROM vendor_files
        WHERE vendor_id = ?
        ORDER BY uploaded_at DESC, id DESC
    ''', (vendor_id,)).fetchall()

    seen_names = set()
    working = []
    for f in all_files:
        name = f['original_filename']
        if name in seen_names:
            continue  # older version of a same-named file; skip
        seen_names.add(name)
        working.append(f)
    return working, len(all_files)


def build_processing_summary(validation: dict, pii_report: dict) -> list:
    """
    Build a comprehensive list of processing steps for display.
    Returns list of summary strings describing what happened.
    """
    summary = []

    # Start with positives
    for positive in validation.get('positives', []):
        summary.append(positive)

    # PII removal
    cols_removed = pii_report.get('columns_removed', [])
    cols_anonymized = pii_report.get('columns_anonymized', [])

    if cols_removed:
        summary.append(f"🔒 Removed {len(cols_removed)} columns with personal info: {', '.join(cols_removed[:3])}{'...' if len(cols_removed) > 3 else ''}")
    if cols_anonymized:
        summary.append(f"🔒 Anonymized {len(cols_anonymized)} name columns")

    # Then warnings/assumptions
    for warning in validation.get('warnings', []):
        summary.append(warning)

    return summary


def vendor_processing_state(raw_status) -> str:
    """Map an internal vendor/submission status to a vendor-safe state string.

    Vendor-reachable pages and JSON only ever see one of these four values.
    Anything unrecognised falls back to 'pending' so a new internal status can
    never leak through to the vendor by accident.
    """
    return {
        'pending': 'pending',
        'processing': 'processing',
        'complete': 'processed',
        'error': 'needs_attention',
    }.get(raw_status or 'pending', 'pending')


@app.route('/<url_code>')
def upload_page(url_code):
    """Show portfolio manager page for vendor."""
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()

    if not vendor:
        db.close()
        return render_template('error.html',
            message="Invalid or expired link"), 404

    if not LEGACY_UI:
        db.close()
        # The React app reads everything it needs from /<url_code>/app-config.
        resp = send_from_directory(os.path.join(app.root_path, 'static', 'app'), 'index.html')
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    # Get existing files for this vendor
    files = db.execute('''
        SELECT * FROM vendor_files
        WHERE vendor_id = ?
        ORDER BY uploaded_at DESC
    ''', (vendor['id'],)).fetchall()

    db.close()

    # Which insurers were pre-selected (by admin / DealTECH) for this vendor.
    selected_insurers = []
    if 'selected_insurers' in vendor.keys() and vendor['selected_insurers']:
        try:
            selected_insurers = clean_insurer_keys(json.loads(vendor['selected_insurers']))
        except (ValueError, TypeError):
            logger.warning("Vendor %s has unparseable selected_insurers", url_code)
            selected_insurers = []

    files_out = []
    for f in files:
        d = dict(f)
        ins_key = d.get('insurer') if 'insurer' in d else None
        d['insurer_name'] = insurer_label(ins_key)
        files_out.append(d)

    # Group files by the tile they belong to: a known insurer key, else 'other'
    # (covers the "other" tag and any legacy untagged files). Used to render each
    # file inside its own tile instead of a shared list.
    files_by_insurer = {}
    for d in files_out:
        key = d.get('insurer')
        if key not in INSURER_KEYS:
            key = OTHER_KEY
        files_by_insurer.setdefault(key, []).append(d)

    # HARD RULE: this page is CUSTOMER FACING. The submission row (valuation
    # summary, master document path) is deliberately NOT loaded or passed to the
    # template. The vendor only ever sees a neutral processing state.
    return render_template('upload.html',
        vendor=vendor,
        url_code=url_code,
        files=files_out,
        files_by_insurer=files_by_insurer,
        processing_state=vendor_processing_state(vendor['status']),
        insurers=INSURERS,
        selected_insurers=selected_insurers
    )


@app.route('/<url_code>/files')
def list_files(url_code):
    """API: Get list of files for vendor."""
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()

    if not vendor:
        db.close()
        return jsonify({'error': 'Invalid link'}), 404

    files = db.execute('''
        SELECT id, filename, original_filename, file_size, uploaded_at,
               status, validation_warnings, pii_report, policy_count, processing_summary, insurer
        FROM vendor_files
        WHERE vendor_id = ?
        ORDER BY uploaded_at DESC
    ''', (vendor['id'],)).fetchall()

    db.close()

    out = []
    for f in files:
        d = dict(f)
        d['insurer_name'] = insurer_label(d.get('insurer'))
        out.append(d)

    return jsonify({
        'files': out,
        'count': len(out)
    })


@app.route('/<url_code>/upload', methods=['POST'])
def handle_upload(url_code):
    """
    Upload a file to vendor's portfolio.
    Handles fuzzy matching for potential replacements.

    LEGACY ONLY. This endpoint receives RAW files (names, contact details and all)
    and strips them on the server. In v3 the browser strips before anything is
    sent, so this route is closed unless SOURCETECH_UI=legacy.
    """
    if not LEGACY_UI:
        return jsonify({'error': 'This endpoint is closed. Files are prepared in the browser and sent to /clean-upload.',
                        'code': 'raw_upload_closed'}), 410
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()

    if not vendor:
        db.close()
        return jsonify({'error': 'Invalid link'}), 404

    if 'file' not in request.files:
        db.close()
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        db.close()
        return jsonify({'error': 'No file selected'}), 400

    # Check file extension
    allowed_extensions = {'.xlsx', '.xls', '.csv'}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        db.close()
        return jsonify({
            'error': 'Invalid file type. Please upload an Excel (.xlsx, .xls) or CSV file.',
            'valid': False
        }), 400

    # Check for fuzzy filename match (potential replacement)
    existing_files = db.execute(
        "SELECT original_filename FROM vendor_files WHERE vendor_id = ?",
        (vendor['id'],)
    ).fetchall()
    existing_names = [f['original_filename'] for f in existing_files]

    match, score = fuzzy_match_filename(file.filename, existing_names)

    # Check if user specified replacement action
    replace_file_id = request.form.get('replace_file_id')
    action = request.form.get('action', 'auto')  # auto, replace, add_new

    # Optional insurer tag (which insurer this file belongs to). Validated against
    # the catalogue; the special "other" tag (catch-all slot) is allowed through;
    # anything else unknown/blank falls back to None so the legacy no-insurer path
    # keeps working unchanged.
    raw_insurer = (request.form.get('insurer', '') or '').strip().lower()
    if raw_insurer == OTHER_KEY:
        insurer_key = OTHER_KEY
    else:
        _clean = clean_insurer_keys([raw_insurer])
        insurer_key = _clean[0] if _clean else None

    if match and score > 0.6 and action == 'auto' and not replace_file_id:
        # Found potential match - ask user what to do
        db.close()
        return jsonify({
            'needs_confirmation': True,
            'match': {
                'filename': match,
                'similarity': round(score * 100),
                'message': f'This looks similar to "{match}" ({round(score * 100)}% match). Do you want to replace it or add as a new file?'
            }
        })

    # Save file to vendor's upload directory
    vendor_dir = get_vendor_upload_dir(url_code)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    # Sanitise the client-supplied filename before it touches the filesystem —
    # secure_filename strips path separators and traversal (`../`) so a hostile
    # name can't escape the vendor's upload dir. Keep a fallback in case the name
    # sanitises down to empty (e.g. all-unicode), preserving the checked extension.
    clean_name = secure_filename(file.filename) or f"upload{file_ext}"
    safe_filename = f"{timestamp}_{clean_name}"
    file_path = vendor_dir / safe_filename
    file.save(file_path)

    # Validate file
    validation = validate_portfolio_file(file_path)

    # Short-circuit on validation failure — strip_pii would crash on corrupt
    # or non-Excel files and return 500. Return the friendly validation
    # message instead so the vendor sees what's wrong.
    if not validation.get('valid'):
        return jsonify({
            'success': False,
            'valid': False,
            'errors': validation.get('errors', ['File could not be processed']),
            'warnings': validation.get('warnings', []),
            'filename': file.filename,
        }), 400

    # Strip PII
    cleaned_df, pii_report = strip_pii(file_path)

    # Save cleaned version
    cleaned_filename = f"cleaned_{safe_filename}"
    if not cleaned_filename.endswith('.xlsx'):
        cleaned_filename = cleaned_filename.rsplit('.', 1)[0] + '.xlsx'
    cleaned_path = vendor_dir / cleaned_filename
    cleaned_df.to_excel(cleaned_path, index=False)

    # Build processing summary
    processing_summary = build_processing_summary(validation, pii_report)

    # Handle replacement
    if replace_file_id:
        # Delete the old file
        old_file = db.execute(
            "SELECT * FROM vendor_files WHERE id = ? AND vendor_id = ?",
            (replace_file_id, vendor['id'])
        ).fetchone()
        if old_file:
            old_path = Path(old_file['file_path'])
            if old_path.exists():
                old_path.unlink()
            db.execute("DELETE FROM vendor_files WHERE id = ?", (replace_file_id,))

    # Save to database
    db.execute('''
        INSERT INTO vendor_files
        (vendor_id, filename, original_filename, file_path, file_size,
         status, validation_warnings, pii_report, policy_count, processing_summary, insurer)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        vendor['id'],
        cleaned_filename,
        file.filename,
        str(cleaned_path),
        file_path.stat().st_size,
        'valid' if validation['valid'] else 'warning',
        json.dumps(validation.get('warnings', [])),
        json.dumps(pii_report),
        validation.get('row_count', 0),
        json.dumps(processing_summary),
        insurer_key
    ))
    db.commit()

    file_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("UPDATE vendors SET last_submission_at = CURRENT_TIMESTAMP WHERE id = ?", (vendor['id'],))
    db.commit()
    db.close()

    # Best-effort: notify DealTECH that this deal's inforce data has been received
    # (PII stripped) so it auto-checks the Ver3 P1 "data received" gate. A DealTECH
    # outage must never fail the vendor's upload — notify_data_received swallows errors.
    deal_id = vendor['deal_id'] if 'deal_id' in vendor.keys() else None
    if deal_id:
        notify_data_received(deal_id, file_url=_upload_url(url_code))

    return jsonify({
        'success': True,
        'file_id': file_id,
        'filename': file.filename,
        'insurer': insurer_key,
        'insurer_name': insurer_label(insurer_key),
        'valid': validation['valid'],
        'positives': validation.get('positives', []),
        'warnings': validation.get('warnings', []),
        'errors': validation.get('errors', []),
        'assumptions': validation.get('assumptions', {}),
        'pii_removed': pii_report.get('columns_removed', []),
        'pii_anonymized': pii_report.get('columns_anonymized', []),
        'policy_count': validation.get('row_count', 0),
        'processing_summary': processing_summary
    })


@app.route('/<url_code>/files/<int:file_id>', methods=['DELETE'])
def delete_file(url_code, file_id):
    """Delete a file from vendor's portfolio."""
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()

    if not vendor:
        db.close()
        return jsonify({'error': 'Invalid link'}), 404

    # Get file info
    file_record = db.execute(
        "SELECT * FROM vendor_files WHERE id = ? AND vendor_id = ?",
        (file_id, vendor['id'])
    ).fetchone()

    if not file_record:
        db.close()
        return jsonify({'error': 'File not found'}), 404

    # Delete physical file
    file_path = Path(file_record['file_path'])
    if file_path.exists():
        file_path.unlink()

    # Delete from database
    db.execute("DELETE FROM vendor_files WHERE id = ?", (file_id,))
    db.commit()
    db.close()

    return jsonify({'success': True, 'deleted': file_record['original_filename']})


@app.route('/<url_code>/review')
def review_submission(url_code):
    """What a Submit would send, for the vendor's Review step.

    Returns the deduped working set (same logic as /submit) grouped by insurer,
    with per-file row counts and aggregate PII-stripping stats, so the vendor
    confirms exactly what is about to be processed.
    """
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()

    if not vendor:
        db.close()
        return jsonify({'error': 'Invalid link'}), 404

    working, total_uploads = build_working_set(db, vendor['id'])
    db.close()

    files_out = []
    by_insurer = {}
    total_rows = 0
    pii_columns_removed = 0
    pii_columns_anonymized = 0

    for f in working:
        d = dict(f)
        if not Path(d['file_path']).exists():
            continue
        key = d.get('insurer')
        if key not in INSURER_KEYS:
            key = OTHER_KEY
        label = insurer_label(key)
        try:
            pii = json.loads(d.get('pii_report') or '{}')
        except (ValueError, TypeError):
            pii = {}
        rows = d.get('policy_count') or 0
        total_rows += rows
        pii_columns_removed += len(pii.get('columns_removed', []))
        pii_columns_anonymized += len(pii.get('columns_anonymized', []))

        files_out.append({
            'id': d['id'],
            'insurer': key,
            'insurer_name': label,
            'original_filename': d['original_filename'],
            'policy_count': rows,
        })
        grp = by_insurer.setdefault(key, {'key': key, 'name': label, 'files': 0, 'policies': 0})
        grp['files'] += 1
        grp['policies'] += rows

    # Stable order: catalogue order, catch-all last
    ordered_keys = [ins['key'] for ins in INSURERS if ins['key'] in by_insurer]
    if OTHER_KEY in by_insurer:
        ordered_keys.append(OTHER_KEY)

    return jsonify({
        'files': files_out,
        'by_insurer': [by_insurer[k] for k in ordered_keys],
        'totals': {
            'files': len(files_out),
            'policies': total_rows,
            'uploads_superseded': total_uploads - len(working),
            'pii_columns_removed': pii_columns_removed,
            'pii_columns_anonymized': pii_columns_anonymized,
        },
    })


@app.route('/<url_code>/revaluate', methods=['POST'])
@app.route('/<url_code>/submit', methods=['POST'])
def trigger_revaluation(url_code):
    """Submit the portfolio: build the working set and run the PavTECH valuation.

    Exposed at both /submit (the vendor-facing "Submit to InsurancePLUS" action)
    and /revaluate (legacy alias). The working set is deduped by ORIGINAL filename
    keeping only the latest upload of each name, so re-uploading an edited file
    with the same name replaces the older version rather than sending both.
    """
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()

    if not vendor:
        db.close()
        return jsonify({'error': 'Invalid link'}), 404

    files, total_uploads = build_working_set(db, vendor['id'])

    if not files:
        db.close()
        return jsonify({'error': 'No files to process'}), 400

    # Collect file paths
    file_paths = [Path(f['file_path']) for f in files if Path(f['file_path']).exists()]

    if not file_paths:
        db.close()
        return jsonify({'error': 'No valid files found'}), 400

    logger.info("Submit %s: %d uploads deduped to %d working files by filename",
                url_code, total_uploads, len(file_paths))

    # Update vendor status
    db.execute('''
        UPDATE vendors SET status = 'processing', last_submission_at = ?
        WHERE id = ?
    ''', (datetime.now().isoformat(), vendor['id']))
    db.commit()

    vendor_dict = dict(vendor)
    files_list = [dict(f) for f in files]
    db.close()

    # Human-quotable submission reference, shown on the vendor's receipt and
    # stored on the submission row for support lookups.
    submitted_at = datetime.now()
    reference = f"ST-{submitted_at.strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"

    # Start background processing
    thread = threading.Thread(
        target=_process_batch_with_pavtech,
        args=(vendor_dict, file_paths, files_list, reference),
        daemon=True
    )
    thread.start()

    return jsonify({
        'success': True,
        'processing': True,
        'file_count': len(file_paths),
        'reference': reference,
        'submitted_at': submitted_at.isoformat(),
        'message': f'Processing {len(file_paths)} files...'
    })


def _process_batch_with_pavtech(vendor: dict, file_paths: list, files_info: list, reference: str = None, extra_notes: list = None):
    """Background batch processing with PavTECH."""
    db = get_db()

    try:
        logger.info(f"Starting PavTECH batch processing for {vendor['vendor_name']} with {len(file_paths)} files")

        # Process through PavTECH
        # Pass the HubSpot deal id so PavTECH ties the run to the exact deal (its HubSpot
        # attach and owner lookup) instead of fuzzy-matching the vendor name.
        success, result = pavtech.process_batch(
            file_paths, vendor['vendor_name'],
            deal_id=(vendor.get('hubspot_deal_id') or None),
            generator_name='SourceTECH',
        )

        # Build file summary for email
        files_summary = [{
            'filename': f['original_filename'],
            'policies': f['policy_count'],
            'status': f['status']
        } for f in files_info]

        # Collect assumptions from all files
        all_assumptions = []
        for f in files_info:
            warnings = json.loads(f.get('validation_warnings', '[]'))
            all_assumptions.extend([w for w in warnings if w.startswith('⚠️')])

        if success:
            master_path = Path(result['master_document_path'])

            # Extract valuation summary from master document
            valuation = extract_valuation_summary(master_path)

            # Record submission
            db.execute('''
                INSERT INTO submissions
                (vendor_id, pavtech_batch_id, file_count, policy_count,
                 pavtech_status, master_document_path, valuation_summary, reference)
                VALUES (?, ?, ?, ?, 'complete', ?, ?, ?)
            ''', (
                vendor['id'],
                result['batch_id'],
                len(file_paths),
                result.get('total_policies', 0),
                str(master_path),
                json.dumps(valuation),
                reference
            ))

            db.execute('''
                UPDATE vendors SET status = 'complete' WHERE id = ?
            ''', (vendor['id'],))
            db.commit()

            # Notify the Deal Manager at the vendor's STORED dm_email (not a
            # hardcoded contact lookup), with the valuation master attached.
            ok, info = send_dm_notification(
                to_email=vendor['dm_email'],
                dm_name=vendor['dm_name'],
                vendor_name=vendor['vendor_name'],
                url_code=vendor['url_code'],
                valuation=valuation,
                attachment_path=master_path,
                pavtech_valuation=result.get('total_valuation'),
                extra_notes=extra_notes,
            )
            if ok:
                logger.info("DM valuation-complete email sent to %s (resend id=%s)",
                            vendor['dm_email'], info.get('id'))
            else:
                logger.error("DM valuation-complete email FAILED to %s: %s",
                             vendor['dm_email'], info)

            logger.info(f"Complete: {vendor['vendor_name']} - {result.get('total_policies', 0)} policies, ${result.get('total_valuation', 0):,.0f} valuation")

        else:
            error_msg = result.get('error', 'Processing failed')

            db.execute('''
                INSERT INTO submissions
                (vendor_id, file_count, pavtech_status, validation_errors, reference)
                VALUES (?, ?, 'error', ?, ?)
            ''', (vendor['id'], len(file_paths), error_msg, reference))

            db.execute('''
                UPDATE vendors SET status = 'error' WHERE id = ?
            ''', (vendor['id'],))
            db.commit()

            # Still notify DM
            send_dm_notification(
                to_email=vendor['dm_email'],
                dm_name=vendor['dm_name'],
                vendor_name=vendor['vendor_name'],
                url_code=vendor['url_code'],
                valuation=None,
                error=error_msg,
                extra_notes=extra_notes,
            )

            logger.error(f"Failed: {vendor['vendor_name']} - {error_msg}")

    except Exception as e:
        logger.error(f"Background batch processing error: {e}")
        db.execute('''
            UPDATE vendors SET status = 'error' WHERE id = ?
        ''', (vendor['id'],))
        db.commit()

    finally:
        db.close()


@app.route('/<url_code>/status')
def get_status(url_code):
    """Vendor-safe processing status, polled by the upload page every 3s.

    HARD RULE: SourceTECH is CUSTOMER FACING. No valuation data (value, multiple,
    commission total, master document path, PavTECH URL or error text) may appear
    in this response. The DM gets the detail by email.
    """
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()

    if not vendor:
        db.close()
        return jsonify({'error': 'Invalid link'}), 404

    # Latest submission: named safe columns only, never SELECT * (the row also
    # holds valuation_summary and master_document_path).
    latest = db.execute('''
        SELECT reference, submitted_at, file_count, pavtech_status
        FROM submissions
        WHERE vendor_id = ?
        ORDER BY submitted_at DESC
        LIMIT 1
    ''', (vendor['id'],)).fetchone()

    # Get file count
    file_count = db.execute(
        "SELECT COUNT(*) FROM vendor_files WHERE vendor_id = ?",
        (vendor['id'],)
    ).fetchone()[0]

    db.close()

    # WHITELIST: every key below is named explicitly. Never return dict(row)
    # from a vendor-reachable route.
    latest_out = None
    if latest:
        latest_out = {
            'reference': latest['reference'],
            'submitted_at': latest['submitted_at'],
            'file_count': latest['file_count'],
            'state': vendor_processing_state(latest['pavtech_status']),
        }

    return jsonify({
        'state': vendor_processing_state(vendor['status']),
        'file_count': file_count,
        'latest_submission': latest_out,
    })


@app.route('/<url_code>/success')
def success_page(url_code):
    """Show success page after upload."""
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()
    db.close()

    if not vendor:
        return render_template('error.html', message="Invalid link"), 404

    return render_template('success.html', vendor=vendor)


# ─────────────────────────────────────────────────────────────
# DEALTECH SERVER-TO-SERVER API
# (consumed by DealTECH app/services/sourcetech.py SourceTECHService)
# ─────────────────────────────────────────────────────────────

def _api_secret_ok() -> bool:
    """If WEBHOOK_SECRET is configured, require a matching X-Webhook-Secret
    header on the server-to-server API. If unset (dev), allow + warn."""
    if not WEBHOOK_SECRET:
        # Fail loud + closed: an unauthenticated server-to-server API is a hole,
        # not a convenience. Prod always sets this; deny rather than run open.
        logger.error("WEBHOOK_SECRET not set — refusing /api/vendors request (fail-closed)")
        return False
    return request.headers.get("X-Webhook-Secret") == WEBHOOK_SECRET


def _upload_url(url_code: str) -> str:
    """Absolute vendor upload URL. Prefers PUBLIC_BASE_URL; falls back to host."""
    base = PUBLIC_BASE_URL or request.host_url.rstrip("/")
    return f"{base}/{url_code}"


@app.route('/api/vendors', methods=['POST'])
def api_create_vendor():
    """Create a vendor upload link from DealTECH (JSON).

    Body: {vendor_name, dm_email, dm_name, deal_id, hubspot_deal_id?}
    Returns: {url_code, vendor_id, upload_url}. Idempotent per deal_id.
    """
    if not _api_secret_ok():
        return jsonify({'error': 'unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    vendor_name = (data.get('vendor_name') or '').strip()
    dm_email = (data.get('dm_email') or '').strip()
    dm_name = (data.get('dm_name') or '').strip()
    deal_id = data.get('deal_id')
    hubspot_deal_id = data.get('hubspot_deal_id')
    # Optional pre-selection of insurers from DealTECH. Absent -> no pre-selection
    # (behaves exactly as before). Unknown keys are dropped.
    selected_insurers = clean_insurer_keys(data.get('insurers'))
    selected_json = json.dumps(selected_insurers)

    if not vendor_name:
        return jsonify({'error': 'vendor_name is required'}), 400

    db = get_db()
    try:
        # Idempotent: reuse an existing link for the same deal.
        if deal_id is not None:
            existing = db.execute(
                "SELECT * FROM vendors WHERE deal_id = ? ORDER BY id DESC LIMIT 1",
                (deal_id,),
            ).fetchone()
            if existing:
                db.close()
                return jsonify({
                    'url_code': existing['url_code'],
                    'vendor_id': existing['id'],
                    'upload_url': _upload_url(existing['url_code']),
                    'already_exists': True,
                })

        url_code = None
        for _ in range(5):  # retry on url_code collision
            candidate = secrets.token_urlsafe(6)[:8].upper()
            try:
                db.execute('''
                    INSERT INTO vendors
                    (url_code, vendor_name, dm_email, dm_name, created_by, deal_id, hubspot_deal_id, selected_insurers)
                    VALUES (?, ?, ?, ?, 'dealtech', ?, ?, ?)
                ''', (candidate, vendor_name, dm_email, dm_name, deal_id, hubspot_deal_id, selected_json))
                db.commit()
                url_code = candidate
                break
            except sqlite3.IntegrityError:
                continue
        if not url_code:
            db.close()
            return jsonify({'error': 'could not allocate url_code'}), 500

        vendor_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()
        return jsonify({
            'url_code': url_code,
            'vendor_id': vendor_id,
            'upload_url': _upload_url(url_code),
        }), 201
    except Exception as e:
        db.close()
        logger.error("api_create_vendor error: %s", e)
        return jsonify({'error': 'internal error'}), 500


@app.route('/api/vendors/<url_code>/status')
def api_vendor_status(url_code):
    """Status for DealTECH polling. Shape matches SourceTECHService.get_vendor_status."""
    if not _api_secret_ok():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    vendor = db.execute("SELECT * FROM vendors WHERE url_code = ?", (url_code,)).fetchone()
    if not vendor:
        db.close()
        return jsonify({'error': 'not found'}), 404
    file_count = db.execute(
        "SELECT COUNT(*) FROM vendor_files WHERE vendor_id = ?", (vendor['id'],)
    ).fetchone()[0]
    db.close()
    # Map SourceTECH vendor.status -> DealTECH processing_status vocabulary.
    status_map = {'pending': 'pending', 'processing': 'processing',
                  'complete': 'complete', 'error': 'failed'}
    return jsonify({
        'has_uploads': file_count > 0,
        'submission_count': file_count,
        'last_upload': vendor['last_submission_at'],
        'processing_status': status_map.get(vendor['status'], vendor['status'] or 'pending'),
        'deal_id': vendor['deal_id'],
    })


@app.route('/api/vendors/<url_code>/submissions')
def api_vendor_submissions(url_code):
    """List submissions for DealTECH (JSON array)."""
    if not _api_secret_ok():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    vendor = db.execute("SELECT id FROM vendors WHERE url_code = ?", (url_code,)).fetchone()
    if not vendor:
        db.close()
        return jsonify({'error': 'not found'}), 404
    rows = db.execute(
        "SELECT * FROM submissions WHERE vendor_id = ? ORDER BY submitted_at DESC",
        (vendor['id'],),
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


# ─────────────────────────────────────────────────────────────
# HEALTH & HOME
# ─────────────────────────────────────────────────────────────

@app.route('/')
def home():
    """Home page - redirect to admin."""
    return redirect(url_for('admin_login'))


def add_column_if_missing_public(db, table: str, column: str, ddl_type: str):
    """ALTER-if-missing for modules outside init_db. Fails loudly, never no-ops."""
    cols = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in cols:
        return
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
        logger.info("Migration: added %s.%s", table, column)
    except Exception as exc:
        logger.error("MIGRATION FAILED adding %s.%s: %s", table, column, exc)
        raise


def insurer_profiles() -> dict:
    """Expected export headers per insurer tile (insurer_profiles.json)."""
    try:
        with open(os.path.join(app.root_path, 'insurer_profiles.json'), encoding='utf-8') as fh:
            return {k: v for k, v in json.load(fh).items() if not k.startswith('_')}
    except (OSError, ValueError) as exc:
        logger.error("insurer_profiles.json unreadable, prong A notes disabled: %s", exc)
        return {}


from types import SimpleNamespace  # noqa: E402
from v3_api import register_v3  # noqa: E402

v3 = register_v3(app, SimpleNamespace(
    get_db=get_db, INSURERS=INSURERS, INSURER_KEYS=INSURER_KEYS, OTHER_KEY=OTHER_KEY, APP_VERSION=APP_VERSION,
    insurer_label=insurer_label, clean_insurer_keys=clean_insurer_keys, insurer_profiles=insurer_profiles,
    vendor_processing_state=vendor_processing_state, get_vendor_upload_dir=get_vendor_upload_dir,
    validate_portfolio_file=validate_portfolio_file, notify_data_received=notify_data_received,
    upload_url=_upload_url, build_working_set=build_working_set, send_dm_notification=send_dm_notification,
    send_email=send_email, process_batch_with_pavtech=_process_batch_with_pavtech,
    add_column_if_missing_public=add_column_if_missing_public,
))


@app.route('/health')
def health():
    """Health check endpoint."""
    pavtech_ok = pavtech.health_check()
    return jsonify({
        'status': 'healthy' if pavtech_ok else 'degraded',
        'version': APP_VERSION,
        'pavtech_available': pavtech_ok,
        'dealtech_bridge': bool(os.environ.get('DEALTECH_API_URL')),
        'timestamp': datetime.now().isoformat()
    })


if __name__ == '__main__':
    app.run(debug=True, port=5002)
