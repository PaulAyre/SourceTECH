"""3.1.0: the admin's live board (presence, insurer slots, recent deals) and the vendor-side presence ping."""
import json
from unittest import mock

from test_v3_clean_upload import ctx, post_clean  # noqa: F401  (fixture + helper)


def _login(c):
    with c.session_transaction() as s:
        s['admin_logged_in'] = True


def test_presence_keeps_stage_and_progress_but_never_names(ctx):
    st, c, d, calls = ctx
    r = c.post('/V3TEST01/presence', json={'step': 1, 'selected': ['aia', 'tal', 'bogus'],
                                          'items': [{'id': 'c1', 'insurer': 'tal', 'state': 'uploading', 'fraction': 0.42, 'name': 'Smith J policies.xlsx'},
                                                    {'id': 'c2', 'insurer': 'nope', 'state': 'uploading'}]})
    assert r.status_code == 204
    db = st.get_db()
    stored = json.loads(db.execute("SELECT payload FROM vendor_presence").fetchone()[0])
    db.close()
    assert stored['selected'] == ['aia', 'tal'] and stored['step'] == 1
    assert stored['items'] == [{'id': 'c1', 'insurer': 'tal', 'state': 'uploading', 'fraction': 0.42}]
    assert 'Smith' not in json.dumps(stored), 'no file name ever stored'


def test_board_fills_slots_live(ctx):
    st, c, d, calls = ctx
    _login(c)
    db = st.get_db()
    db.execute("UPDATE vendors SET selected_insurers = ? WHERE url_code = 'V3TEST01'", (json.dumps(['aia', 'tal', 'zurich']),))
    db.commit(); db.close()
    post_clean(c, 'aia_1.xlsx', client_id='cid-000001')
    c.post('/V3TEST01/presence', json={'step': 1, 'selected': ['aia', 'tal', 'zurich'],
                                      'items': [{'id': 'cid-000002', 'insurer': 'tal', 'state': 'stripping', 'fraction': 0.3}]})
    b = c.get('/admin/vendors/V3TEST01/live.json').get_json()
    slots = {s['key']: s for s in b['slots']}
    assert b['presence']['state'] == 'here' and b['presence']['step'] == 1
    assert slots['aia']['state'] == 'received' and slots['aia']['policies'] > 0
    assert slots['tal']['state'] == 'preparing' and slots['tal']['progress'] == 0.3
    assert slots['zurich']['state'] == 'ticked'
    assert b['totals']['expected'] == 3 and b['totals']['expected_received'] == 1
    assert b['vendor']['deal_url'].endswith('/287657057728')
    # the vendor leaves: in-flight work stops showing, what arrived stays
    c.post('/V3TEST01/presence', json={'left': True, 'items': []})
    b = c.get('/admin/vendors/V3TEST01/live.json').get_json()
    slots = {s['key']: s for s in b['slots']}
    assert b['presence']['state'] == 'away' and slots['tal']['state'] == 'waiting' and slots['aia']['state'] == 'received'


def test_main_page_lists_recent_deals_with_links(ctx):
    st, c, d, calls = ctx
    _login(c)
    deals = {'deals': [
        {'deal_id': '287657057728', 'name': 'Pytest Wealth', 'entity': '', 'stage': 'Valuation', 'owner': 'Dee Em',
         'sourcetech_url': 'https://sourcetech.onrender.com/V3TEST01', 'last_activity': '2026-09-27T10:00:00Z'},
        {'deal_id': '999', 'name': 'Brand New Advice', 'entity': '', 'stage': 'Positive Intent', 'owner': 'Tom',
         'sourcetech_url': '', 'last_activity': '2026-09-27T09:00:00Z'},
    ]}
    with mock.patch('dealtech_client.recent_deals', return_value=deals):
        html = c.get('/admin').get_data(as_text=True)
    assert 'Most recently active deals' in html
    assert 'Pytest Wealth' in html and 'https://sourcetech.onrender.com/V3TEST01' in html
    assert "location.href='/admin/vendors/V3TEST01'" in html, 'the whole row opens the live board'
    assert 'Brand New Advice' in html and '/admin/deals/999/create-link' in html, 'a deal with no link can get one'


def test_main_page_falls_back_to_vendors_when_dealtech_is_down(ctx):
    st, c, d, calls = ctx
    _login(c)
    with mock.patch('dealtech_client.recent_deals', return_value={'error': 'DealTECH unreachable'}):
        html = c.get('/admin/vendors').get_data(as_text=True)
    assert 'Could not load the recent HubSpot deals' in html and 'Pytest Wealth' in html


def test_vendor_page_renders(ctx):
    st, c, d, calls = ctx
    _login(c)
    html = c.get('/admin/vendors/V3TEST01').get_data(as_text=True)
    assert 'id="slots"' in html and '/admin/vendors/V3TEST01/live.json' in html and 'insuranceplus_wordmark_white.svg' in html
