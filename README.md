# SourceTECH

Vendor Portfolio Upload Portal - secure file upload with automatic PII stripping and PavTECH integration.

## Features

- **Vendor Upload Portal**: Simple drag-and-drop file upload for vendors
- **PII Stripping**: Automatically removes personal information (emails, phones, addresses, names)
- **File Validation**: Ensures uploaded files have required fields for valuation
- **PavTECH Integration**: Seamlessly processes files through PavTECH for valuation
- **Email Notifications**: Sends valuation results to account managers via SendGrid
- **Admin Portal**: Manage vendors and track submissions

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
| `SECRET_KEY` | Flask secret key | Random |
| `PAVTECH_API_URL` | PavTECH API endpoint | `http://localhost:5000` |
| `DATABASE` | SQLite database path | `sourcetech.db` |
| `TEMP_DIR` | Temporary file directory | `/tmp/sourcetech` |
| `ADMIN_PASSWORD` | Admin portal password | `changeme` |
| `SENDGRID_API_KEY` | SendGrid API key | None |
| `FROM_EMAIL` | Sender email address | `sourcetech@example.com` |

## Usage

### Admin Portal

1. Login at `/admin` with the admin password
2. Create a vendor link with:
   - Vendor/Practice name
   - Account Manager (DM) name and email
   - Optional notes
3. Copy the generated link and send to the vendor

### Vendor Upload

1. Vendor visits their unique URL (e.g., `/GTRHER2W`)
2. Drags and drops their portfolio file
3. File is validated for required fields
4. PII is automatically stripped
5. File is processed through PavTECH
6. DM receives valuation email with attached master document

## Required File Fields

Uploaded portfolios must contain:
- **Premium**: Annual premium or premium amount with frequency
- **Benefit Type**: Product type (Life, TPD, Trauma, IP)
- **Status**: Policy status (In Force / Active)
- **DOB/Age**: Date of birth or client age

## Architecture

```
Vendor Browser  →  SourceTECH  →  PavTECH
     ↓                ↓              ↓
  Upload         Validate       Process
                 Strip PII      Generate Master
                     ↓              ↓
                 SendGrid  ←  Download Master
                     ↓
                 DM Email + Attachment
```

## Deployment

Deploy to Render using the included `render.yaml`:

```bash
render deploy
```

Or manually:
1. Create a new Web Service on Render
2. Connect your repository
3. Set environment variables
4. Deploy
