"""
Email Service for SourceTECH.

Sends DM notifications via Resend (raw HTTPS POST, mirroring DealTECH's pattern).
SourceTECH runs synchronous background threads, so this uses `requests` rather
than async httpx. Fails LOUDLY: any missing key or non-2xx from Resend is logged
at ERROR and returned as a failure, never a silent no-op.
"""
import os
from html import escape as html_escape
import re
import base64
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Resend config. FROM_EMAIL must be on a Resend-verified domain (insuranceplus.com.au,
# the same domain DealTECH sends from) or the send is rejected.
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'SourceTECH <sourcetech@insuranceplus.com.au>')
RESEND_ENDPOINT = 'https://api.resend.com/emails'


def format_currency(amount) -> str:
    """Format a number as Australian currency (compact)."""
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount >= 1_000_000:
        return f"${amount/1_000_000:,.1f}M"
    if amount >= 1_000:
        return f"${amount/1_000:,.0f}K"
    return f"${amount:,.0f}"


def send_email(to_email: str, subject: str, html_body: str,
               text_body: Optional[str] = None,
               attachment_path: Optional[Path] = None) -> Tuple[bool, Dict]:
    """Send an email via Resend. Returns (success, info).

    info carries the Resend message id on success, or the rejection detail on
    failure. Logs LOUDLY on any failure so a missing key or bad sender never
    passes as a silent success.
    """
    if not RESEND_API_KEY:
        logger.error("EMAIL NOT SENT (RESEND_API_KEY not set) to=%s subject=%r", to_email, subject)
        return False, {'error': 'RESEND_API_KEY not set'}

    payload = {
        'from': FROM_EMAIL,
        'to': [to_email],
        'subject': subject,
        'html': html_body,
    }
    if text_body:
        payload['text'] = text_body

    if attachment_path and Path(attachment_path).exists():
        try:
            data = Path(attachment_path).read_bytes()
            payload['attachments'] = [{
                'filename': Path(attachment_path).name,
                'content': base64.b64encode(data).decode(),
            }]
        except Exception as e:
            logger.error("Failed to read attachment %s: %s", attachment_path, e)

    try:
        resp = requests.post(
            RESEND_ENDPOINT,
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=20,
        )
    except Exception as e:
        logger.error("Resend send raised for to=%s subject=%r: %s", to_email, subject, e)
        return False, {'error': str(e)}

    if resp.status_code >= 300:
        logger.error("Email REJECTED by Resend (HTTP %s) to=%s subject=%r body=%s",
                     resp.status_code, to_email, subject, resp.text[:400])
        return False, {'status': resp.status_code, 'body': resp.text[:400]}

    message_id = None
    try:
        message_id = resp.json().get('id')
    except Exception:
        pass
    logger.info("Email sent via Resend id=%s to=%s subject=%r", message_id, to_email, subject)
    return True, {'id': message_id, 'status': resp.status_code}


def _humanize_error(error: Optional[str]) -> str:
    """Turn an internal error string into DM-appropriate copy.

    A deal manager should never see a raw HTTP code or traceback. PavTECH's own
    gate messages are already human-readable and useful, so pass those through;
    but scrub bare "Upload failed: 500" / exception-looking strings down to a
    clean, generic line. The raw error still lives in the logs + submission record.
    """
    detail = (error or '').strip()
    if not detail:
        return 'Automated valuation did not complete.'
    low = detail.lower()
    looks_technical = (
        re.match(r'^upload failed:\s*\d+\s*$', low) is not None
        or 'traceback' in low
        or 'exception' in low
        or low.startswith('httperror')
        or low.startswith('connectionerror')
    )
    if looks_technical:
        return 'The automated valuation could not be completed. The files have been saved and queued for manual processing in PavTECH.'
    return detail


def _pavtech_valuation_or_none(pavtech_valuation) -> Optional[float]:
    """PavTECH's real batch valuation as a positive float, else None.

    INTERNAL (DM email) only. Never a locally computed estimate: if PavTECH did
    not report a positive figure the line is dropped rather than shown as $0.
    """
    try:
        amount = float(pavtech_valuation)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _valuation_rows_html(valuation: Dict, pavtech_valuation=None) -> str:
    rows = [
        ('Total policies', f"{valuation.get('total_policies', 0):,}"),
        ('In-force policies', f"{valuation.get('in_force_policies', 0):,}"),
        ('Annual premium', format_currency(valuation.get('total_annual_premium', 0))),
        ('Annual commission', format_currency(valuation.get('total_annual_commission', 0))),
    ]
    amount = _pavtech_valuation_or_none(pavtech_valuation)
    if amount is not None:
        rows.append(('PavTECH valuation', format_currency(amount)))
    trs = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#495057;">{label}</td>'
        f'<td style="padding:4px 0;font-weight:600;color:#1a202c;">{value}</td></tr>'
        for label, value in rows
    )
    return f'<table style="border-collapse:collapse;margin:12px 0;">{trs}</table>'


def send_dm_notification(
    to_email: str,
    dm_name: str,
    vendor_name: str,
    url_code: str,
    valuation: Optional[Dict] = None,
    pii_report: Optional[Dict] = None,
    attachment_path: Optional[Path] = None,
    error: Optional[str] = None,
    pavtech_valuation: Optional[float] = None,
    extra_notes: Optional[list] = None,
) -> Tuple[bool, Dict]:
    """Notify the Deal Manager about a submitted portfolio.

    Success (valuation present, no error): "valuation complete for <vendor>",
    with the master output attached. Failure (error set): "received, valuation
    issue". Always sends to the passed to_email (the vendor's stored dm_email).

    INTERNAL ONLY: this email goes to the Deal Manager, never the vendor, so it
    may carry figures. pavtech_valuation is PavTECH's real batch total
    (process_batch total_valuation); the line is omitted when it is not a
    positive number.
    """
    greeting = dm_name or 'there'
    # Things the DM should know about this submission (files that never arrived,
    # personal details the server had to remove, files that need a look). The
    # vendor is never blocked by these; the DM is always told.
    notes = [str(n) for n in (extra_notes or []) if n]
    notes_html = ("<p><strong>Worth knowing:</strong></p><ul>" + "".join(f"<li>{html_escape(n)}</li>" for n in notes) + "</ul>") if notes else ""
    notes_text = ("Worth knowing:\n" + "".join(f"  - {n}\n" for n in notes) + "\n") if notes else ""
    pavtech_amount = _pavtech_valuation_or_none(pavtech_valuation)
    pavtech_line = (
        f"PavTECH valuation: {format_currency(pavtech_amount)}\n"
        if pavtech_amount is not None else ""
    )

    if valuation and not valuation.get('error'):
        subject = f"Portfolio valuation complete - {vendor_name}"
        html_body = f"""\
<div style="font-family:Calibri,Segoe UI,Arial,sans-serif;color:#1a202c;">
  <p>Hi {greeting},</p>
  <p>The portfolio valuation is complete for <strong>{vendor_name}</strong>.</p>
  {_valuation_rows_html(valuation, pavtech_valuation)}
  <p>The full valuation master is attached to this email.</p>
  {notes_html}
  <p style="color:#6c757d;font-size:13px;">Reference: {url_code}</p>
  <p style="color:#1e5631;font-weight:600;">InsurancePLUS SourceTECH</p>
</div>"""
        text_body = (
            f"Hi {greeting},\n\n"
            f"The portfolio valuation is complete for {vendor_name}.\n\n"
            f"Total policies: {valuation.get('total_policies', 0):,}\n"
            f"In-force policies: {valuation.get('in_force_policies', 0):,}\n"
            f"Annual premium: {format_currency(valuation.get('total_annual_premium', 0))}\n"
            f"Annual commission: {format_currency(valuation.get('total_annual_commission', 0))}\n"
            f"{pavtech_line}\n"
            f"The full valuation master is attached.\n\n"
            f"{notes_text}"
            f"Reference: {url_code}\n\nInsurancePLUS SourceTECH\n"
        )
    else:
        subject = f"Portfolio received (valuation issue) - {vendor_name}"
        detail = _humanize_error(error)
        html_body = f"""\
<div style="font-family:Calibri,Segoe UI,Arial,sans-serif;color:#1a202c;">
  <p>Hi {greeting},</p>
  <p><strong>{vendor_name}</strong> has submitted their portfolio and the files were received and saved.</p>
  <p>Automated valuation hit an issue: {detail}</p>
  <p>The files are available for manual processing in PavTECH.</p>
  {notes_html}
  <p style="color:#6c757d;font-size:13px;">Reference: {url_code}</p>
  <p style="color:#1e5631;font-weight:600;">InsurancePLUS SourceTECH</p>
</div>"""
        text_body = (
            f"Hi {greeting},\n\n"
            f"{vendor_name} has submitted their portfolio; files received and saved.\n\n"
            f"Automated valuation hit an issue: {detail}\n\n"
            f"The files are available for manual processing in PavTECH.\n\n"
            f"{notes_text}"
            f"Reference: {url_code}\n\nInsurancePLUS SourceTECH\n"
        )

    return send_email(to_email, subject, html_body, text_body=text_body, attachment_path=attachment_path)
