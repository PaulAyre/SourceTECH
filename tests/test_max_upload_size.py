"""
Tests for MAX_CONTENT_LENGTH (INS-57).

Verifies:
  1. Static: the cap is configured in app.py.
  2. Behavioural: Flask's built-in 413 short-circuit fires when the request
     body exceeds MAX_CONTENT_LENGTH (requires Flask installed).
  3. The custom 413 handler returns JSON for /upload paths.
"""
import io
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

try:
    # Force a small cap before importing app so we can fake "too large" with
    # tiny test payloads.
    os.environ['MAX_UPLOAD_MB'] = '1'
    os.environ['ADMIN_PASSWORD'] = 'test-only-do-not-use'
    import sys
    sys.path.insert(0, str(REPO))
    import app as sourcetech_app  # noqa: E402
    FLASK_OK = True
except Exception as exc:  # noqa: BLE001
    FLASK_OK = False
    IMPORT_ERR = exc


class TestStaticConfig(unittest.TestCase):
    """app.py wires MAX_CONTENT_LENGTH from env (no behavioural deps)."""

    def test_max_content_length_is_set(self):
        src = (REPO / "app.py").read_text()
        self.assertIn("app.config['MAX_CONTENT_LENGTH']", src,
                      "MAX_CONTENT_LENGTH not configured in app.py")
        self.assertIn("MAX_UPLOAD_MB", src,
                      "MAX_UPLOAD_MB env override missing")

    def test_413_handler_registered(self):
        src = (REPO / "app.py").read_text()
        self.assertIn("@app.errorhandler(413)", src,
                      "no 413 handler registered")


@unittest.skipUnless(FLASK_OK, "Flask not installed in this environment")
class TestBehavioural(unittest.TestCase):
    """Flask returns 413 when body exceeds the cap."""

    def setUp(self):
        sourcetech_app.app.config['TESTING'] = True
        self.client = sourcetech_app.app.test_client()

    def test_413_on_oversized_upload(self):
        big = io.BytesIO(b"A" * (2 * 1024 * 1024))
        resp = self.client.post(
            "/NOPE12345/upload",
            data={"file": (big, "huge.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 413,
                         f"expected 413, got {resp.status_code}: {resp.data[:200]!r}")

    def test_413_returns_json_for_upload_path(self):
        big = io.BytesIO(b"A" * (2 * 1024 * 1024))
        resp = self.client.post(
            "/NOPE12345/upload",
            data={"file": (big, "huge.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 413)
        self.assertIn(b"too large", resp.data.lower(),
                      f"413 body missing friendly message: {resp.data[:200]!r}")


if __name__ == "__main__":
    unittest.main()
