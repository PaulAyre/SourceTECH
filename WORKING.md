# SourceTECH - Working Log

## Project Overview
**Vendor Portfolio Upload Portal** - A Flask web app that enables insurance vendors to upload portfolio files via unique URLs. Files are validated, PII-stripped, processed through PavTECH for valuation, and results emailed to account managers via SendGrid.

## Tech Stack
- **Backend:** Python/Flask 3.0.0, SQLite3, Gunicorn
- **Data Processing:** Pandas, OpenPyXL
- **External Services:** PavTECH API (valuation engine), SendGrid (email)
- **Frontend:** Flask templates, Bootstrap, vanilla JS, drag-and-drop upload
- **Deployment:** Render.com

## Key Files
| File | Purpose |
|------|---------|
| `app.py` | Main Flask app (831 lines) - all routes & logic |
| `validator.py` | Portfolio file validation (required fields check) |
| `pii_stripper.py` | PII removal (emails, phones, addresses, names) |
| `pavtech_client.py` | PavTECH API integration (batch upload/process) |
| `excel_parser.py` | Master document extraction (valuation summary) |
| `email_service.py` | SendGrid email notifications |
| `static/css/style.css` | Complete design system v2.0 (PavTECH-inspired) |

## Architecture Flow
```
Vendor Upload -> Validation -> PII Strip -> PavTECH Processing -> Master Doc -> Email to DM (List of DM's comes from Hubspot)
```

## Current Status
- v2.0 UI redesign complete and committed
- Git initialized, v2.0 committed on master

## Version History
- **v2.4.0** - Best-of-both-worlds UX merge from Thomas Hawke's valu-guide-sync prototype (team feedback 2026-07-29): per-tile download guides collapsed behind a "How to download your file" dropdown; step indicator (Select / Upload / Review / Done); Review & Submit modal backed by new GET /<code>/review (same dedup as submit); submission receipt with quotable reference (ST-YYYYMMDD-XXXX, new submissions.reference column); APP_VERSION single source of truth; PavTECH health check moved to /api/version (GET / sits behind PavTECH's rate limiter and 429s, falsely reporting degraded)
- **v2.3.x** - Prod hardening: secure_filename, fail-closed API secret, render.yaml parity, DM email error scrubbing, fromjson 500 fix; HubSpot P1 gate kept enforced
- **v2.2.0** - Submit action, filename-dedup working set, per-tile files, Resend DM email, vendor tiles UI
- **v2.0** - Full UI redesign with PavTECH styling, insurer sidebar, smart drag-and-drop
- **v1.0** - Initial codebase (commit 3c6768a)

## What We're Working On
- [x] Getting up to speed on codebase
- [x] Initial git setup and commit
- [x] v2.0 UI redesign (PavTECH style, insurer sidebar, smart upload)

## Completed Work
- Initial project exploration and documentation
- Git repo initialized (commit 3c6768a on master)
- **v2.0 UI Redesign:**
  - Complete CSS design system based on PavTECH brand (green gradients, cream sidebar, modern cards)
  - Upload page with left sidebar containing 9 Australian insurer download guides
  - Each insurer has: step-by-step instructions, portal link, format info, adviser code notes
  - Smart drag-and-drop with client-side duplicate filename detection
  - Handles Chrome download suffixes: (1), (2), _1, _2, "copy" variants
  - Sequential file processing queue with per-file duplicate confirmation
  - Mobile-responsive sidebar (drawer on mobile, sticky on desktop)
  - InsurancePLUS logo integration in header
  - Admin templates updated with new nav styling and gradient stat cards

## Stable Commits
- `491aec1` - v2.0 UI redesign (PavTECH styling, insurer sidebar, smart upload)
- `3c6768a` - Initial commit (v1.0 baseline)
