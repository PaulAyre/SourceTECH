# SourceTECH - Vendor Portfolio Upload Portal

Secure web portal enabling insurance vendors to upload portfolio files via unique URLs. Files are validated, PII-stripped, processed through the PavTECH valuation engine, and results emailed to account managers via SendGrid.

## Features

- **Vendor Upload Portal** - Drag-and-drop file upload with unique per-vendor URLs
- **Automatic PII Stripping** - Removes emails, phones, addresses, tax file numbers; anonymizes client names to `CLIENT_00001` etc.
- **Portfolio Validation** - Non-blocking validation with graceful assumptions (warns instead of rejecting)
- **PavTECH Integration** - Batch processing through PavTECH valuation engine with polling and master document generation
- **Email Notifications** - SendGrid emails to assigned DMs with valuation summary and attached master document
- **Admin Portal** - Create vendor links, track submissions, view processing status
- **Fuzzy Duplicate Detection** - 60% similarity threshold catches re-uploads including Chrome download suffixes `(1)`, `_2`, etc.
- **Smart Insurer Sidebar** - Upload page includes step-by-step download guides for 9 Australian insurers

## Architecture

```
Vendor Browser                  SourceTECH                         PavTECH
     │                              │                                │
     ├── Upload files ──────────────┤                                │
     │                              ├── Validate portfolio           │
     │                              ├── Strip PII                    │
     │                              ├── Upload batch ────────────────┤
     │                              ├── Start processing ────────────┤
     │                              ├── Poll status ─────────────────┤
     │                              ├── Download master doc ─────────┤
     │                              ├── Extract valuation summary    │
     │                              ├── Send email via SendGrid      │
     │                              │                                │
Admin Browser                       │                                │
     ├── Create vendor link ────────┤                                │
     ├── View dashboard ────────────┤                                │
     ├── Track submissions ─────────┤                                │
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3, Flask 3.0.0 |
| Database | SQLite3 (file-based) |
| Data Processing | Pandas 2.1.4, OpenPyXL 3.1.2 |
| Valuation Engine | PavTECH API (external) |
| Email | SendGrid 6.11.0 |
| HTTP Client | Requests 2.31.0 |
| WSGI Server | Gunicorn 21.2.0 (production) |
| Frontend | Jinja2 templates, vanilla JS, CSS design system v2.0 |
| Deployment | Render.com |

## Project Structure

```
SourceTECH/
├── app.py                  # Flask application - routes, admin, upload handling (831 lines)
├── validator.py            # Portfolio file validation with graceful assumptions (213 lines)
├── pii_stripper.py         # PII removal - column deletion, name anonymization, regex scan (153 lines)
├── pavtech_client.py       # PavTECH batch API client - upload, process, poll, download (317 lines)
├── excel_parser.py         # Master document parser - extract valuation summary (197 lines)
├── email_service.py        # SendGrid notifications with attachments (346 lines)
├── requirements.txt        # Python dependencies
├── render.yaml             # Render.com deployment configuration
├── .env.example            # Environment variable template
├── .gitignore
├── README.md               # This file
├── WORKING.md              # Development log and version history
├── Gotcha.MD               # Known issues, bugs, and lessons learned
├── sourcetech.db           # SQLite database (created on first run)
├── static/
│   ├── css/style.css       # Design system v2.0 (PavTECH-inspired)
│   ├── js/                 # Client-side JavaScript
│   └── images/             # InsurancePLUS logo and assets
├── templates/
│   ├── base.html           # Base template with navigation
│   ├── upload.html         # Vendor upload portal with insurer sidebar
│   ├── success.html        # Upload success page
│   ├── error.html          # Error display
│   ├── already_submitted.html
│   └── admin/
│       ├── base.html       # Admin base template
│       ├── dashboard.html  # Overview with stats
│       ├── login.html      # Password authentication
│       ├── vendors.html    # Vendor list
│       ├── create_vendor.html
│       ├── vendor_created.html
│       └── vendor_detail.html
└── uploads/                # Persistent file storage (organized by vendor URL code)
```

## Setup

1. Clone the repository

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and configure:
   ```bash
   cp .env.example .env
   ```

5. Run the application:
   ```bash
   python app.py
   ```

6. Access the admin portal at `http://localhost:5002/admin`

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret key | Random (generated at startup) |
| `PAVTECH_API_URL` | PavTECH API base URL | `http://localhost:5000` |
| `DATABASE` | SQLite database file path | `sourcetech.db` |
| `TEMP_DIR` | Temporary file directory for downloads | `/tmp/sourcetech` |
| `UPLOADS_DIR` | Persistent upload storage directory | `uploads` |
| `ADMIN_PASSWORD` | Admin portal password | `changeme` |
| `SENDGRID_API_KEY` | SendGrid API key for email | None (emails logged to console) |
| `FROM_EMAIL` | Sender email address | `sourcetech@example.com` |

## Database Schema

Three tables in SQLite:

**vendors** - Vendor/practice records with unique upload URLs
- `url_code` (unique 8-char code), `vendor_name`, `dm_email`, `dm_name`, `status`, `notes`

**submissions** - Batch processing records
- Links to vendor, tracks PavTECH `session_id`/`batch_id`, `policy_count`, `pavtech_status`, `master_document_path`, `valuation_summary`

**vendor_files** - Individual uploaded files
- Links to vendor, tracks `original_filename`, `file_path`, `validation_warnings`, `pii_report`, `policy_count`, `processing_summary`

## API Endpoints

### Vendor-Facing

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/<url_code>` | Upload portal page |
| `POST` | `/<url_code>/upload` | Upload file (handles fuzzy matching, validation, PII strip) |
| `GET` | `/<url_code>/files` | List uploaded files (JSON) |
| `DELETE` | `/<url_code>/files/<id>` | Delete a file |
| `POST` | `/<url_code>/revaluate` | Trigger PavTECH batch processing |
| `GET` | `/<url_code>/status` | Poll processing status (JSON) |
| `GET` | `/<url_code>/success` | Success page |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| `GET/POST` | `/admin/login` | Admin login |
| `GET` | `/admin/logout` | Admin logout |
| `GET` | `/admin` | Dashboard with stats |
| `GET` | `/admin/vendors` | Vendor list with submission counts |
| `GET/POST` | `/admin/vendors/create` | Create new vendor link |
| `GET` | `/admin/vendors/created/<code>` | Show generated link |
| `GET` | `/admin/vendors/<code>` | Vendor detail and submission history |

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Redirects to admin login |
| `GET` | `/health` | Health check (includes PavTECH availability) |

## Module Reference

### validator.py

Non-blocking portfolio validation. Checks for four field categories using fuzzy column name matching:

| Field | Patterns Checked | Critical? | Default Assumption |
|-------|-----------------|-----------|-------------------|
| Premium | `annual premium`, `premium`, `gross premium`, `prem` | Yes | None (file rejected) |
| Benefit Type | `benefit`, `product`, `cover type`, `plan type` | No | Income Protection |
| Status | `in force`, `status`, `active`, `inforce` | No | All In Force |
| DOB/Age | `dob`, `date of birth`, `age`, `age next` | No | Age 63 |

Premium can also be detected as separate amount + frequency columns. Validation reports sparse columns (>50% empty) and small files (<10 rows).

### pii_stripper.py

Three-layer PII removal:

1. **Column removal** - Deletes columns matching PII patterns (email, phone, address, TFN, bank details, etc.)
2. **Name anonymization** - Replaces values in name columns with `CLIENT_00001`, `CLIENT_00002`, etc.
3. **Regex scan** - Scans remaining text columns for email addresses and Australian phone numbers, replaces with `[REDACTED]`

Has an explicit keep-list to protect business-critical columns (DOB, premium, commission, status, etc.) from accidental stripping.

### pavtech_client.py

Five-step batch processing flow:

1. **Upload** - `POST /api/batch/upload` with multipart files (120s timeout)
2. **Process** - `POST /api/batch/process_file` for each file individually (30s timeout each)
3. **Poll** - `GET /api/batch/status` every 3 seconds, up to 10 minutes max
4. **Generate** - `POST /api/batch/generate_master` to create consolidated document (120s timeout)
5. **Download** - `GET /api/batch/download_master` streamed download (120s timeout)

### excel_parser.py

Extracts valuation metrics from PavTECH master documents. Looks for a "Summary" or "Master" sheet first; falls back to aggregating across all data sheets. Calculates estimated portfolio value using commission multiple (3.5x annual commission) or premium percentage (35% of annual premium).

### email_service.py

Two email types via SendGrid:
- **Success email** - Valuation summary with product breakdown, attached master document
- **Error/pending email** - Notification that file was received but processing failed

Falls back to logging email content when SendGrid is not configured (development mode).

DM contacts are resolved by first name key (`paul`, `thomas`, `mike`) from a hardcoded contact list.

## Validation Philosophy

SourceTECH follows a **non-blocking validation** approach:

- Warns about missing fields instead of rejecting files
- Makes conservative assumptions when data is missing
- Reports what was found (positives) before what's missing (warnings)
- Only rejects truly unusable files (empty, unreadable, no premium column at all)

The goal is: busy vendors upload a file, get a rough valuation immediately, and can refine later.

## Deployment

### Render.com

Deploy using the included `render.yaml`:

```bash
render deploy
```

The configuration provides:
- Python web service with Gunicorn (2 workers, 120s timeout)
- Health check on `/health`
- 1GB persistent disk mounted at `/data` (database + uploads)
- Auto-generated `SECRET_KEY`
- Manual sync required for `SENDGRID_API_KEY` and `ADMIN_PASSWORD`

### Manual Deployment

1. Create a new Web Service on Render
2. Connect your repository
3. Set environment variables (see table above)
4. Ensure persistent disk is mounted at `/data`
5. Deploy

## Required File Format

Uploaded Excel (.xlsx, .xls) or CSV files should contain insurance portfolio data with columns for:

- **Premium** (required) - Annual premium or premium amount with frequency
- **Benefit Type** (optional) - Product type: Life, TPD, Trauma, Income Protection
- **Status** (optional) - Policy status: In Force, Active, Current, etc.
- **DOB/Age** (optional) - Date of birth or client age

Column names are matched fuzzily - exact naming is not required.
