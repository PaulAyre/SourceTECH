"""
HARD RULE regression test: SourceTECH is CUSTOMER FACING.

No valuation data may ever reach the vendor: no dollar value, estimated value,
multiple, commission total, master document path or PavTECH URL, on any
vendor-facing page or in any vendor-reachable API response.

The test seeds a vendor with a COMPLETED submission whose valuation_summary is
full of obviously fake sentinel numbers, then hits every vendor-reachable route
and asserts none of the sentinels (or the internal key names) come back.

Runs against a temp SQLite DB and temp uploads dir. app.py resolves DATABASE,
UPLOADS_DIR and TEMP_DIR from the environment at import time, so the env is set
BEFORE the module is imported. Real data is never touched.

Run:  pytest tests/test_no_vendor_valuation.py -v
"""
import io
import json
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
URL_CODE = 'SENTINEL1'
REFERENCE = 'ST-20260917-ABCD'

# Obviously fake figures. None of these may ever appear in a vendor response.
SENTINEL_NUMBERS = {
    'estimated_value': 987654321,
    'total_valuation': 123456789,
    'total_annual_commission': 555444333,
    'total_annual_premium': 777666555,
}
MASTER_SENTINEL = 'MASTER_SENTINEL_batch123'
PAVTECH_URL_SENTINEL = 'http://pavtech-sentinel.invalid:5999'

# Internal key names / strings that must never be vendor-visible.
FORBIDDEN_STRINGS = [
    'valuation_summary',
    'estimated_value',
    'total_valuation',
    'total_annual_commission',
    'master_document',
    'portfolioapp',
    'pavtech',            # covers the PavTECH URL, pavtech_status, pavtech_batch_id
    MASTER_SENTINEL.lower(),
]


def _number_forms(n: int):
    """Every way a figure could plausibly be rendered."""
    return {
        str(n),                      # 987654321
        f"{n:,}",                    # 987,654,321
        f"{n:,}".replace(',', ' '),  # 987 654 321
        f"{n / 1_000_000:,.1f}M",    # 987.7M (email_service.format_currency)
        f"{n / 1_000_000:,.2f}M",
        f"{n / 1_000:,.0f}K",
    }


@pytest.fixture(scope='module')
def st(tmp_path_factory):
    """Import app.py against a throwaway DB + uploads dir, seeded with a vendor
    that has a COMPLETED submission carrying sentinel valuation data."""
    base = tmp_path_factory.mktemp('sourcetech')
    uploads = base / 'uploads'
    mp = pytest.MonkeyPatch()
    mp.setenv('UPLOADS_DIR', str(uploads))
    mp.setenv('DATABASE', str(base / 'test.db'))
    mp.setenv('TEMP_DIR', str(base / 'temp'))
    mp.setenv('PAVTECH_API_URL', PAVTECH_URL_SENTINEL)
    mp.delenv('DEALTECH_API_URL', raising=False)
    mp.delenv('RESEND_API_KEY', raising=False)
    mp.syspath_prepend(str(REPO_ROOT))
    sys.modules.pop('app', None)
    import app as app_module

    assert app_module.DATABASE == str(base / 'test.db'), 'test must not touch the real DB'

    # The vendor's uploaded (already PII-stripped) file, so /review has content.
    vendor_dir = uploads / URL_CODE
    vendor_dir.mkdir(parents=True, exist_ok=True)
    cleaned = vendor_dir / 'cleaned_book.xlsx'
    pd.DataFrame({'Policy Number': ['P1', 'P2'], 'Premium': [100, 200]}).to_excel(cleaned, index=False)

    summary = dict(SENTINEL_NUMBERS)
    summary.update({
        'total_policies': 2,
        'in_force_policies': 2,
        'multiple': 3.5,
        'product_breakdown': {'Life': 2},
        'source_file': f'{MASTER_SENTINEL}.xlsx',
        'pavtech_url': f'{PAVTECH_URL_SENTINEL}/portfolioapp/batch/123',
    })

    db = sqlite3.connect(app_module.DATABASE)
    db.execute(
        "INSERT INTO vendors (url_code, vendor_name, dm_email, dm_name, status) "
        "VALUES (?, 'Sentinel Advisers', 'dm@example.invalid', 'Test DM', 'complete')",
        (URL_CODE,),
    )
    vendor_id = db.execute("SELECT id FROM vendors WHERE url_code = ?", (URL_CODE,)).fetchone()[0]
    db.execute(
        "INSERT INTO vendor_files (vendor_id, filename, original_filename, file_path, file_size, "
        "status, validation_warnings, pii_report, policy_count, processing_summary, insurer) "
        "VALUES (?, 'cleaned_book.xlsx', 'book.xlsx', ?, 1234, 'valid', '[]', ?, 2, '[]', 'aia')",
        (vendor_id, str(cleaned), json.dumps({'columns_removed': ['Email'], 'columns_anonymized': []})),
    )
    db.execute(
        "INSERT INTO submissions (vendor_id, pavtech_batch_id, file_count, policy_count, "
        "pavtech_status, master_document_path, valuation_summary, reference) "
        "VALUES (?, 'batch123', 1, 2, 'complete', ?, ?, ?)",
        (vendor_id, f'/data/uploads/temp/{MASTER_SENTINEL}.xlsx', json.dumps(summary), REFERENCE),
    )
    db.commit()
    db.close()

    # /submit starts a background PavTECH thread: make it a no-op so the test
    # never makes a network call.
    mp.setattr(app_module, '_process_batch_with_pavtech', lambda *a, **k: None)

    app_module.app.config['TESTING'] = True
    yield app_module
    mp.undo()
    sys.modules.pop('app', None)


@pytest.fixture()
def client(st):
    return st.app.test_client()


def assert_no_valuation_data(body: str, where: str):
    low = body.lower()
    for name, number in SENTINEL_NUMBERS.items():
        for form in _number_forms(number):
            assert form.lower() not in low, f"{where}: leaked {name} as {form!r}"
    for needle in FORBIDDEN_STRINGS:
        assert needle not in low, f"{where}: leaked forbidden string {needle!r}"
    # 'multiple' is also a legitimate boolean attribute on <input type="file">,
    # so drop input tags before checking for the valuation multiple.
    without_inputs = re.sub(r'<input\b[^>]*>', '', low)
    assert 'multiple' not in without_inputs, f"{where}: leaked 'multiple'"


VENDOR_GET_ROUTES = [
    f'/{URL_CODE}',
    f'/{URL_CODE}/files',
    f'/{URL_CODE}/review',
    f'/{URL_CODE}/status',
    f'/{URL_CODE}/success',
]


@pytest.mark.parametrize('path', VENDOR_GET_ROUTES)
def test_vendor_get_routes_leak_no_valuation_data(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"
    assert_no_valuation_data(resp.get_data(as_text=True), f"GET {path}")


def test_vendor_error_page_leaks_nothing(client):
    resp = client.get('/NOSUCHCODE')
    assert resp.status_code == 404
    assert_no_valuation_data(resp.get_data(as_text=True), 'GET /NOSUCHCODE')


def test_status_returns_usable_safe_state(client):
    data = client.get(f'/{URL_CODE}/status').get_json()
    # Exact whitelist: a new key cannot appear without this test being updated.
    assert set(data.keys()) == {'state', 'file_count', 'latest_submission'}
    assert data['state'] == 'processed'
    assert data['file_count'] == 1
    latest = data['latest_submission']
    assert set(latest.keys()) == {'reference', 'submitted_at', 'file_count', 'state'}
    assert latest['reference'] == REFERENCE
    assert latest['state'] == 'processed'
    assert latest['submitted_at']


@pytest.mark.parametrize('raw,expected', [
    ('pending', 'pending'),
    ('processing', 'processing'),
    ('complete', 'processed'),
    ('error', 'needs_attention'),
    (None, 'pending'),
    ('some_future_internal_status', 'pending'),
])
def test_vendor_processing_state_mapping(st, raw, expected):
    assert st.vendor_processing_state(raw) == expected


def test_status_error_state_hides_pavtech_error_text(st, client):
    """A failed PavTECH run shows the vendor 'needs_attention', never the error."""
    db = sqlite3.connect(st.DATABASE)
    db.execute(
        "INSERT INTO vendors (url_code, vendor_name, dm_email, dm_name, status) "
        "VALUES ('SENTINEL2', 'Error Advisers', 'dm@example.invalid', 'Test DM', 'error')")
    vid = db.execute("SELECT id FROM vendors WHERE url_code = 'SENTINEL2'").fetchone()[0]
    db.execute(
        "INSERT INTO submissions (vendor_id, file_count, pavtech_status, validation_errors, reference) "
        "VALUES (?, 1, 'error', 'PAVTECH_ERROR_SENTINEL no HubSpot P1 deal', 'ST-20260917-EEEE')", (vid,))
    db.commit()
    db.close()

    for path in ('/SENTINEL2', '/SENTINEL2/status'):
        body = client.get(path).get_data(as_text=True)
        assert 'PAVTECH_ERROR_SENTINEL' not in body, f"{path} leaked PavTECH error text"
        assert_no_valuation_data(body, f"GET {path}")
    assert client.get('/SENTINEL2/status').get_json()['state'] == 'needs_attention'


def test_vendor_post_routes_leak_no_valuation_data(client):
    # Rejected upload (bad extension)
    resp = client.post(f'/{URL_CODE}/upload',
                       data={'file': (io.BytesIO(b'x'), 'notes.txt')},
                       content_type='multipart/form-data')
    assert_no_valuation_data(resp.get_data(as_text=True), 'POST /upload (rejected)')

    # Real spreadsheet upload
    buf = io.BytesIO()
    pd.DataFrame({
        'Policy Number': ['P1', 'P2'],
        'Insurer': ['AIA', 'AIA'],
        'Product Type': ['Life', 'TPD'],
        'Annual Premium': [1200, 800],
        'Status': ['In Force', 'In Force'],
    }).to_excel(buf, index=False)
    buf.seek(0)
    resp = client.post(f'/{URL_CODE}/upload',
                       data={'file': (buf, 'second_book.xlsx'), 'insurer': 'aia', 'action': 'add_new'},
                       content_type='multipart/form-data')
    assert_no_valuation_data(resp.get_data(as_text=True), 'POST /upload')

    for path in (f'/{URL_CODE}/submit', f'/{URL_CODE}/revaluate'):
        resp = client.post(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"
        assert_no_valuation_data(resp.get_data(as_text=True), f"POST {path}")

    # Page + status again while 'processing'
    for path in (f'/{URL_CODE}', f'/{URL_CODE}/status'):
        assert_no_valuation_data(client.get(path).get_data(as_text=True), f"GET {path} (processing)")
    assert client.get(f'/{URL_CODE}/status').get_json()['state'] == 'processing'


def test_local_estimate_is_gone():
    """The commission x 3.5 / premium x 0.35 guess must not come back."""
    src = (REPO_ROOT / 'excel_parser.py').read_text(encoding='utf-8')
    assert 'estimated_value' not in src
