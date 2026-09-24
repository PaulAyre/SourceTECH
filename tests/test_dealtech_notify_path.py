"""SourceTECH -> DealTECH "data received" callback hits the route DealTECH actually serves.

DealTECH mounts the handler at /api/pavtech/webhook/sourcetech (app/api/pavtech.py,
router prefix /api/pavtech). The bare /webhook/sourcetech path answers 405 from
DealTECH's SPA catch-all, which is what every upload got until 24 Sep 2026.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_notify_posts_to_dealtech_pavtech_router(monkeypatch):
    import dealtech_client as dc

    monkeypatch.setattr(dc, 'DEALTECH_API_URL', 'https://dealtech.example')
    monkeypatch.setattr(dc, 'WEBHOOK_SECRET', 'shh')
    seen = {}

    class R:
        status_code = 200
        text = 'ok'

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return R()

    monkeypatch.setattr(dc.requests, 'post', fake_post)
    assert dc.notify_data_received(294200920556, file_url='https://sourcetech.onrender.com/_ABC') is True
    assert seen['url'] == 'https://dealtech.example/api/pavtech/webhook/sourcetech'
    assert seen['json'] == {'deal_id': 294200920556, 'file_url': 'https://sourcetech.onrender.com/_ABC'}
    assert seen['headers']['X-Webhook-Secret'] == 'shh'


def test_notify_never_raises_on_failure(monkeypatch):
    import dealtech_client as dc

    monkeypatch.setattr(dc, 'DEALTECH_API_URL', 'https://dealtech.example')

    class R:
        status_code = 405
        text = 'Method Not Allowed'

    monkeypatch.setattr(dc.requests, 'post', lambda *a, **k: R())
    assert dc.notify_data_received(1, file_url='') is False
