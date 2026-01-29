"""
PII Stripping for SourceTECH.
Removes personally identifiable information from portfolio files before processing.
"""
import pandas as pd
from pathlib import Path
from typing import Tuple, Dict
import re

# Columns to REMOVE entirely
PII_COLUMN_PATTERNS = [
    'email', 'e-mail', 'e mail',
    'phone', 'mobile', 'telephone', 'tel', 'fax', 'contact number',
    'address', 'street', 'suburb', 'city', 'postcode', 'post code', 'zip', 'state',
    'tfn', 'tax file', 'abn', 'acn',
    'medicare', 'health fund', 'member number', 'membership',
    'bank', 'bsb', 'account number', 'account no',
    'driver', 'passport', 'licence', 'license'
]

# Columns to ANONYMIZE (replace values, keep column)
NAME_COLUMN_PATTERNS = [
    'name', 'client', 'insured', 'policy holder', 'policyholder',
    'first name', 'last name', 'surname', 'given name', 'middle name',
    'contact', 'owner', 'member name', 'applicant'
]

# Columns to explicitly KEEP (never strip)
KEEP_PATTERNS = [
    'dob', 'date of birth', 'birth', 'age',
    'policy', 'premium', 'sum', 'benefit', 'commission',
    'product', 'cover', 'status', 'in force', 'insurer',
    'frequency', 'amount', 'rate', 'gender', 'sex', 'smoker',
    'occupation', 'class', 'waiting', 'term', 'loading',
    'commencement', 'inception', 'start date', 'effective',
    'expiry', 'renewal', 'anniversary'
]


def should_remove_column(col_name: str) -> bool:
    """Check if column should be removed entirely."""
    col_lower = col_name.lower().strip()

    # Never remove if it matches a keep pattern
    for keep in KEEP_PATTERNS:
        if keep in col_lower:
            return False

    # Remove if matches PII pattern
    for pii in PII_COLUMN_PATTERNS:
        if pii in col_lower:
            return True

    return False


def should_anonymize_column(col_name: str) -> bool:
    """Check if column should have values anonymized."""
    col_lower = col_name.lower().strip()

    # Never anonymize if it matches a keep pattern
    for keep in KEEP_PATTERNS:
        if keep in col_lower:
            return False

    # Anonymize if matches name pattern
    for name in NAME_COLUMN_PATTERNS:
        if name in col_lower:
            return True

    return False


def strip_pii(file_path: Path) -> Tuple[pd.DataFrame, Dict]:
    """
    Remove PII from portfolio file.

    Args:
        file_path: Path to the Excel or CSV file

    Returns:
        Tuple of (cleaned_dataframe, report_dict)
        Report contains:
            - original_columns: list of all original columns
            - columns_removed: list of columns that were removed
            - columns_anonymized: list of columns that were anonymized
            - rows_processed: number of rows
            - final_columns: list of remaining columns
    """
    # Load file
    if file_path.suffix.lower() == '.csv':
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)

    # Ensure column names are strings
    df.columns = df.columns.astype(str)

    report = {
        'original_columns': list(df.columns),
        'columns_removed': [],
        'columns_anonymized': [],
        'rows_processed': len(df)
    }

    # Identify columns to process
    cols_to_remove = []
    cols_to_anonymize = []

    for col in df.columns:
        if should_remove_column(col):
            cols_to_remove.append(col)
        elif should_anonymize_column(col):
            cols_to_anonymize.append(col)

    # Anonymize name columns
    for col in cols_to_anonymize:
        df[col] = [f"CLIENT_{i+1:05d}" for i in range(len(df))]
        report['columns_anonymized'].append(col)

    # Remove PII columns
    df = df.drop(columns=cols_to_remove, errors='ignore')
    report['columns_removed'] = cols_to_remove

    # Scan remaining text columns for any PII patterns we might have missed
    email_pattern = re.compile(r'\S+@\S+\.\S+')
    # Phone pattern without groups to avoid pandas warning
    phone_pattern = re.compile(r'\b\+?61[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}\b|\b0\d{3}[\s\-]?\d{3}[\s\-]?\d{3}\b')

    redacted_columns = []
    for col in df.select_dtypes(include=['object']).columns:
        # Check if any values look like emails or phones
        sample = df[col].dropna().head(100).astype(str)
        had_pii = False

        # Use regex=True explicitly to avoid pandas warning
        if sample.str.contains(email_pattern, regex=True).any():
            df[col] = df[col].apply(lambda x: re.sub(email_pattern, '[REDACTED]', str(x)) if pd.notna(x) else x)
            had_pii = True

        if sample.str.contains(phone_pattern, regex=True).any():
            df[col] = df[col].apply(lambda x: re.sub(phone_pattern, '[REDACTED]', str(x)) if pd.notna(x) else x)
            had_pii = True

        if had_pii:
            redacted_columns.append(col)

    if redacted_columns:
        report['values_redacted_in'] = redacted_columns

    report['final_columns'] = list(df.columns)

    return df, report
