"""
Tests for the INS-62 follow-up DB-backed DM contact override layer.

The DB lookup takes precedence over env vars + built-ins. Misses fall
through to the env+built-in chain. Crashes in the lookup must NOT take
down the email pipeline.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _reload_email_service(env_overrides=None):
    """Reimport email_service with optional env tweaks so each test is clean."""
    import os
    if env_overrides:
        for k, v in env_overrides.items():
            os.environ[k] = v
    if 'email_service' in sys.modules:
        del sys.modules['email_service']
    import email_service  # noqa: WPS433
    return email_service


class TestDbLayerOverridesEnv(unittest.TestCase):
    def setUp(self):
        import os
        os.environ.pop('DM_PAUL_EMAIL', None)
        os.environ.pop('DM_CONTACTS_JSON', None)
        self.email_service = _reload_email_service()
        # Reset any previously-registered DB lookup.
        self.email_service._dm_db_lookup = None

    def tearDown(self):
        self.email_service._dm_db_lookup = None

    def test_db_lookup_overrides_builtin(self):
        def db(dm_key):
            if dm_key == 'paul':
                return {'name': 'Paul From DB', 'email': 'paul-from-db@example.com'}
            return None

        self.email_service.register_dm_db_lookup(db)
        c = self.email_service.get_dm_contact('paul')
        self.assertEqual(c['email'], 'paul-from-db@example.com')
        self.assertEqual(c['name'], 'Paul From DB')

    def test_db_lookup_miss_falls_through_to_env(self):
        import os
        os.environ['DM_PAUL_EMAIL'] = 'paul-from-env@example.com'
        # Reload to pick up the env.
        es = _reload_email_service()
        es._dm_db_lookup = None  # fresh

        def db(dm_key):
            return None  # always miss

        es.register_dm_db_lookup(db)
        c = es.get_dm_contact('paul')
        self.assertEqual(c['email'], 'paul-from-env@example.com')

    def test_db_lookup_exception_falls_through_gracefully(self):
        def db(dm_key):
            raise RuntimeError("simulated DB outage")

        self.email_service.register_dm_db_lookup(db)
        # Should NOT raise — must return the env/built-in default.
        c = self.email_service.get_dm_contact('paul')
        self.assertIn('email', c)

    def test_db_lookup_missing_email_field_falls_through(self):
        """A row missing 'email' should be treated as a miss, not used."""
        def db(dm_key):
            return {'name': 'Half Row'}  # no email key

        self.email_service.register_dm_db_lookup(db)
        c = self.email_service.get_dm_contact('paul')
        self.assertNotEqual(c.get('name'), 'Half Row')

    def test_db_lookup_typo_falls_through_to_typo_tolerant(self):
        """If DB doesn't have the key, typo-tolerant env layer still kicks in."""
        def db(dm_key):
            return None

        self.email_service.register_dm_db_lookup(db)
        c = self.email_service.get_dm_contact('Mik')  # typo of 'mike'
        # Built-in 'mike' is in DM_CONTACTS — typo-tolerant should hit it.
        self.assertIn('mike', c['email'])


class TestRegisterIsIdempotent(unittest.TestCase):
    def test_register_overwrites(self):
        es = _reload_email_service()
        es.register_dm_db_lookup(lambda k: None)
        first = es._dm_db_lookup
        es.register_dm_db_lookup(lambda k: {'name': 'X', 'email': 'x@y'})
        second = es._dm_db_lookup
        self.assertIsNot(first, second)


class TestSchemaWiring(unittest.TestCase):
    """app.py should declare the dm_contacts table + register the lookup."""
    def test_app_creates_dm_contacts_table(self):
        src = (REPO / "app.py").read_text()
        self.assertIn("CREATE TABLE IF NOT EXISTS dm_contacts", src)
        self.assertIn("dm_key TEXT PRIMARY KEY", src)

    def test_app_registers_db_lookup(self):
        src = (REPO / "app.py").read_text()
        self.assertIn("register_dm_db_lookup(_dm_db_lookup)", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
