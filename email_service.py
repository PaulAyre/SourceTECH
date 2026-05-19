"""
Email Service for SourceTECH.
Sends notifications via SendGrid with optional Excel attachments.
"""
import os
import base64
from pathlib import Path
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)

# Try to import SendGrid
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (
        Mail, Attachment, FileContent, FileName,
        FileType, Disposition
    )
    SENDGRID_AVAILABLE = True
except ImportError:
    SENDGRID_AVAILABLE = False
    logger.warning("SendGrid not installed. Email functionality disabled.")

SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'sourcetech@example.com')

# DM contact list — externalised in INS-62.
#
# Resolution order (first hit wins):
#   1. Per-DM env var: DM_<KEY>_EMAIL and DM_<KEY>_NAME
#      e.g. DM_PAUL_EMAIL=paul@insuranceplus.com.au DM_PAUL_NAME="Paul Ayre"
#   2. JSON blob env var: DM_CONTACTS_JSON='{"paul": {"name": "...", "email": "..."}}'
#   3. Built-in fallback (intentionally NOT impaulayre@gmail.com — that was a
#      personal address leaked into production code; default the fallback to
#      InsurancePLUS-domain only).
#
# Vendor admin form (long-term plan): when a vendor is created in /admin/vendors/create
# the DM dropdown should write into a `dm_contacts` SQLite table that overrides
# everything below. Scaffolded in INS-62 follow-up.
import json as _json

_BUILT_IN_DMS = {
    'paul':   {'name': 'Paul Ayre',     'email': 'paul@insuranceplus.com.au'},
    'thomas': {'name': 'Thomas Hawke',  'email': 'thomas@insuranceplus.com.au'},
    'mike':   {'name': 'Mike Clifford', 'email': 'mike@insuranceplus.com.au'},
}


def _load_dm_contacts() -> Dict:
    """Build the DM map from env, falling back to built-ins."""
    contacts = {k: dict(v) for k, v in _BUILT_IN_DMS.items()}

    # Layer 2: JSON blob env var overrides built-ins.
    raw = os.environ.get('DM_CONTACTS_JSON')
    if raw:
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(v, dict) and 'email' in v:
                        contacts[k.lower()] = {
                            'name': v.get('name', k.title()),
                            'email': v['email'],
                        }
        except _json.JSONDecodeError as exc:
            logger.error("DM_CONTACTS_JSON parse error: %s — falling back to built-ins", exc)

    # Layer 1: per-DM env vars override everything else.
    for key in list(contacts.keys()) + ['paul', 'thomas', 'mike']:
        env_email = os.environ.get(f"DM_{key.upper()}_EMAIL")
        env_name = os.environ.get(f"DM_{key.upper()}_NAME")
        if env_email:
            contacts.setdefault(key.lower(), {'name': key.title(), 'email': env_email})
            contacts[key.lower()]['email'] = env_email
            if env_name:
                contacts[key.lower()]['name'] = env_name

    return contacts


DM_CONTACTS = _load_dm_contacts()


def _normalise_dm_key(dm_key: str) -> str:
    """Normalise a DM lookup key — strip whitespace, lowercase, drop punctuation."""
    return ''.join(ch for ch in dm_key.lower().strip() if ch.isalnum())


# INS-62 follow-up: optional DB-backed override layer.
# If app.py registers a callable here via register_dm_db_lookup, that callable
# is consulted FIRST for every get_dm_contact call. Empty / missing rows fall
# through to env + built-ins.
#
# Expected callable signature:
#     fn(dm_key: str) -> Optional[Dict[str, str]]
#     returning {'name': str, 'email': str} for a hit, None for a miss.
_dm_db_lookup: Optional[callable] = None  # type: ignore[assignment]


def register_dm_db_lookup(fn) -> None:
    """Install a DB-backed lookup function. app.py calls this at startup."""
    global _dm_db_lookup
    _dm_db_lookup = fn
    logger.info("DM DB-backed lookup registered (callable=%s)", getattr(fn, '__name__', fn))


def get_dm_contact(dm_key: str) -> Dict:
    """
    Get DM contact info by key.

    Resolution (first hit wins):
      0. DB-backed override (INS-62 follow-up). Admin-edited dm_contacts table
         in the SQLite DB. None means "no DB override" — fall through.
      1. Exact (case-insensitive) match on _normalise_dm_key against the
         env-driven DM_CONTACTS map.
      2. Typo-tolerant fallback — closest match by SequenceMatcher ratio
         if it scores >= 0.75. Catches "paull", "tomas", "mik", etc.
      3. Built-in default (paul) — logged at WARN so we can spot pattern
         drift over time.
    """
    if not dm_key:
        logger.warning("get_dm_contact called with empty key, defaulting to 'paul'")
        return DM_CONTACTS['paul']

    norm = _normalise_dm_key(dm_key)

    # Layer 0: DB-backed override.
    if _dm_db_lookup is not None:
        try:
            row = _dm_db_lookup(norm)
            if row and row.get('email'):
                logger.info("DM key %r matched DB-backed override", dm_key)
                return {'name': row.get('name', norm.title()), 'email': row['email']}
        except Exception as exc:  # noqa: BLE001
            logger.warning("DM DB lookup failed for key=%r (%s) — falling back to env/built-in",
                           dm_key, exc)

    if norm in DM_CONTACTS:
        return DM_CONTACTS[norm]

    # Typo-tolerant
    import difflib as _difflib
    best = None
    best_score = 0.0
    for k in DM_CONTACTS:
        score = _difflib.SequenceMatcher(None, norm, k).ratio()
        if score > best_score:
            best, best_score = k, score
    if best and best_score >= 0.75:
        logger.info("DM key %r matched %r via typo-tolerant lookup (score=%.2f)",
                    dm_key, best, best_score)
        return DM_CONTACTS[best]

    logger.warning("DM key %r did not match any contact, defaulting to 'paul'", dm_key)
    return DM_CONTACTS['paul']


def format_currency(amount: float) -> str:
    """Format number as Australian currency."""
    if amount >= 1000000:
        return f"${amount/1000000:,.1f}M"
    elif amount >= 1000:
        return f"${amount/1000:,.0f}K"
    else:
        return f"${amount:,.0f}"


def send_dm_notification(
    to_email: str,
    dm_name: str,
    vendor_name: str,
    url_code: str,
    valuation: Optional[Dict] = None,
    pii_report: Optional[Dict] = None,
    attachment_path: Optional[Path] = None,
    error: Optional[str] = None
) -> bool:
    """
    Send notification email to DM about portfolio submission.

    Args:
        to_email: DM's email address
        dm_name: DM's name for greeting
        vendor_name: Name of the vendor who submitted
        url_code: Reference code for the submission
        valuation: Dict with valuation summary (if successful)
        pii_report: Dict with PII stripping report
        attachment_path: Path to master document to attach
        error: Error message (if processing failed)

    Returns:
        bool: True if email sent successfully
    """
    if not SENDGRID_AVAILABLE:
        logger.warning(f"Would send email to {to_email} but SendGrid not available")
        _log_email_content(to_email, dm_name, vendor_name, url_code, valuation, error)
        return False

    if not SENDGRID_API_KEY:
        logger.warning(f"Would send email to {to_email} but SENDGRID_API_KEY not set")
        _log_email_content(to_email, dm_name, vendor_name, url_code, valuation, error)
        return False

    # Build email content
    if valuation and not valuation.get('error'):
        subject = f"Portfolio Valued - {vendor_name}"
        body = _build_success_email(dm_name, vendor_name, url_code, valuation, pii_report)
    elif error:
        subject = f"Portfolio Received (Valuation Pending) - {vendor_name}"
        body = _build_error_email(dm_name, vendor_name, url_code, error)
    else:
        subject = f"Portfolio Received - {vendor_name}"
        body = _build_received_email(dm_name, vendor_name, url_code)

    # Build email message
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject=subject,
        plain_text_content=body
    )

    # Attach master document if available
    if attachment_path and Path(attachment_path).exists():
        try:
            with open(attachment_path, 'rb') as f:
                data = f.read()

            encoded_file = base64.b64encode(data).decode()

            attachment = Attachment(
                FileContent(encoded_file),
                FileName(Path(attachment_path).name),
                FileType('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                Disposition('attachment')
            )
            message.attachment = attachment
            logger.info(f"Attached: {attachment_path}")
        except Exception as e:
            logger.error(f"Failed to attach file: {e}")

    # Send via SendGrid
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        success = response.status_code in [200, 201, 202]
        if success:
            logger.info(f"Email sent to {to_email}")
        else:
            logger.error(f"Email failed: {response.status_code}")
        return success
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def _build_success_email(dm_name: str, vendor_name: str, url_code: str,
                         valuation: Dict, pii_report: Optional[Dict]) -> str:
    """Build success email body with valuation summary."""
    # Build product breakdown
    product_lines = []
    for product, count in valuation.get('product_breakdown', {}).items():
        product_lines.append(f"  - {product}: {count} policies")
    product_section = "\n".join(product_lines) if product_lines else "  (breakdown not available)"

    pii_count = len(pii_report.get('columns_removed', [])) if pii_report else 0

    body = f"""Hi {dm_name},

{vendor_name} has submitted their portfolio and it's been valued.

VALUATION SUMMARY
-----------------
Total Policies:        {valuation.get('total_policies', 0):,}
In-Force Policies:     {valuation.get('in_force_policies', 0):,}
Annual Premium:        {format_currency(valuation.get('total_annual_premium', 0))}
Annual Commission:     {format_currency(valuation.get('total_annual_commission', 0))}
Estimated Value:       {format_currency(valuation.get('estimated_value', 0))}

Product Mix:
{product_section}

The full valuation report is attached.

SUBMISSION DETAILS
-----------------
Reference:    {url_code}
PII Removed:  {pii_count} columns stripped

-----------------
SourceTECH
"""
    return body


def _build_error_email(dm_name: str, vendor_name: str, url_code: str, error: str) -> str:
    """Build email body for failed processing."""
    return f"""Hi {dm_name},

{vendor_name} has submitted their portfolio.

The file has been received and saved, but automated valuation encountered an issue:

{error}

The file is available for manual processing in PavTECH.

Reference: {url_code}

-----------------
SourceTECH
"""


def _build_received_email(dm_name: str, vendor_name: str, url_code: str) -> str:
    """Build simple received confirmation email."""
    return f"""Hi {dm_name},

{vendor_name} has submitted their portfolio.

Processing is in progress. You'll receive another email when valuation is complete.

Reference: {url_code}

-----------------
SourceTECH
"""


def _log_email_content(to_email: str, dm_name: str, vendor_name: str,
                       url_code: str, valuation: Optional[Dict], error: Optional[str]):
    """Log email content when SendGrid is not available (for development)."""
    logger.info("=" * 50)
    logger.info(f"EMAIL TO: {to_email}")
    logger.info(f"SUBJECT: Portfolio {'Valued' if valuation else 'Received'} - {vendor_name}")
    logger.info(f"DM: {dm_name}")
    logger.info(f"Reference: {url_code}")
    if valuation:
        logger.info(f"Policies: {valuation.get('total_policies', 0)}")
        logger.info(f"Premium: ${valuation.get('total_annual_premium', 0):,.0f}")
    if error:
        logger.info(f"Error: {error}")
    logger.info("=" * 50)


def send_portfolio_update_notification(
    dm_key: str,
    vendor_name: str,
    url_code: str,
    file_count: int,
    files_summary: List[Dict],
    valuation: Optional[Dict] = None,
    assumptions: Optional[List[str]] = None,
    attachment_path: Optional[Path] = None
) -> bool:
    """
    Send notification about portfolio update to assigned DM.

    Args:
        dm_key: Key to look up DM contact (e.g., 'paul', 'thomas', 'mike')
        vendor_name: Name of the vendor
        url_code: Reference code
        file_count: Number of files in portfolio
        files_summary: List of dicts with filename, policies, status
        valuation: Optional valuation summary
        assumptions: List of assumption warnings made
        attachment_path: Optional master document to attach
    """
    dm = get_dm_contact(dm_key)
    to_email = dm['email']
    dm_name = dm['name']

    if not SENDGRID_AVAILABLE or not SENDGRID_API_KEY:
        logger.info(f"Would send portfolio update to {to_email}")
        logger.info(f"  Vendor: {vendor_name}, Files: {file_count}")
        if valuation:
            logger.info(f"  Valuation: {format_currency(valuation.get('estimated_value', 0))}")
        return False

    # Build email
    subject = f"Portfolio Updated - {vendor_name} ({file_count} files)"

    # Build file list
    file_lines = []
    for f in files_summary:
        status_icon = "✓" if f.get('status') == 'valid' else "⚠️"
        file_lines.append(f"  {status_icon} {f.get('filename', 'Unknown')} - {f.get('policies', 0):,} policies")
    files_section = "\n".join(file_lines) if file_lines else "  No files"

    # Build assumptions section
    assumptions_section = ""
    if assumptions:
        assumptions_section = "\nASSUMPTIONS MADE\n----------------\n"
        for a in assumptions:
            assumptions_section += f"  • {a}\n"

    # Build valuation section
    valuation_section = ""
    if valuation:
        valuation_section = f"""
ESTIMATED VALUATION
-------------------
Total Policies:     {valuation.get('total_policies', 0):,}
Annual Premium:     {format_currency(valuation.get('total_annual_premium', 0))}
Estimated Value:    {format_currency(valuation.get('estimated_value', 0))}
"""

    body = f"""Hi {dm_name},

{vendor_name} has updated their portfolio.

FILES UPLOADED ({file_count})
{'-' * 20}
{files_section}
{assumptions_section}{valuation_section}
Reference: {url_code}
View/manage: [Portal link would go here]

-----------------
SourceTECH
"""

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject=subject,
        plain_text_content=body
    )

    # Attach master document if available
    if attachment_path and Path(attachment_path).exists():
        try:
            with open(attachment_path, 'rb') as f:
                data = f.read()
            encoded_file = base64.b64encode(data).decode()
            attachment = Attachment(
                FileContent(encoded_file),
                FileName(Path(attachment_path).name),
                FileType('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                Disposition('attachment')
            )
            message.attachment = attachment
        except Exception as e:
            logger.error(f"Failed to attach file: {e}")

    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        success = response.status_code in [200, 201, 202]
        if success:
            logger.info(f"Portfolio update email sent to {to_email}")
        return success
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False
