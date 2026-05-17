"""
SourceTECH - Vendor Portfolio Upload Portal
Main Flask application
"""
from flask import Flask, request, render_template, redirect, url_for, jsonify, session
from functools import wraps
from validator import validate_portfolio_file
from pii_stripper import strip_pii
from pavtech_client import PavTechClient
from excel_parser import extract_valuation_summary
from email_service import send_dm_notification
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
PAVTECH_API_URL = os.environ.get('PAVTECH_API_URL', 'http://localhost:5000')
UPLOADS_DIR = os.environ.get('UPLOADS_DIR', 'uploads')  # Persistent file storage
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme')  # Change in production!

# Ensure directories exist
Path(UPLOADS_DIR).mkdir(parents=True, exist_ok=True)

# Database lives inside uploads dir for persistence (both local and Render)
DATABASE = os.environ.get('DATABASE', str(Path(UPLOADS_DIR) / 'sourcetech.db'))
TEMP_DIR = os.environ.get('TEMP_DIR', str(Path(UPLOADS_DIR) / 'temp'))
Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)

pavtech = PavTechClient(PAVTECH_API_URL, temp_dir=TEMP_DIR)


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

        if not vendor_name or not dm_email or not dm_name:
            return render_template('admin/create_vendor.html',
                                   error='All fields are required',
                                   form=request.form)

        # Generate unique URL code
        url_code = secrets.token_urlsafe(6)[:8].upper()

        db = get_db()
        try:
            db.execute('''
                INSERT INTO vendors (url_code, vendor_name, dm_email, dm_name, created_by, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (url_code, vendor_name, dm_email, dm_name, 'admin', notes))
            db.commit()
        except sqlite3.IntegrityError:
            # URL code collision - try again
            url_code = secrets.token_urlsafe(6)[:8].upper()
            db.execute('''
                INSERT INTO vendors (url_code, vendor_name, dm_email, dm_name, created_by, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (url_code, vendor_name, dm_email, dm_name, 'admin', notes))
            db.commit()

        db.close()

        # Redirect to success page showing the link
        return redirect(url_for('admin_vendor_created', url_code=url_code))

    return render_template('admin/create_vendor.html')


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
    return render_template('admin/vendor_detail.html', vendor=vendor, submissions=submissions, upload_url=upload_url)


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

    # Get existing files for this vendor
    files = db.execute('''
        SELECT * FROM vendor_files
        WHERE vendor_id = ?
        ORDER BY uploaded_at DESC
    ''', (vendor['id'],)).fetchall()

    # Get latest submission for valuation info
    latest_submission = db.execute('''
        SELECT * FROM submissions
        WHERE vendor_id = ?
        ORDER BY submitted_at DESC
        LIMIT 1
    ''', (vendor['id'],)).fetchone()

    db.close()

    return render_template('upload.html',
        vendor=vendor,
        url_code=url_code,
        files=[dict(f) for f in files],
        latest_submission=dict(latest_submission) if latest_submission else None
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
               status, validation_warnings, pii_report, policy_count, processing_summary
        FROM vendor_files
        WHERE vendor_id = ?
        ORDER BY uploaded_at DESC
    ''', (vendor['id'],)).fetchall()

    db.close()

    return jsonify({
        'files': [dict(f) for f in files],
        'count': len(files)
    })


@app.route('/<url_code>/upload', methods=['POST'])
def handle_upload(url_code):
    """
    Upload a file to vendor's portfolio.
    Handles fuzzy matching for potential replacements.
    """
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
    safe_filename = f"{timestamp}_{file.filename}"
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
         status, validation_warnings, pii_report, policy_count, processing_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        json.dumps(processing_summary)
    ))
    db.commit()

    file_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()

    return jsonify({
        'success': True,
        'file_id': file_id,
        'filename': file.filename,
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


@app.route('/<url_code>/revaluate', methods=['POST'])
def trigger_revaluation(url_code):
    """Trigger revaluation of all files in portfolio."""
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()

    if not vendor:
        db.close()
        return jsonify({'error': 'Invalid link'}), 404

    # Get all files for this vendor
    files = db.execute('''
        SELECT * FROM vendor_files
        WHERE vendor_id = ?
    ''', (vendor['id'],)).fetchall()

    if not files:
        db.close()
        return jsonify({'error': 'No files to process'}), 400

    # Collect file paths
    file_paths = [Path(f['file_path']) for f in files if Path(f['file_path']).exists()]

    if not file_paths:
        db.close()
        return jsonify({'error': 'No valid files found'}), 400

    # Update vendor status
    db.execute('''
        UPDATE vendors SET status = 'processing', last_submission_at = ?
        WHERE id = ?
    ''', (datetime.now().isoformat(), vendor['id']))
    db.commit()

    vendor_dict = dict(vendor)
    files_list = [dict(f) for f in files]
    db.close()

    # Start background processing
    thread = threading.Thread(
        target=_process_batch_with_pavtech,
        args=(vendor_dict, file_paths, files_list),
        daemon=True
    )
    thread.start()

    return jsonify({
        'success': True,
        'processing': True,
        'file_count': len(file_paths),
        'message': f'Processing {len(file_paths)} files...'
    })


def _process_batch_with_pavtech(vendor: dict, file_paths: list, files_info: list):
    """Background batch processing with PavTECH."""
    db = get_db()

    try:
        logger.info(f"Starting PavTECH batch processing for {vendor['vendor_name']} with {len(file_paths)} files")

        # Process through PavTECH
        success, result = pavtech.process_batch(file_paths, vendor['vendor_name'])

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
                 pavtech_status, master_document_path, valuation_summary)
                VALUES (?, ?, ?, ?, 'complete', ?, ?)
            ''', (
                vendor['id'],
                result['batch_id'],
                len(file_paths),
                result.get('total_policies', 0),
                str(master_path),
                json.dumps(valuation)
            ))

            db.execute('''
                UPDATE vendors SET status = 'complete' WHERE id = ?
            ''', (vendor['id'],))
            db.commit()

            # Send notification email
            from email_service import send_portfolio_update_notification
            send_portfolio_update_notification(
                dm_key=vendor.get('dm_name', 'paul').split()[0].lower(),
                vendor_name=vendor['vendor_name'],
                url_code=vendor['url_code'],
                file_count=len(file_paths),
                files_summary=files_summary,
                valuation=valuation,
                assumptions=list(set(all_assumptions)),
                attachment_path=master_path
            )

            logger.info(f"Complete: {vendor['vendor_name']} - {result.get('total_policies', 0)} policies, ${result.get('total_valuation', 0):,.0f} valuation")

        else:
            error_msg = result.get('error', 'Processing failed')

            db.execute('''
                INSERT INTO submissions
                (vendor_id, file_count, pavtech_status, validation_errors)
                VALUES (?, ?, 'error', ?)
            ''', (vendor['id'], len(file_paths), error_msg))

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
                error=error_msg
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
    """Get current processing status for vendor."""
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE url_code = ?",
        (url_code,)
    ).fetchone()

    if not vendor:
        db.close()
        return jsonify({'error': 'Invalid link'}), 404

    # Get latest submission
    latest = db.execute('''
        SELECT * FROM submissions
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

    return jsonify({
        'status': vendor['status'],
        'file_count': file_count,
        'latest_submission': dict(latest) if latest else None,
        'valuation': json.loads(latest['valuation_summary']) if latest and latest['valuation_summary'] else None
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
# HEALTH & HOME
# ─────────────────────────────────────────────────────────────

@app.route('/')
def home():
    """Home page - redirect to admin."""
    return redirect(url_for('admin_login'))


@app.route('/health')
def health():
    """Health check endpoint."""
    pavtech_ok = pavtech.health_check()
    return jsonify({
        'status': 'healthy' if pavtech_ok else 'degraded',
        'pavtech_available': pavtech_ok,
        'timestamp': datetime.now().isoformat()
    })


if __name__ == '__main__':
    app.run(debug=True, port=5002)
