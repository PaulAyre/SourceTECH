import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_run_url_is_the_pavtech_hash_deep_link():
    import app
    u = app.pavtech_run_url("ZZ TEST - PavTECH", "26Sep26_0934", base="https://portfolioapp-d6aq.onrender.com/")
    assert u == "https://portfolioapp-d6aq.onrender.com/#vendor=ZZ%20TEST%20-%20PavTECH&batch=26Sep26_0934"
    assert app.pavtech_run_url("v", None, base="https://x") == ""
