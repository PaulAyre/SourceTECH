"""3.0.3: each file's vendor-picked insurer reaches PavTECH as a confirmed pick."""
from pathlib import Path
from unittest import mock

import pavtech_client


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def test_picks_follow_files_and_splits(tmp_path):
    paths = []
    for name in ('tal_1.xlsx', 'aia_1.xlsx', 'other_1.xlsx', 'tal_2.xlsx'):
        p = tmp_path / name
        p.write_bytes(b'x')
        paths.append(p)
    upload_body = {'batch_id': 'B1', 'file_ids': ['file_0', 'file_1', 'file_2', 'file_3', 'file_4'], 'files': [
        {'file_id': 'file_0', 'filename': 'tal_1.xlsx', 'sheet_info': {'is_split': False}},
        {'file_id': 'file_1', 'filename': 'aia_1.xlsx', 'sheet_info': {'is_split': False}},
        {'file_id': 'file_2', 'filename': 'other_1.xlsx', 'sheet_info': {'is_split': False}},
        # a workbook split by sheet keeps the vendor's pick ...
        {'file_id': 'file_3', 'filename': 'tal_2_Sheet1.xlsx', 'sheet_info': {'is_split': True, 'original_file': 'tal_2.xlsx'}},
        # ... a part split by a Provider column already names its insurer
        {'file_id': 'file_4', 'filename': 'source_AIA_from_tal_2.xlsx',
         'sheet_info': {'is_split': True, 'original_file': 'tal_2.xlsx', 'insurer_row_split': 'AIA'}},
    ]}
    sent = []

    def fake_post(url, **kw):
        if url.endswith('/api/batch/upload'):
            return _Resp(200, upload_body)
        sent.append(kw['json'])
        return _Resp(200, {'success': True})

    client = pavtech_client.PavTechClient('http://pavtech.invalid', temp_dir=str(tmp_path / 't'))
    with mock.patch.object(pavtech_client.requests, 'post', side_effect=fake_post), \
         mock.patch.object(client, '_poll_until_complete', return_value={'all_complete': False}):
        client.process_batch(paths, 'Vendor', insurer_by_filename={
            'tal_1.xlsx': 'TAL', 'aia_1.xlsx': 'AIA', 'tal_2.xlsx': 'TAL'})

    got = {j['file_id']: (j['company_name'], j['insurer_confirmed']) for j in sent}
    assert got == {
        'file_0': ('TAL', True), 'file_1': ('AIA', True), 'file_2': ('', False),
        'file_3': ('TAL', True), 'file_4': ('', False),
    }


def test_every_tile_maps_to_a_pavtech_insurer():
    app_src = Path(__file__).resolve().parents[1].joinpath('app.py').read_text()
    assert 'PAVTECH_INSURER_BY_KEY' in app_src
    ns = {}
    start = app_src.index('PAVTECH_INSURER_BY_KEY = {')
    exec(app_src[start:app_src.index('}', start) + 1], ns)
    keys = set(ns['PAVTECH_INSURER_BY_KEY'])
    tile_keys = {line.split('"key": "')[1].split('"')[0] for line in app_src.splitlines() if '"key": "' in line}
    assert tile_keys == keys
