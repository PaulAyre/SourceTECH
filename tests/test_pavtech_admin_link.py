"""3.6.0 (29 Sep 2026): the HubSpot 'PavTECH Admin' link and the embedded documents panel."""
from unittest import mock

from test_v3_clean_upload import ctx  # noqa: F401  (fixture)


def _login(c):
    with c.session_transaction() as s:
        s['admin_logged_in'] = True


def test_deal_link_opens_the_deals_page(ctx):
    st, c, d, calls = ctx
    _login(c)
    r = c.get('/admin/deal/287657057728')
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/vendors/V3TEST01')


def test_deal_without_a_link_gets_one_then_opens(ctx):
    st, c, d, calls = ctx
    _login(c)
    with mock.patch('dealtech_client.create_link',
                    return_value={'deal_id': '555', 'sourcetech_url': 'https://sourcetech.onrender.com/NEWCODE1'}) as m:
        r = c.get('/admin/deal/555')
    m.assert_called_once_with('555')
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/vendors/NEWCODE1')


def test_deal_link_failure_is_said_not_swallowed(ctx):
    st, c, d, calls = ctx
    _login(c)
    with mock.patch('dealtech_client.create_link', return_value={'error': 'DealTECH 502: HubSpot down'}):
        r = c.get('/admin/deal/556')
    assert r.status_code == 502 and b'HubSpot down' in r.data


def test_non_numeric_deal_id_is_refused(ctx):
    st, c, d, calls = ctx
    _login(c)
    assert c.get('/admin/deal/abc').status_code == 404


def test_login_returns_to_the_deal(ctx):
    st, c, d, calls = ctx
    r = c.get('/admin/deal/287657057728')
    assert r.status_code == 302 and '/admin/login' in r.headers['Location']
    import app as sourcetech
    r = c.post('/admin/login', data={'password': sourcetech.ADMIN_PASSWORD})
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/deal/287657057728')


def test_vendor_page_has_the_documents_panel(ctx):
    st, c, d, calls = ctx
    _login(c)
    html = c.get('/admin/vendors/V3TEST01').get_data(as_text=True)
    assert 'id="docs-frame"' in html and "'&embed=documents'" in html
