"""
DealTECH callback client.

When a vendor uploads (and PII is stripped), SourceTECH calls DealTECH's
`POST /api/pavtech/webhook/sourcetech` so DealTECH auto-checks the Ver3 P1 "Inforce Data
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

    # DealTECH serves this under its pavtech router: /api/pavtech/webhook/sourcetech
    # (the bare /webhook/sourcetech path 405s against DealTECH's SPA catch-all; every
    # vendor upload since v2 hit that 405, so the P1 "Inforce Data Received" tick
    # never fired. Found by the 24 Sep 2026 smoke test on the ZZ TEST deal.)
    url = f"{DEALTECH_API_URL}/api/pavtech/webhook/sourcetech"
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


# 3.1.0: SourceTECH's main page lists the most recently active HubSpot deals (via DealTECH,
# which owns HubSpot access), each with its SourceTECH link; older deals get one on demand.
def recent_deals(limit: int = 40, timeout: float = 20.0) -> dict:
    """{'deals': [...]} or {'error': '...'}; never raises."""
    if not DEALTECH_API_URL or not WEBHOOK_SECRET:
        return {"error": "DEALTECH_API_URL / WEBHOOK_SECRET not set on SourceTECH"}
    try:
        r = requests.get(f"{DEALTECH_API_URL}/api/vendor-research/recent-deals", params={"limit": limit},
                         headers={"X-Webhook-Secret": WEBHOOK_SECRET}, timeout=timeout)
        if r.status_code != 200:
            logger.error("recent deals: DealTECH %s %s", r.status_code, r.text[:200])
            return {"error": f"DealTECH {r.status_code}"}
        return r.json()
    except requests.RequestException as e:
        logger.error("recent deals: DealTECH unreachable: %s", e)
        return {"error": f"DealTECH unreachable: {e}"}


def create_link(deal_id: str, timeout: float = 30.0) -> dict:
    if not DEALTECH_API_URL or not WEBHOOK_SECRET:
        return {"error": "DEALTECH_API_URL / WEBHOOK_SECRET not set on SourceTECH"}
    try:
        r = requests.post(f"{DEALTECH_API_URL}/api/vendor-research/sourcetech-link", json={"deal_id": str(deal_id)},
                          headers={"X-Webhook-Secret": WEBHOOK_SECRET}, timeout=timeout)
        return r.json() if r.status_code == 200 else {"error": f"DealTECH {r.status_code}: {r.text[:200]}"}
    except requests.RequestException as e:
        return {"error": f"DealTECH unreachable: {e}"}
