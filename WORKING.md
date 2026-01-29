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

## Architecture Flow
```
Vendor Upload -> Validation -> PII Strip -> PavTECH Processing -> Master Doc -> Email to DM
```

## Current Status
- Initial codebase - just initialized git repository
- No previous commits or branches

## What We're Working On
- [ ] Getting up to speed on codebase (DONE)
- [ ] Initial git setup and commit

## Completed Work
- Initial project exploration and documentation
