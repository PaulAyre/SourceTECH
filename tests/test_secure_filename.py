"""
Tests for the secure_filename wrapping introduced in INS-56.

Validates that we never let a raw uploaded filename hit the filesystem.
Tests are written against werkzeug.utils.secure_filename directly because
exercising the Flask route requires a full test client + pandas (heavy).
The path-traversal protection lives in the secure_filename call — once we
know it's wired into handle_upload (see grep test at the bottom of this
file), the unit tests on secure_filename give us the full guarantee.
"""
import unittest
from pathlib import Path

try:
    from werkzeug.utils import secure_filename
    WERKZEUG_OK = True
except ImportError:
    WERKZEUG_OK = False


@unittest.skipUnless(WERKZEUG_OK, "werkzeug not installed in this environment")
class TestSecureFilename(unittest.TestCase):
    """Verify secure_filename strips the classes of input we care about."""

    def test_strips_path_traversal_dotdot(self):
        # Classic ../etc/passwd should collapse to a flat name with no slashes.
        result = secure_filename("../../etc/passwd")
        self.assertNotIn("..", result)
        self.assertNotIn("/", result)
        self.assertNotIn("\\", result)

    def test_strips_path_traversal_with_dotdot_in_middle(self):
        result = secure_filename("portfolio/../../../etc/passwd.xlsx")
        self.assertNotIn("..", result)
        self.assertNotIn("/", result)
        # Should still end in xlsx because secure_filename preserves the
        # trailing extension on a token boundary.
        self.assertTrue(result.endswith(".xlsx"))

    def test_strips_backslash_windows_traversal(self):
        result = secure_filename(r"..\..\windows\system32\config.xlsx")
        self.assertNotIn("\\", result)
        self.assertNotIn("..", result)

    def test_strips_null_byte(self):
        result = secure_filename("portfolio.xlsx\x00.txt")
        self.assertNotIn("\x00", result)

    def test_strips_shell_metachars(self):
        for evil in ["a;rm -rf /;.xlsx", "$(whoami).xlsx", "`id`.xlsx",
                     "foo|bar.xlsx", "a&b.xlsx"]:
            with self.subTest(evil=evil):
                result = secure_filename(evil)
                for ch in [";", "|", "&", "$", "`", "(", ")"]:
                    self.assertNotIn(ch, result,
                                     f"char {ch!r} survived secure_filename of {evil!r}")

    def test_preserves_normal_filename(self):
        # Sanity: a normal vendor upload comes through readable.
        result = secure_filename("CommInsure FY24 Portfolio v2.xlsx")
        self.assertTrue(result.endswith(".xlsx"))
        self.assertIn("Portfolio", result)

    def test_empty_input_returns_empty_string(self):
        # secure_filename returns "" for inputs that collapse to nothing — our
        # production code falls back to "upload<ext>" in that case.
        self.assertEqual(secure_filename("../../"), "")
        self.assertEqual(secure_filename(".."), "")


class TestWiredIntoUploadHandler(unittest.TestCase):
    """Static check: confirm handle_upload actually calls secure_filename."""

    def test_app_py_uses_secure_filename(self):
        app_py = (Path(__file__).resolve().parent.parent / "app.py").read_text()
        self.assertIn("from werkzeug.utils import secure_filename", app_py,
                      "secure_filename import missing from app.py")
        self.assertIn("secure_filename(file.filename)", app_py,
                      "secure_filename not invoked on the uploaded filename")
        # And the unsanitised concatenation we replaced must be gone.
        self.assertNotIn('safe_filename = f"{timestamp}_{file.filename}"', app_py,
                         "unsafe filename concatenation still present in app.py")


if __name__ == "__main__":
    unittest.main()
