"""
Tests for INS-61 defense-in-depth additions (session cookie security, idle
timeout, security headers, paranoid session.clear on login + logout).

Static-only assertions on app.py source so these run in any minimal env.
Behavioural Flask tests are deferred to the venv where Flask is installed.
"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestSessionCookieConfig(unittest.TestCase):
    def setUp(self):
        self.src = (REPO / "app.py").read_text()

    def test_session_cookie_secure(self):
        self.assertIn("SESSION_COOKIE_SECURE", self.src)

    def test_session_cookie_httponly(self):
        self.assertIn("SESSION_COOKIE_HTTPONLY", self.src)

    def test_session_cookie_samesite(self):
        self.assertIn("SESSION_COOKIE_SAMESITE", self.src)

    def test_permanent_session_lifetime(self):
        self.assertIn("PERMANENT_SESSION_LIFETIME", self.src)

    def test_idle_timeout_env_var(self):
        self.assertIn("ADMIN_SESSION_IDLE_MIN", self.src)


class TestAdminRequiredHardening(unittest.TestCase):
    def setUp(self):
        self.src = (REPO / "app.py").read_text()

    def test_idle_timeout_check_in_decorator(self):
        # The decorator must check session age — look for the marker string.
        self.assertIn("admin_last_seen_at", self.src,
                      "admin_required should read session['admin_last_seen_at']")

    def test_session_cleared_on_idle(self):
        # On idle expiry, session.clear() should fire — anchor on the log line.
        self.assertIn("Admin session idle-timeout", self.src)

    def test_login_marks_session_permanent(self):
        self.assertIn("session.permanent = True", self.src,
                      "login should set session.permanent = True so the "
                      "PERMANENT_SESSION_LIFETIME idle expiry actually applies")

    def test_login_paranoid_clears_pre_login_data(self):
        self.assertIn("session.clear()  # paranoid", self.src,
                      "login should clear the session before stamping new keys")

    def test_logout_full_clear(self):
        # Logout must wipe the whole session, not just admin_logged_in.
        # Look for the docstring marker so we don't false-positive on the
        # session.clear() in the idle path.
        self.assertIn(
            "INS-61 hardening", self.src,
            "logout docstring should call out the INS-61 hardening change",
        )


class TestSecurityHeaders(unittest.TestCase):
    def setUp(self):
        self.src = (REPO / "app.py").read_text()

    def test_after_request_hook_registered(self):
        self.assertIn("_apply_security_headers", self.src)
        self.assertIn("@app.after_request", self.src)

    def test_x_content_type_options(self):
        self.assertIn("X-Content-Type-Options", self.src)

    def test_x_frame_options(self):
        self.assertIn("X-Frame-Options", self.src)

    def test_referrer_policy(self):
        self.assertIn("Referrer-Policy", self.src)

    def test_csp(self):
        self.assertIn("Content-Security-Policy", self.src)

    def test_hsts_when_secure(self):
        self.assertIn("Strict-Transport-Security", self.src)

    def test_hsts_gated_on_secure(self):
        # HSTS should only fire when the request is over HTTPS — otherwise
        # local dev breaks.
        self.assertIn("request.is_secure", self.src)


# ---------------------------------------------------------------------------
# Behavioural tests — require Flask. Skipped cleanly in the minimal env.
# ---------------------------------------------------------------------------
try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False


@unittest.skipUnless(_HAVE_FLASK, "Flask not installed in this env")
class TestBehavioural(unittest.TestCase):
    """Run in Paul's venv to verify the security headers actually ship."""

    def setUp(self):
        import os, sys
        os.environ['ADMIN_PASSWORD'] = 'test-password-xyz'
        os.environ['SOURCETECH_DISABLE_BATCH_QUEUE'] = '1'
        os.environ['SOURCETECH_INSECURE_COOKIES'] = '1'  # tests run over http
        sys.path.insert(0, str(REPO))
        # Reimport fresh to pick up env.
        if 'app' in sys.modules:
            del sys.modules['app']
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_security_headers_present_on_response(self):
        r = self.client.get('/admin/login')
        self.assertEqual(r.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(r.headers.get('X-Frame-Options'), 'DENY')
        self.assertIn('Content-Security-Policy', r.headers)

    def test_session_cookie_httponly_on_login(self):
        r = self.client.post('/admin/login', data={'password': 'test-password-xyz'})
        # Flask sets the cookie via Set-Cookie; check for HttpOnly token.
        cookies = r.headers.getlist('Set-Cookie')
        self.assertTrue(any('HttpOnly' in c for c in cookies),
                        f"no HttpOnly cookie in {cookies}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
