"""
Tests for admin login throttle (INS-61).

- Static: ADMIN_PASSWORD has no insecure default; throttle helpers present.
- Behavioural (requires Flask): N failed logins from one IP -> 429 lockout;
  successful login clears the failure counter.
"""
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestStaticConfig(unittest.TestCase):
    def test_no_changeme_default(self):
        src = (REPO / "app.py").read_text()
        self.assertNotIn(
            "ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme')",
            src,
            "the 'changeme' default is still present",
        )

    def test_constant_time_compare(self):
        src = (REPO / "app.py").read_text()
        self.assertIn("compare_digest", src,
                      "admin_login should use hmac.compare_digest")

    def test_throttle_helpers_present(self):
        src = (REPO / "app.py").read_text()
        for sym in [
            "_ADMIN_LOGIN_MAX_FAILS",
            "_ADMIN_LOGIN_LOCKOUT_MIN",
            "_admin_is_locked_out",
            "_admin_record_failure",
            "_admin_clear_failures",
        ]:
            self.assertIn(sym, src, f"throttle helper {sym!r} missing")


try:
    os.environ['ADMIN_PASSWORD'] = 'test-strong-password-for-throttle-tests'
    # Use a tiny lockout so the test doesn't have to actually wait 15 min.
    os.environ['ADMIN_LOGIN_MAX_FAILS'] = '3'
    os.environ['ADMIN_LOGIN_LOCKOUT_MIN'] = '15'
    os.environ['ADMIN_LOGIN_WINDOW_MIN'] = '15'
    import sys
    sys.path.insert(0, str(REPO))
    import app as sourcetech_app  # noqa: E402
    FLASK_OK = True
except Exception as exc:  # noqa: BLE001
    FLASK_OK = False


@unittest.skipUnless(FLASK_OK, "Flask not installed in this environment")
class TestBehavioural(unittest.TestCase):
    def setUp(self):
        sourcetech_app.app.config['TESTING'] = True
        # Wipe the in-memory failure dict between tests so tests are independent.
        sourcetech_app._admin_login_failures.clear()
        self.client = sourcetech_app.app.test_client()

    def _login(self, pw):
        return self.client.post('/admin/login', data={'password': pw})

    def test_three_failures_locks_out(self):
        for _ in range(3):
            resp = self._login('wrong')
            self.assertIn(resp.status_code, (200, 429))
        # 4th attempt should be 429 (locked out) even with correct password.
        resp = self._login('test-strong-password-for-throttle-tests')
        self.assertEqual(resp.status_code, 429,
                         f"expected 429 lockout, got {resp.status_code}")

    def test_success_clears_failures(self):
        self._login('wrong')
        self._login('wrong')
        # Correct password while under the threshold -> success, counter reset.
        resp = self._login('test-strong-password-for-throttle-tests')
        # Successful admin login redirects to dashboard (302).
        self.assertEqual(resp.status_code, 302)
        # Now hitting wrong 2 more times shouldn't lock us out (since counter reset).
        self._login('wrong')
        self._login('wrong')
        resp = self._login('test-strong-password-for-throttle-tests')
        self.assertEqual(resp.status_code, 302,
                         "successful login should have reset the failure counter")


if __name__ == "__main__":
    unittest.main()
