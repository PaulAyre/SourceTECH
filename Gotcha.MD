# SourceTECH - Gotchas

## Known Issues & Non-Obvious Behaviors

### 1. Database
- **SQLite is single-writer**: Background PavTECH processing uses threads with separate DB connections. Under load, this could cause `database is locked` errors.
- **No migrations**: Schema is created via `init_db()` on startup with `CREATE TABLE IF NOT EXISTS`. Adding columns requires manual ALTER TABLE or DB recreation.

### 2. PII Stripping
- PII stripping happens on upload, not on revaluation. The cleaned file is what gets sent to PavTECH.
- Name columns are anonymized to CLIENT_00001, CLIENT_00002, etc. - this is irreversible.

### 3. File Handling
- Files are saved to `uploads/<url_code>/` with timestamp prefix (e.g., `20250101_120000_portfolio.xlsx`)
- Cleaned files get `cleaned_` prefix and are always saved as `.xlsx` regardless of input format
- Fuzzy filename matching (60% threshold) triggers a confirmation dialog for potential replacements

### 4. PavTECH Integration
- PavTECH processing runs in a daemon thread - if the Flask process dies, processing is lost
- Polling timeout is up to 10 minutes (`pavtech_client.py`)
- PavTECH URL defaults to `http://localhost:5000` - must be configured via env var for production

### 5. Admin Portal
- Single password auth (no user accounts) - password stored in env var, default is "changeme"
- Session-based auth using Flask's built-in sessions

### 6. Email Service
- DM contacts are hardcoded in `email_service.py` (Paul, Thomas, Mike)
- Email requires SENDGRID_API_KEY env var - gracefully degrades if not set
- Currency is hardcoded to AUD

### 7. Deployment
- `render.yaml` configured for Render.com with 1GB persistent disk
- App runs on port 5002 locally (`app.py:831`)
- Gunicorn runs with 2 workers and 120s timeout in production

### 8. Validation Philosophy
- Validation is **non-blocking** - warns instead of rejecting files
- Missing fields result in assumptions (e.g., Age 63, Income Protection product)
- Required fields: Premium, Benefit Type, Status, DOB/Age

### 9. URL Codes
- Generated via `secrets.token_urlsafe(6)[:8].upper()` - 8 uppercase chars
- If collision occurs on insert, one retry is attempted (then would fail)
