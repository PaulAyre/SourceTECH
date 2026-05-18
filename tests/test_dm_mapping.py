"""
Tests for the externalised DM mapping (INS-62).

Verifies:
  - impaulayre@gmail.com no longer hard-coded.
  - DM_CONTACTS_JSON env var overrides built-ins.
  - Per-DM DM_<KEY>_EMAIL / DM_<KEY>_NAME env vars override JSON.
  - Typo-tolerant lookup catches paull/tomas/mik etc.
  - Empty key / unknown key falls back to 'paul' with a WARN log.
"""
import importlib
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _reload_email_service():
    """Re-import email_service so it picks up new env vars."""
    # Clear it so the module-level _load_dm_contacts re-runs.
    for mod in list(sys.modules):
        if mod == 'email_service':
            del sys.modules[mod]
    import email_service  # noqa: F401
    return sys.modules['email_service']


class TestStaticConfig(unittest.TestCase):
    def test_no_impaulayre_gmail_in_code(self):
        src = (REPO / "email_service.py").read_text()
        # Only allowed in the explanatory comment.
        # Count occurrences in non-comment lines.
        leaks = []
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith('#'):
                continue
            if 'impaulayre@gmail.com' in line:
                leaks.append((i, line))
        self.assertEqual(leaks, [],
                         f"impaulayre@gmail.com still present in code (non-comment): {leaks}")


class TestEnvLoading(unittest.TestCase):
    def setUp(self):
        # Wipe DM_* env vars between tests for isolation.
        for k in list(os.environ):
            if k.startswith('DM_'):
                del os.environ[k]

    def test_builtin_defaults(self):
        es = _reload_email_service()
        self.assertIn('paul', es.DM_CONTACTS)
        self.assertEqual(es.DM_CONTACTS['paul']['email'],
                         'paul@insuranceplus.com.au')

    def test_json_env_overrides_builtin(self):
        os.environ['DM_CONTACTS_JSON'] = (
            '{"paul": {"name": "Paul A.", "email": "paul.a@iplus.test"}, '
            '"jane": {"name": "Jane Doe", "email": "jane@iplus.test"}}'
        )
        es = _reload_email_service()
        self.assertEqual(es.DM_CONTACTS['paul']['email'], 'paul.a@iplus.test')
        self.assertEqual(es.DM_CONTACTS['jane']['email'], 'jane@iplus.test')

    def test_per_dm_env_overrides_json(self):
        os.environ['DM_CONTACTS_JSON'] = '{"paul": {"name": "Paul J", "email": "json@test"}}'
        os.environ['DM_PAUL_EMAIL'] = 'env@test'
        os.environ['DM_PAUL_NAME'] = 'Paul Env'
        es = _reload_email_service()
        self.assertEqual(es.DM_CONTACTS['paul']['email'], 'env@test')
        self.assertEqual(es.DM_CONTACTS['paul']['name'], 'Paul Env')

    def test_malformed_json_falls_back_to_builtins(self):
        os.environ['DM_CONTACTS_JSON'] = 'not-valid-json{'
        es = _reload_email_service()
        # No crash, paul still present.
        self.assertEqual(es.DM_CONTACTS['paul']['email'],
                         'paul@insuranceplus.com.au')


class TestTypoTolerantLookup(unittest.TestCase):
    def setUp(self):
        for k in list(os.environ):
            if k.startswith('DM_'):
                del os.environ[k]
        self.es = _reload_email_service()

    def test_exact_match(self):
        self.assertEqual(self.es.get_dm_contact('paul')['email'],
                         'paul@insuranceplus.com.au')

    def test_case_insensitive(self):
        self.assertEqual(self.es.get_dm_contact('PAUL')['email'],
                         'paul@insuranceplus.com.au')
        self.assertEqual(self.es.get_dm_contact('Paul')['email'],
                         'paul@insuranceplus.com.au')

    def test_whitespace_and_punctuation_stripped(self):
        self.assertEqual(self.es.get_dm_contact(' paul ')['email'],
                         'paul@insuranceplus.com.au')
        self.assertEqual(self.es.get_dm_contact('paul.')['email'],
                         'paul@insuranceplus.com.au')

    def test_single_typo(self):
        # "paull" -> paul
        self.assertEqual(self.es.get_dm_contact('paull')['email'],
                         'paul@insuranceplus.com.au')
        # "tomas" -> thomas
        self.assertEqual(self.es.get_dm_contact('tomas')['email'],
                         'thomas@insuranceplus.com.au')

    def test_unknown_key_falls_back_to_paul(self):
        result = self.es.get_dm_contact('completely-unknown-name-xyz')
        self.assertEqual(result['email'], 'paul@insuranceplus.com.au')

    def test_empty_key_falls_back_to_paul(self):
        result = self.es.get_dm_contact('')
        self.assertEqual(result['email'], 'paul@insuranceplus.com.au')


if __name__ == "__main__":
    unittest.main(verbosity=2)
