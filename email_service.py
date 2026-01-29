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

# Fixed DM contact list
DM_CONTACTS = {
    'paul': {
        'name': 'Paul Ayre',
        'email': 'impaulayre@gmail.com'
    },
    'thomas': {
        'name': 'Thomas Hawke',
        'email': 'thomas@insuranceplus.com.au'
    },
    'mike': {
        'name': 'Mike Clifford',
        'email': 'mike@insuranceplus.com.au'
    }
}

def get_dm_contact(dm_key: str) -> Dict:
    """Get DM contact info by key or return default."""
    return DM_CONTACTS.get(dm_key.lower(), DM_CONTACTS['paul'])


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
