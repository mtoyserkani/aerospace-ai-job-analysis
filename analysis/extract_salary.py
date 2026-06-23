"""
Retroactive salary extraction from description_text.
Scoped decisions (locked, do not change without re-confirming):
- Multiple distinct salary ranges in one description -> midpoint average across all
- Malformed/truncated numbers (e.g. "$259" after "$139,500 -") -> skip, leave null
- Non-USD currency -> store raw number, separate salary_currency column, no conversion
- No $ pattern found -> leave null (true non-disclosure)
"""
import pandas as pd
import re
import sys

# Matches $X,XXX or $XX,XXX or $XXX,XXX (comma-grouped, 2-3 digit groups before comma)
# Also matches $XXX.XX (decimal, rare but seen) but NOT malformed "$259" standalone after a dash
RANGE_PATTERN = re.compile(
    r'\$(\d{2,3}(?:,\d{3})+(?:\.\d{1,2})?)\s*(?:-|to|MIN\s*-)\s*\$(\d{2,3}(?:,\d{3})+(?:\.\d{1,2})?)',
    re.IGNORECASE
)
SINGLE_PATTERN = re.compile(r'\$(\d{2,3}(?:,\d{3})+(?:\.\d{1,2})?)')

# SCA/Union/Intern hourly rate, e.g. "Rate or Range $26.07" -- annualized at 2,080 hrs/yr
# to match the existing salary-by-seniority convention used elsewhere in this project.
HOURLY_RATE_PATTERN = re.compile(r'Rate or Range\s*\$(\d{1,3}\.\d{2})', re.IGNORECASE)
HOURS_PER_YEAR = 2080

CDN_MARKERS = re.compile(r'\bCDN\b|\bCanadian\b|\bCAD\b', re.IGNORECASE)


def parse_number(s):
    """Convert '139,500' or '139,500.00' to float. Returns None if malformed."""
    cleaned = s.replace(',', '')
    try:
        val = float(cleaned)
    except ValueError:
        return None
    return val


def is_malformed_pair(low_str, high_str):
    """
    Detect the truncation bug: e.g. '139,500' - '259' where high < low
    and high has fewer digits than expected (missing leading digits).
    """
    low = parse_number(low_str)
    high = parse_number(high_str)
    if low is None or high is None:
        return True
    if high < low:
        return True
    # Sanity: salary ranges shouldn't span more than ~5x low->high for aerospace roles
    if high > low * 5:
        return True
    return False


def extract_ranges(text):
    """
    Find all valid (non-malformed) salary ranges in text.
    Returns list of (low, high, currency) tuples. Currency inferred from
    nearby context (CDN markers within 50 chars after the match).
    """
    if not isinstance(text, str):
        return []
    results = []
    for m in RANGE_PATTERN.finditer(text):
        low_str, high_str = m.group(1), m.group(2)
        if is_malformed_pair(low_str, high_str):
            continue
        low, high = parse_number(low_str), parse_number(high_str)
        # check for currency marker in a window after the match
        window = text[m.end():m.end() + 60]
        currency = 'CAD' if CDN_MARKERS.search(window) else 'USD'
        results.append((low, high, currency))
    return results


def compute_salary(text):
    """
    Returns (salary_value, currency, num_ranges_found, source) for a single description.
    salary_value = midpoint of each range, averaged across all valid ranges found.
    If no range found, falls back to a single hourly SCA/Union rate, annualized
    at 2,080 hrs/yr (source='hourly_annualized').
    If neither found -> (None, None, 0, None).
    """
    ranges = extract_ranges(text)
    if ranges:
        currencies = set(r[2] for r in ranges)
        currency = currencies.pop() if len(currencies) == 1 else 'MIXED'
        midpoints = [(low + high) / 2 for low, high, _ in ranges]
        avg_midpoint = sum(midpoints) / len(midpoints)
        return round(avg_midpoint, 2), currency, len(ranges), 'range'

    if isinstance(text, str):
        m = HOURLY_RATE_PATTERN.search(text)
        if m:
            hourly = float(m.group(1))
            annualized = round(hourly * HOURS_PER_YEAR, 2)
            return annualized, 'USD', 1, 'hourly_annualized'

    return None, None, 0, None


def process_file(path, dry_run=True):
    df = pd.read_csv(path, low_memory=False)
    if 'salary' not in df.columns or 'description_text' not in df.columns:
        print(f"SKIP {path}: missing salary or description_text column")
        return df, None

    if 'salary_currency' not in df.columns:
        df['salary_currency'] = None

    candidates = df['salary'].isna() & df['description_text'].notna()
    n_candidates = candidates.sum()

    stats = {'filled': 0, 'still_null_no_pattern': 0, 'still_null_malformed_only': 0, 'multi_range': 0}
    log_rows = []

    for idx in df.loc[candidates].index:
        text = df.at[idx, 'description_text']
        salary_val, currency, n_ranges, source = compute_salary(text)
        # also check: did we find ANY $ pattern at all (even malformed) for diagnostics
        any_dollar = bool(SINGLE_PATTERN.search(text)) if isinstance(text, str) else False

        if salary_val is not None:
            df.at[idx, 'salary'] = salary_val
            df.at[idx, 'salary_currency'] = currency
            stats['filled'] += 1
            if n_ranges > 1:
                stats['multi_range'] += 1
            log_rows.append({
                'row_index': idx,
                'job_id': df.at[idx, 'job_id'] if 'job_id' in df.columns else None,
                'title': df.at[idx, 'title'] if 'title' in df.columns else None,
                'extracted_salary': salary_val,
                'currency': currency,
                'n_ranges_found': n_ranges,
                'source': source,
                'status': 'filled'
            })
        elif any_dollar:
            stats['still_null_malformed_only'] += 1
            log_rows.append({
                'row_index': idx,
                'job_id': df.at[idx, 'job_id'] if 'job_id' in df.columns else None,
                'title': df.at[idx, 'title'] if 'title' in df.columns else None,
                'extracted_salary': None,
                'currency': None,
                'n_ranges_found': 0,
                'status': 'malformed_skipped'
            })
        else:
            stats['still_null_no_pattern'] += 1

    print(f"=== {path} ===")
    print(f"  Candidates (null salary + has description): {n_candidates}")
    print(f"  Filled: {stats['filled']} (of which multi-range averaged: {stats['multi_range']})")
    print(f"  Skipped - malformed pattern only: {stats['still_null_malformed_only']}")
    print(f"  Still null - no $ pattern at all: {stats['still_null_no_pattern']}")

    log_df = pd.DataFrame(log_rows)
    return df, log_df


if __name__ == '__main__':
    path = sys.argv[1]
    df, log_df = process_file(path)
    log_path = path.replace('.csv', '_salary_extraction_log.csv')
    if log_df is not None:
        log_df.to_csv(log_path, index=False)
        print(f"  Log written to {log_path}")
