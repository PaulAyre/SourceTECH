# Gotcha.md - Non-obvious Bugs, Edge Cases, and Lessons Learned

This file documents tricky bugs, edge cases, and non-obvious behaviors across SourceTECH and the broader PavTECH/SmarterSheets ecosystem to avoid repeating mistakes.

---

# SourceTECH Gotchas

## 1. Database

- **SQLite is single-writer**: Background PavTECH processing uses threads with separate DB connections. Under load, this could cause `database is locked` errors.
- **No migrations**: Schema is created via `init_db()` on startup with `CREATE TABLE IF NOT EXISTS`. Adding columns requires manual ALTER TABLE or DB recreation.

## 2. PII Stripping

- PII stripping happens on upload, not on revaluation. The cleaned file is what gets sent to PavTECH.
- Name columns are anonymized to CLIENT_00001, CLIENT_00002, etc. - this is irreversible.
- The keep-list in `pii_stripper.py` prevents accidental removal of business-critical columns (DOB, premium, commission). If adding new PII patterns, check they don't match keep-list entries.

## 3. File Handling

- Files are saved to `uploads/<url_code>/` with timestamp prefix (e.g., `20250101_120000_portfolio.xlsx`)
- Cleaned files get `cleaned_` prefix and are always saved as `.xlsx` regardless of input format
- Fuzzy filename matching (60% threshold) triggers a confirmation dialog for potential replacements
- Chrome download suffixes like `(1)`, `_2`, `copy` are handled client-side

## 4. PavTECH Integration

- PavTECH processing runs in a daemon thread - if the Flask process dies, processing is lost
- Polling timeout is up to 10 minutes (`pavtech_client.py`)
- PavTECH URL defaults to `http://localhost:5000` - must be configured via env var for production
- Files are processed one-by-one via `/api/batch/process_file` even though they're uploaded as a batch

## 5. Admin Portal

- Single password auth (no user accounts) - password stored in env var, default is "changeme"
- Session-based auth using Flask's built-in sessions

## 6. Email Service

- DM contacts are hardcoded in `email_service.py` (Paul, Thomas, Mike)
- DM is resolved by first name key (e.g., `dm_name="Paul Ayre"` -> `dm_key="paul"`)
- Email requires SENDGRID_API_KEY env var - gracefully degrades to console logging if not set
- Currency is hardcoded to AUD

## 7. Deployment

- `render.yaml` configured for Render.com with 1GB persistent disk at `/data`
- App runs on port 5002 locally (`app.py:831`)
- Gunicorn runs with 2 workers and 120s timeout in production
- Database and uploads live on persistent disk in production (`/data/sourcetech.db`, `/data/temp`)

## 8. Validation Philosophy

- Validation is **non-blocking** - warns instead of rejecting files
- Missing fields result in assumptions (e.g., Age 63, Income Protection product, All In Force)
- Required fields: Premium (only truly required), Benefit Type, Status, DOB/Age (all optional with defaults)

## 9. URL Codes

- Generated via `secrets.token_urlsafe(6)[:8].upper()` - 8 uppercase chars
- If collision occurs on insert, one retry is attempted (then would fail)
- URL codes are case-sensitive in the database but generated as uppercase

## 10. Valuation Calculation

- `excel_parser.py` estimates portfolio value two ways:
  - **Commission multiple**: annual commission x 3.5
  - **Premium percentage**: annual premium x 0.35 (assumes ~10% commission rate)
- Commission method is preferred when commission data is available

---

# SmarterSheets / Render Deploy Gotchas

*Copied from SmarterSheets/render_deploy for cross-project reference. These lessons apply to any PavTECH ecosystem project.*

---

## v3.161 - In Force Status Detection

**Date:** January 9, 2026

### The Bug

The `is_in_force()` function used a blacklist approach:
```python
lapsed_keywords = ['lapse', 'cancel', 'terminate', 'inactive', 'closed']
if any(keyword in status_lower for keyword in lapsed_keywords):
    return False
return True  # Assume everything else is In Force
```

### Why It Was Wrong

Insurance status values like "Waiver of premium", "Expired", "On claim" don't contain blacklist keywords, so they were treated as In Force and earning commission.

**Real-world impact:** 11 AIA policies incorrectly included in valuation (4 Expired, 6 Waiver of premium, 1 On claim).

### The Fix

Switched to **whitelist approach** in ALL code paths:

**Consistent whitelist:**
```python
in_force_keywords = ['in force', 'inforce', 'in-force', 'active', 'current']
```

### Lesson

For commission calculations, **only include policies that explicitly state they are active**. When in doubt, exclude - it's better to undervalue than overvalue a book.

---

## General Insurance Data Gotchas

### Status Values Vary by Insurer

| Insurer | "Active" Status | "Inactive" Statuses |
|---------|-----------------|---------------------|
| AIA | "In force", "Inforce" | Cancelled, Expired, Waiver of premium, On claim, Terminated |
| TAL | "Active", "In Force" | Lapsed, Cancelled, Surrendered |
| MLC | "Current" | Lapsed, Paid Up, Cancelled |

Always check the actual status values when adding a new insurer.

### Premium Frequency Gotchas

- "Annually" ≠ "Annual Premium" - Some files have Annual Premium column that's actually monthly premium x 12
- "Monthly" typically means 11.43 payments per year (due to policy anniversary dates), not 12
- Always validate: `stated_annual_premium` should ≈ `payment_amount x frequency_multiplier`

### Commission Rate Gotchas

- Some files have commission rates WITH GST (11%, 22%), others WITHOUT (10%, 20%)
- Rate column names like "(w/o GST)" indicate GST needs to be added
- Always check if rates are decimals (0.10) or percentages (10) and normalize

---

## Code Architecture Gotchas

### Never Execute Scripts on Code to Save Time

Per user instructions: "NEVER execute scripts on code to save time. It ALWAYS breaks things."

Always make targeted, manual edits to specific files.

### Session Directory Race Conditions

Fixed in v3.99: Batch processing must use file-specific directories, not shared session directories. Otherwise parallel file processing causes race conditions.

---

## v3.162 - Render Disk Mount Path

**Date:** January 14, 2026

### The Bug

Render disk was mounted at wrong path, causing data loss on every deploy:
```yaml
# WRONG - disk mounted at parent level
mountPath: /opt/render/project/src/uploads

# But app runs from render_deploy/ subdirectory, so it writes to:
# /opt/render/project/src/render_deploy/uploads/
```

### Why It Was Wrong

- `render.yaml` has `rootDir: render_deploy`
- App uses relative path `uploads/` which resolves to working directory
- Disk was mounted one level up, so app wrote to ephemeral storage

### The Fix

```yaml
# CORRECT - disk mounted where app actually writes
mountPath: /opt/render/project/src/render_deploy/uploads
```

### Lesson

When using `rootDir` in Render, all mount paths must include that subdirectory. The app's working directory is `/opt/render/project/src/{rootDir}/`.

**Note for SourceTECH:** SourceTECH does NOT use `rootDir` in its render.yaml, so the mount path is simply `/data`. But if `rootDir` is ever added, this gotcha applies.

---

## v3.163 - GST Detection Default for Ambiguous Rates

**Date:** January 17, 2026

### The Bug

GST detection heuristic incorrectly added GST to commission rates that didn't match any "common" patterns.

Commission rates like 16% and 27%:
- Not in common base rates list (5%, 7.5%, 10%, 12.5%, 15%, 17.5%, 20%, 22.5%, 25%, 27.5%, 30%)
- Not recognized as GST-inclusive rates (rate/1.1 didn't match common bases either)
- Both classified as "ambiguous"

When ambiguous, the code defaulted to `'excluded'` (adding GST), which inflated commission by 10%.

### Why It Was Wrong

Default behavior was "safer to add GST than not" but this is backwards:
- Unusual rates like 16%, 27% are often negotiated rates already finalized
- Adding GST when it shouldn't be there **inflates** valuation (worse outcome)
- NOT adding GST when it should be there **deflates** valuation (more conservative)

### The Fix

Changed default to `'included'` (no GST added).

### Lesson

For financial calculations: **conservative errors are better than inflationary errors**. When in doubt about GST, don't add it - a slightly lower valuation is better than an inflated one.

---

## Git Push = Render Redeploy

**Date:** January 19, 2026

### The Rule

**DO NOT push to GitHub unless explicitly told to.** Every push to `main` triggers an automatic Render redeploy.

### Why This Matters

- User needs control over when production deploys happen
- Untested code should not go live automatically
- Multiple small pushes = multiple unnecessary redeploys

### Correct Workflow

1. **Commit locally** - Always commit changes to track progress
2. **DO NOT push** - Until user explicitly requests it
3. **When tested & stable** - Ask user: "Ready to push to deploy?"
4. **Push only on approval** - Then push to trigger Render deploy

### Lesson

Commits are cheap and local. Pushes are deployments. Always ask before deploying.

---

## v3.169 - Batch Sorting Was Alphabetical (WRONG!)

**Date:** January 19, 2026

### The Bug

The `get_vendor_metrics()` function sorted batch IDs alphabetically:
```python
batch_dirs.sort(key=lambda x: x[0], reverse=True)  # x[0] is batch_id string
```

### Why It Was Wrong

Batch IDs like "17Jan26_0021" vs "18Dec25_0742":
- Alphabetically: "18" > "17", so December 2025 would appear as MORE RECENT than January 2026
- This is completely backwards!

**Real-world impact:** Old batches were being used for vendor metrics instead of the latest ones.

### The Fix

Parse the actual date from batch_id and sort by datetime:
```python
# Parse date from batch_id format: DDMonYY_HHMM
date_part = batch_id.split('_')[0] + '_' + batch_id.split('_')[1][:4]
parsed_date = datetime.strptime(date_part, "%d%b%y_%H%M")
batch_dirs.append((batch_id, batch_path, parsed_date))

# Sort by PARSED DATE, not alphabetically!
batch_dirs.sort(key=lambda x: x[2], reverse=True)
```

### Lesson

When sorting by date-like strings, ALWAYS parse to actual datetime objects first. String sorting only works if the format is ISO-8601 (YYYY-MM-DD) or similar that naturally sorts correctly.

---

## TERMINOLOGY - Vendor vs Insurer (CRITICAL DISTINCTION)

**Date:** January 20, 2026

### Correct Definitions

| Term | Meaning | Examples |
|------|---------|----------|
| **Vendor/Client** | Person/business selling their insurance book to us | "Network FP Insurance", "Smith Financial" |
| **Insurer** | Insurance company whose policies are in the book | AIA, TAL, MLC, Zurich, OnePath |

### What Went Wrong

In v3.166, "New Vendor" naming was applied to **insurer sheet names** instead of **vendor/client folder names**. Sheet names should ALWAYS be the actual insurer name.

### Lesson

**Vendor = Client = the person selling to us**
**Insurer = the insurance company on the policies**

Never conflate these two concepts. Insurer names are extracted from file contents and used for sheet names. Vendor/Client names are folder-level organization for who owns the book.

---

## v3.175 - Short Status Codes (Resolution Life "A" = Active)

**Date:** January 30, 2026

### The Bug

Resolution Life uses single-letter status code `"A"` in `CONTRACT STATUS CODE` column to mean "Active". The In Force whitelist uses **substring matching** (`keyword in status_lower`), checking if keywords like `"active"` appear inside the status value. Since `"a"` is shorter than `"active"`, the substring check `"active" in "a"` returns False.

All 5 Resolution Life policies were marked as NOT in force, giving valuation = $0.

### Why Simple Fix Won't Work

Cannot add `"a"` to the substring keyword list because `"a" in "lapsed"` -> True, `"a" in "cancelled"` -> True. It would match everything.

### The Fix

Added **exact-match** checks for short status codes, separate from substring keywords:
```python
in_force_exact_codes = ['a', 'if']
if status_lower in in_force_exact_codes:
    return True
```

Applied consistently across all 5 code paths.

### Lesson

When adding new status values to the whitelist, consider whether they need **exact match** or **substring match**. Short codes (1-2 characters) must always use exact match. Also check what status codes each insurer actually uses - don't assume they use full English words.

---

**Last Updated:** February 2, 2026
