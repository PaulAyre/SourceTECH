"""3.7.0 (Tom, 1 Oct 2026): PPS Mutual slot, NEOS without NobleOak, real marks on the vendor page."""
import os

os.environ.setdefault('ADMIN_PASSWORD', 'test')
import app as st  # noqa: E402


def test_pps_slot_reaches_pavtech_as_pps_mutual():
    keys = [i['key'] for i in st.INSURERS]
    assert 'pps' in keys
    assert st.PAVTECH_INSURER_BY_KEY['pps'] == 'PPS Mutual'
    assert 'pps' in st.INSURER_LOGOS
    assert os.path.isfile(os.path.join(st.app.root_path, 'static', 'images', 'insurers', 'pps.png'))


def test_neos_slot_has_no_nobleoak_and_keeps_its_key():
    neos = next(i for i in st.INSURERS if i['key'] == 'neos')
    blob = ' '.join(str(v) for v in neos.values()).lower()
    assert 'nobleoak' not in blob and 'noble oak' not in blob
    assert neos['name'] == 'NEOS'


def test_every_logo_key_has_a_file():
    for k in st.INSURER_LOGOS:
        assert os.path.isfile(os.path.join(st.app.root_path, 'static', 'images', 'insurers', f'{k}.png')), k
