"""
DealTECH callback client.

When a vendor uploads (and PII is stripped), SourceTECH calls DealTECH's
`POST /webhook/sourcetech` so DealTECH auto-checks the Ver3 P1 "Inforce Data
Received (PII removed)" checkbox on the matching deal.

Best-effort: a DealTECH outage must never fail the vendor's upload. All errors
are logged and swallowed.

Config (env):
  DEALTECH_API_URL   base URL of the DealTECH service (e.g. https://dealtech.onrender.com)
  WEBHOOK_SECRET     shared secret; sent as X-Webhook-Secret (DealTECH requires it)
"""
import logging
import os

import requests

logger = logging.getLogger("sourcetech.dealtech")

DEALTECH_API_URL = os.environ.get("DEALTECH_API_URL", "").rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


def notify_data_received(deal_id, file_url: str, timeout: float = 10.0) -> bool:
    """Tell DealTECH a vendor has uploaded inforce data for `deal_id`.

    Returns True on a 2xx from DealTECH, False otherwise (never raises).
    No-ops (returns False) if deal_id is missing or DEALTECH_API_URL unset.
    """
    if not deal_id:
        return False
    if not DEALTECH_API_URL:
        logger.info("DEALTECH_API_URL not set — skipping DealTECH notify for deal %s", deal_id)
        return False

    url = f"{DEALTECH_API_URL}/webhook/sourcetech"
    headers = {"Content-Type": "application/json"}
    if WEBHOOK_SECRET:
        headers["X-Webhook-Secret"] = WEBHOOK_SECRET
    try:
        resp = requests.post(
            url,
            json={"deal_id": deal_id, "file_url": file_url or ""},
            headers=headers,
            timeout=timeout,
        )
        if 200 <= resp.status_code < 300:
            logger.info("DealTECH notified: deal %s data received", deal_id)
            return True
        logger.warning(
            "DealTECH notify failed (deal %s): %s %s",
            deal_id, resp.status_code, resp.text[:200],
        )
        return False
    except requests.RequestException as e:
        logger.warning("DealTECH notify error (deal %s): %s", deal_id, e)
        return False
