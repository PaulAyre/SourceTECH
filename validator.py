"""
Portfolio file validation for SourceTECH.
Validates uploaded files and provides graceful degradation with helpful warnings.

Philosophy: Accept what they give us, warn about assumptions, never block unnecessarily.
These are busy people - let them upload and get a rough valuation immediately.
"""
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple

# Field detection patterns
FIELD_PATTERNS = {
    'annual_premium': {
        'patterns': ['annual premium', 'premium', 'gross premium', 'total premium', 'prem'],
        'critical': True,  # Policies without premium can't be valued
    },
    'benefit_type': {
        'patterns': ['benefit', 'product', 'cover type', 'plan type', 'policy type', 'benefit type', 'cover'],
        'critical': False,
        'assumption': 'Income Protection',
        'assumption_reason': "We'll treat unknown products as Income Protection (most conservative). Life/TPD/Trauma policies may be worth more!",
    },
    'in_force': {
        'patterns': ['in force', 'in-force', 'status', 'policy status', 'active', 'inforce'],
        'critical': False,
        'assumption': 'All In Force',
        'assumption_reason': "We'll assume all policies are currently active. If any have lapsed, the final settlement value will be adjusted.",
    },
    'dob_or_age': {
        'patterns': ['dob', 'date of birth', 'birth date', 'birth', 'age', 'age next', 'age_next', 'client age'],
        'critical': False,
        'assumption': 'Age 63',
        'assumption_reason': "We'll use age 63 (conservative estimate - lowest multiple). Adding client ages could significantly increase your valuation!",
    }
}

# Frequency column patterns (for premium calculation)
FREQUENCY_PATTERNS = ['frequency', 'freq', 'payment freq', 'premium freq', 'pay freq', 'payment frequency']


def find_matching_column(columns: List[str], patterns: List[str]) -> str:
    """Find a column that matches any of the patterns."""
    for col in columns:
        col_lower = col.lower().strip()
        for pattern in patterns:
            if pattern in col_lower:
                return col
    return None


def validate_portfolio_file(file_path: Path) -> Dict:
    """
    Validate a portfolio file with graceful degradation.

    Returns warnings with assumptions rather than blocking errors.
    Only truly invalid files (unreadable, empty, no premium) are rejected.

    Args:
        file_path: Path to the Excel or CSV file

    Returns:
        dict with:
            - valid: bool (only False if file is truly unusable)
            - errors: list of blocking error messages
            - warnings: list of warning messages with assumptions
            - assumptions: dict of field -> assumption made
            - found_fields: dict of field -> column name mappings
            - row_count: number of data rows
            - positives: list of positive feedback messages
    """
    errors = []
    warnings = []
    positives = []
    found_fields = {}
    assumptions = {}

    # Load file
    try:
        if file_path.suffix.lower() == '.csv':
            df = pd.read_csv(file_path)
        else:
            # Try to read Excel, handle multiple sheets
            xl = pd.ExcelFile(file_path)
            if len(xl.sheet_names) > 1:
                warnings.append(f'📋 File has {len(xl.sheet_names)} sheets - using first sheet: "{xl.sheet_names[0]}"')
            df = pd.read_excel(file_path, sheet_name=0)
    except Exception as e:
        return {
            'valid': False,
            'errors': [f'Could not read file: {str(e)}. Please check the file is a valid Excel or CSV.'],
            'warnings': [],
            'positives': [],
            'assumptions': {},
            'found_fields': {},
            'row_count': 0
        }

    if len(df) == 0:
        return {
            'valid': False,
            'errors': ['File appears to be empty. Please upload a file with policy data.'],
            'warnings': [],
            'positives': [],
            'assumptions': {},
            'found_fields': {},
            'row_count': 0
        }

    # Clean column names
    df.columns = df.columns.astype(str)
    columns = list(df.columns)

    # Check each field
    for field_key, field_config in FIELD_PATTERNS.items():
        matched_col = find_matching_column(columns, field_config['patterns'])

        if matched_col:
            found_fields[field_key] = matched_col
            positives.append(f"✓ Found {field_key.replace('_', ' ')}: \"{matched_col}\"")
        else:
            # Special case: premium can be amount + frequency
            if field_key == 'annual_premium':
                freq_col = find_matching_column(columns, FREQUENCY_PATTERNS)
                amount_col = find_matching_column(columns, ['amount', 'premium amount', 'payment', 'prem amt'])
                if freq_col and amount_col:
                    found_fields[field_key] = f"{amount_col} + {freq_col}"
                    positives.append(f"✓ Found premium: \"{amount_col}\" with frequency \"{freq_col}\"")
                elif field_config.get('critical'):
                    errors.append("Missing premium information. We need a Premium column to value policies.")
            elif field_config.get('critical'):
                errors.append(f"Missing required field: {field_key.replace('_', ' ')}")
            else:
                # Non-critical field - record assumption
                assumption = field_config.get('assumption', 'Unknown')
                reason = field_config.get('assumption_reason', '')
                assumptions[field_key] = {
                    'value': assumption,
                    'reason': reason
                }
                warnings.append(f"⚠️ No {field_key.replace('_', ' ')} column found. {reason}")

    # Check for empty/sparse data in found columns
    for field_key, col_name in found_fields.items():
        if '+' not in col_name and col_name in df.columns:
            empty_count = df[col_name].isna().sum()
            filled_count = len(df) - empty_count
            empty_pct = (empty_count / len(df)) * 100

            if empty_pct > 80:
                warnings.append(f"⚠️ \"{col_name}\" is {empty_pct:.0f}% empty ({filled_count} of {len(df)} rows have data)")
            elif empty_pct > 50:
                warnings.append(f"ℹ️ \"{col_name}\" is partially filled ({filled_count} of {len(df)} rows)")

    # Count policies with premium (the ones we can actually value)
    premium_col = found_fields.get('annual_premium')
    if premium_col and '+' not in premium_col and premium_col in df.columns:
        policies_with_premium = df[premium_col].notna().sum()
        policies_without = len(df) - policies_with_premium

        if policies_with_premium > 0:
            positives.append(f"✓ {policies_with_premium:,} policies have premium data")

        if policies_without > 0:
            warnings.append(f"ℹ️ {policies_without} policies have no premium and won't be included in valuation")

    # Helpful size feedback
    if len(df) >= 100:
        positives.insert(0, f"✓ Good portfolio size: {len(df):,} policies")
    elif len(df) >= 20:
        positives.insert(0, f"✓ Portfolio loaded: {len(df):,} policies")
    elif len(df) < 10:
        warnings.append(f"ℹ️ Small file with only {len(df)} rows - is this the complete portfolio?")

    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'positives': positives,
        'assumptions': assumptions,
        'found_fields': found_fields,
        'row_count': len(df)
    }


def get_validation_summary(validation_result: Dict) -> str:
    """
    Generate a human-friendly summary of validation results.
    Positives first, then warnings - encouraging tone.
    """
    lines = []

    # Start with positives
    if validation_result.get('positives'):
        lines.append("**What we found:**")
        for positive in validation_result['positives']:
            lines.append(f"  {positive}")
        lines.append("")

    # Then assumptions/warnings
    if validation_result.get('warnings'):
        lines.append("**Things to know:**")
        for warning in validation_result['warnings']:
            lines.append(f"  {warning}")
        lines.append("")

    # Finally errors (if any)
    if validation_result.get('errors'):
        lines.append("**Issues to fix:**")
        for error in validation_result['errors']:
            lines.append(f"  ❌ {error}")

    return "\n".join(lines)
