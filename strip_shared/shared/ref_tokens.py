"""Relationship tokens, server half, plus the Python twin of src/normalise.js.

Two steps, by design:
  1. The BROWSER hashes each name part with the public app key, so real names
     never leave the vendor's machine.                  -> browser_hash()
  2. The SERVER immediately hashes that again with REF_TOKEN_SECRET and stores
     only the result. The browser code is discarded.    -> server_hash()

Stored codes are stable forever and comparable across every upload. They cannot
be reversed without the server secret. They are pseudonymous personal
information, not anonymous data: keep them off every vendor-facing surface.

To match people you legitimately hold (the xPlan export), run their names
through normalise -> browser_hash -> server_hash on your side. Same code = same
person part. REF_BLANK never matches anything: use is_blank_token().

This module is shared by SourceTECH and ServiceTECH. The secret and the rules
MUST be identical in both or nothing matches. Never rotate the secret casually:
vendor-side codes cannot be rebuilt.

Standard library only. Python 3.9+.
"""
import hashlib
import hmac
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta

HEX_LENGTH = 32
# Public by nature: it ships in the vendor's page. It only stops the browser codes
# being a bare, keyless hash. It must NEVER change, and must be identical in every
# service, or codes stop matching. The protection is REF_TOKEN_SECRET, not this.
APP_KEY = "iplus-ref-v1-public-7c1f0e5a9b3d4268"
UNIT_SEP = chr(0x1F)
DOMAIN_GIVEN, DOMAIN_FAMILY, DOMAIN_DOB = "given", "family", "dob"

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pii_rules.json")
_rules_cache = None


def load_rules(path=None):
    global _rules_cache
    if path is None and _rules_cache is not None:
        return _rules_cache
    with open(path or _RULES_PATH, encoding="utf-8") as fh:
        rules = json.load(fh)
    if path is None:
        _rules_cache = rules
    return rules


def _ranges(pairs):
    return "".join(chr(a) if b is None or a == b else chr(a) + "-" + chr(b) for a, b in pairs)


_INVISIBLE = re.compile("[" + _ranges([(0x00, 0x1F), (0x7F, None), (0xA0, None), (0x1680, None), (0x2000, 0x200F),
                                       (0x2028, 0x202F), (0x205F, None), (0x2060, None), (0x3000, None), (0xFEFF, None)]) + "]")
_DIACRITICS = re.compile("[" + _ranges([(0x0300, 0x036F)]) + "]")
_APOSTROPHE = re.compile("[" + _ranges([(0x27, None), (0x60, None), (0x2018, None), (0x2019, None), (0x02BC, None)]) + "]")
_HYPHEN = re.compile("[" + chr(0x2D) + _ranges([(0x2010, 0x2015)]) + "]")
_JS_SPACE = re.compile(r"[ \t\n\r\f\v]+")


def trim_all(v):
    """Trim, always. Must match trimAll() in src/normalise.js exactly."""
    if v is None:
        return ""
    return _JS_SPACE.sub(" ", _INVISIBLE.sub(" ", str(v))).strip(" ")


def is_blank(v, rules=None):
    rules = rules or load_rules()
    t = trim_all(v).lower()
    return t == "" or t in rules["blankPlaceholders"]


def _words(v):
    s = unicodedata.normalize("NFKD", trim_all(v))
    s = _APOSTROPHE.sub("", _DIACRITICS.sub("", s).lower())
    return [w for w in re.split(r"[^a-z]+", s) if w]


def normalise_given(v, rules=None):
    rules = rules or load_rules()
    if is_blank(v, rules):
        return ""
    w = [x for x in _words(v) if x not in rules["titles"]]
    for x in w:
        if len(x) > 1:
            return x
    return w[0] if w else ""


def normalise_family(v, rules=None):
    """Returns (whole, [parts])."""
    rules = rules or load_rules()
    if is_blank(v, rules):
        return "", []
    raw = trim_all(v)
    w = [x for x in _words(raw) if x not in rules["suffixes"] and x not in rules["titles"]]
    whole = "".join(w)
    parts = []
    if _HYPHEN.search(raw):
        for p in _HYPHEN.split(raw):
            j = "".join(x for x in _words(p) if x not in rules["suffixes"])
            if len(j) >= 2 and j != whole:
                parts.append(j)
    return whole, parts


_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}


def _valid(y, m, d):
    if not (1880 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
        return False
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def _fmt(y, m, d):
    return "%04d-%02d-%02d" % (y, m, d) if _valid(y, m, d) else ""


def _expand_year(yy, now_year):
    return 1900 + yy if 2000 + yy > now_year else 2000 + yy


def _serial(n):
    if not (1 <= n <= 80000):
        return ""
    n = int(n)
    if n > 59:
        n -= 1
    d = date(1899, 12, 31) + timedelta(days=n)
    return _fmt(d.year, d.month, d.day)


def normalise_dob(v, rules=None, now_year=None):
    """DOB to YYYY-MM-DD or ''. Day-first for ambiguous dates. Must match normaliseDob() in JS."""
    rules = rules or load_rules()
    now_year = now_year or datetime.utcnow().year
    if v is None:
        return ""
    if isinstance(v, datetime):
        return _fmt(v.year, v.month, v.day)
    if isinstance(v, date):
        return _fmt(v.year, v.month, v.day)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _serial(v)
    if is_blank(v, rules):
        return ""
    s = trim_all(v)
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T\s].*)?$", s)
    if m:
        return _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{2}|\d{4})(?:\s.*)?$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if len(m.group(3)) == 2:
            y = _expand_year(y, now_year)
        if mo > 12 and d <= 12:
            d, mo = mo, d
        return _fmt(y, mo, d)
    m = re.match(r"^(\d{1,2})[-/.\s]?([A-Za-z]{3,9})[-/.,\s]*(\d{2}|\d{4})$", s)
    if m:
        mo = _MONTHS.get(m.group(2)[:4].lower()) or _MONTHS.get(m.group(2)[:3].lower())
        y = int(m.group(3))
        if len(m.group(3)) == 2:
            y = _expand_year(y, now_year)
        return _fmt(y, mo, int(m.group(1))) if mo else ""
    m = re.match(r"^([A-Za-z]{3,9})[-/.\s]*(\d{1,2})[,\s]+(\d{4})$", s)
    if m:
        mo = _MONTHS.get(m.group(1)[:4].lower()) or _MONTHS.get(m.group(1)[:3].lower())
        return _fmt(int(m.group(3)), mo, int(m.group(2))) if mo else ""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        return _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if re.match(r"^\d{4,5}(\.\d+)?$", s):
        return _serial(float(s))
    return ""


def _hmac_hex(key, msg):
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()[:HEX_LENGTH]


def browser_hash(app_key, domain, value):
    """What src/tokens.js produces. Blank value -> the browser's REF_BLANK."""
    if not app_key or len(app_key) < 16:
        raise ValueError("app key missing or too short")
    return _hmac_hex(app_key, "") if not value else _hmac_hex(app_key, domain + UNIT_SEP + value)


def server_hash(secret, browser_code):
    """Second, secret hash. Store THIS, never the browser code."""
    if not secret or len(secret) < 32:
        raise ValueError("REF_TOKEN_SECRET missing or too short (need 32+ characters)")
    if not re.match(r"^[0-9a-f]{%d}$" % HEX_LENGTH, browser_code or ""):
        raise ValueError("not a browser token")
    return _hmac_hex(secret, "ref2" + UNIT_SEP + browser_code)


def ref_blank(app_key, secret):
    """The stored code that means 'we looked and there was nothing'."""
    return server_hash(secret, browser_hash(app_key, DOMAIN_GIVEN, ""))


def is_blank_token(code, app_key, secret):
    return code == ref_blank(app_key, secret)


def person_tokens(app_key, secret, first="", last="", preferred="", dob=None, rules=None):
    """Full pipeline for a person you hold in the clear (e.g. an xPlan row)."""
    rules = rules or load_rules()
    whole, parts = normalise_family(last, rules)
    two = lambda domain, val: server_hash(secret, browser_hash(app_key, domain, val))
    return {
        "ref_first": two(DOMAIN_GIVEN, normalise_given(first, rules)),
        "ref_last": two(DOMAIN_FAMILY, whole),
        "ref_last_parts": [two(DOMAIN_FAMILY, p) for p in parts],
        "ref_pref": two(DOMAIN_GIVEN, normalise_given(preferred, rules)),
        "ref_dob": two(DOMAIN_DOB, normalise_dob(dob, rules)),
    }


def tokens_match(a, b, blank):
    """Never let REF_BLANK match. Returns (first, last, dob) booleans."""
    eq = lambda x, y: x == y and x != blank
    first = eq(a["ref_first"], b["ref_first"]) or eq(a["ref_pref"], b["ref_first"]) or eq(a["ref_first"], b["ref_pref"])
    last = eq(a["ref_last"], b["ref_last"]) or any(eq(p, b["ref_last"]) for p in a.get("ref_last_parts", [])) \
        or any(eq(a["ref_last"], p) for p in b.get("ref_last_parts", []))
    return first, last, eq(a["ref_dob"], b["ref_dob"])
