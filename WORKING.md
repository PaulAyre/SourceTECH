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
Vendor Upload -> Validation -> PII Strip -> PavTECH Processing -> Master Doc -> Email to DM
```

## Current Status
- v2.0 UI redesign complete
- Git initialized, initial commit on master

## Version History
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
- `3c6768a` - Initial commit (v1.0 baseline)
