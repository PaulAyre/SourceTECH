"""
Tests for pii_stripper (INS-59).

Covers:
  1. Classification helpers (should_remove_column / should_anonymize_column)
     — pure-stdlib, always runs.
  2. End-to-end strip_pii() on golden CSVs — requires pandas. Tests cover:
       * email/phone/address columns are REMOVED.
       * client/insured/policyholder columns are ANONYMIZED to CLIENT_NNNNN.
       * DOB / premium / commission / status / occupation are KEPT (must not
         leak past the keep-list).
       * Inline emails + AU phone numbers in free-text columns are REDACTED.
       * Reports surface the right column lists.

The "no PII reaches PavTECH" guarantee is enforced by these tests: if a
new column pattern is added that should be removed/anon, add a test here
first.
"""
import os
import sys
import csv
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from pii_stripper import should_remove_column, should_anonymize_column, strip_pii
    import pandas  # noqa: F401
    PII_MODULE_OK = True
    PANDAS_OK = True
except ImportError:
    PII_MODULE_OK = False
    PANDAS_OK = False
    # Re-attempt pure classification import in case pandas is the only missing dep
    # (it isn't, currently — pii_stripper imports pandas at module load — but if
    # that ever changes the classification tests will start running again).
    try:
        from pii_stripper import should_remove_column, should_anonymize_column  # noqa: F401
        PII_MODULE_OK = True
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# 1. Classification (pure-stdlib)
# ---------------------------------------------------------------------------
@unittest.skipUnless(PII_MODULE_OK, 'pii_stripper (and its pandas dep) not importable')
class TestClassification(unittest.TestCase):
    """should_remove_column / should_anonymize_column logic."""

    REMOVE_CASES = [
        "Email", "email", "Email Address", "E-Mail", "e mail",
        "Phone", "Mobile", "Telephone", "Tel", "Fax", "Contact Number",
        "Address", "Street", "Suburb", "City", "Postcode", "Post Code", "Zip", "State",
        "TFN", "Tax File Number", "ABN", "ACN",
        "Medicare", "Health Fund", "Member Number", "Membership ID",
        "Bank", "BSB", "Account Number", "Account No",
        "Driver Licence", "Passport No", "Licence Number",
    ]

    ANONYMIZE_CASES = [
        "Name", "Client Name", "Insured", "Policy Holder", "PolicyHolder",
        "First Name", "Last Name", "Surname", "Given Name", "Middle Name",
        "Contact", "Owner", "Member Name", "Applicant",
    ]

    KEEP_CASES = [
        "DOB", "Date of Birth", "Age",
        "Policy Number", "Premium", "Sum Insured", "Benefit Type", "Commission",
        "Product", "Cover", "Status", "In Force", "Insurer",
        "Frequency", "Premium Amount", "Rate", "Gender", "Sex", "Smoker",
        "Occupation", "Occupation Class", "Waiting Period", "Term", "Loading",
        "Commencement Date", "Inception", "Start Date", "Effective Date",
        "Expiry Date", "Renewal Date", "Anniversary",
    ]

    def test_pii_columns_marked_remove(self):
        for col in self.REMOVE_CASES:
            with self.subTest(col=col):
                self.assertTrue(should_remove_column(col),
                                f"{col!r} should be REMOVED")
                # And it must NOT also be flagged for anonymise (anonymise has
                # its own keep-list short-circuit, but PII pattern overlap is
                # confusing — these are different categories).
                # (We don't assert the other way because some PII patterns also
                # match name patterns, e.g. "Contact" — should_anonymize_column
                # is checked second in app code so PII removal wins.)

    def test_name_columns_marked_anonymize(self):
        for col in self.ANONYMIZE_CASES:
            with self.subTest(col=col):
                self.assertTrue(should_anonymize_column(col),
                                f"{col!r} should be ANONYMIZED")
                self.assertFalse(should_remove_column(col),
                                 f"{col!r} should NOT be removed (anonymize only)")

    def test_business_columns_kept(self):
        for col in self.KEEP_CASES:
            with self.subTest(col=col):
                self.assertFalse(should_remove_column(col),
                                 f"{col!r} is business-critical and must NOT be removed")
                self.assertFalse(should_anonymize_column(col),
                                 f"{col!r} is business-critical and must NOT be anonymized")

    def test_keep_list_overrides_pii(self):
        # "Member Name" contains "name" (anonymize) but should still be
        # anonymized (not kept) because "member" alone is not in the keep-list.
        # "Member Number" has both "member" and "number" — neither in keep-list
        # → remove wins via "member number" PII pattern.
        self.assertTrue(should_remove_column("Member Number"))
        self.assertTrue(should_anonymize_column("Member Name"))


# ---------------------------------------------------------------------------
# 2. End-to-end strip_pii on golden CSVs
# ---------------------------------------------------------------------------
GOLDEN_DIR = REPO / "tests" / "golden"
GOLDEN_DIR.mkdir(parents=True, exist_ok=True)


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(r)


def _make_golden_full_portfolio():
    """A representative vendor file with PII + business cols + inline PII."""
    path = GOLDEN_DIR / "golden_full_portfolio.csv"
    _write_csv(path, [
        # Header
        ["Policy Number", "Client Name", "Email", "Mobile", "Address",
         "DOB", "Premium", "Commission", "Status", "Notes"],
        ["POL001", "Jane Smith", "jane@example.com", "0412 345 678",
         "12 Pitt St Sydney NSW 2000", "1965-03-12", "1200.00", "120.00",
         "In Force", "Renewed 2024. Contact 0411 222 333 or john@acme.com."],
        ["POL002", "Bob Jones", "bob@example.com", "0422 456 789",
         "5 Bourke St Melbourne VIC 3000", "1970-07-21", "950.00", "95.00",
         "Lapsed", "Notes only no PII"],
        ["POL003", "Alice Lee", "alice@example.com", "0433 567 890",
         "8 Queen St Brisbane QLD 4000", "1980-11-02", "2100.00", "210.00",
         "In Force", "Call +61 412 999 888 for details"],
    ])
    return path


@unittest.skipUnless(PANDAS_OK, "pandas not installed in this environment")
class TestStripPIIGolden(unittest.TestCase):
    """End-to-end golden CSV runs."""

    @classmethod
    def setUpClass(cls):
        cls.path = _make_golden_full_portfolio()

    def test_email_phone_address_removed(self):
        df, report = strip_pii(self.path)
        for col in ["Email", "Mobile", "Address"]:
            self.assertNotIn(col, df.columns,
                             f"{col!r} should have been removed")
            self.assertIn(col, report["columns_removed"],
                          f"{col!r} missing from report.columns_removed")

    def test_client_name_anonymized_kept_as_column(self):
        df, report = strip_pii(self.path)
        self.assertIn("Client Name", df.columns,
                      "Client Name column should be present (anonymized, not removed)")
        self.assertIn("Client Name", report["columns_anonymized"])
        # No real names should survive.
        for original in ["Jane Smith", "Bob Jones", "Alice Lee"]:
            self.assertFalse((df["Client Name"] == original).any(),
                             f"{original!r} leaked through anonymisation")
        # Should look like CLIENT_NNNNN.
        for val in df["Client Name"]:
            self.assertRegex(val, r"^CLIENT_\d{5}$")

    def test_business_columns_preserved(self):
        df, _ = strip_pii(self.path)
        for col in ["Policy Number", "DOB", "Premium", "Commission", "Status"]:
            self.assertIn(col, df.columns,
                          f"business-critical column {col!r} was removed")
        # Values should be unchanged.
        self.assertEqual(list(df["Policy Number"]), ["POL001", "POL002", "POL003"])
        self.assertEqual(list(df["Status"]), ["In Force", "Lapsed", "In Force"])

    def test_inline_email_and_phone_redacted_in_notes(self):
        df, report = strip_pii(self.path)
        notes = df["Notes"].astype(str)
        # Row 0 had email + phone in the notes column — both should be redacted.
        self.assertNotIn("john@acme.com", " ".join(notes))
        self.assertNotIn("0411 222 333", " ".join(notes))
        # Row 2 had a +61 international format — also redacted.
        self.assertNotIn("+61 412 999 888", " ".join(notes))
        # The redaction marker should be present.
        self.assertIn("[REDACTED]", " ".join(notes))
        self.assertIn("Notes", report.get("values_redacted_in", []),
                      "Notes column should be in values_redacted_in")

    def test_row_count_preserved(self):
        df, report = strip_pii(self.path)
        self.assertEqual(len(df), 3)
        self.assertEqual(report["rows_processed"], 3)

    def test_no_email_string_anywhere_in_output(self):
        """Defence-in-depth: scan every cell for stray email patterns."""
        import re
        df, _ = strip_pii(self.path)
        email_re = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
        for col in df.columns:
            for val in df[col].astype(str):
                m = email_re.search(val)
                self.assertIsNone(m,
                                  f"email leaked through PII strip in column "
                                  f"{col!r}, value {val!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
