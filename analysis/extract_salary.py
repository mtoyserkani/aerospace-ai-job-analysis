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
#
# The second number's "$" is OPTIONAL -- confirmed real-world format on Mercury's
# postings: "$269,700 - 353,950" (second number has no $ sign). This is scoped
# narrowly: the separator (-/to/and/MIN-) must still be present and the second
# number must still be a full comma-grouped figure of the same shape as the first,
# so this does not loosen the pattern enough to catch unrelated nearby numbers.
#
# An optional "Max" label is allowed between the separator and the second $ --
# confirmed real format on General Dynamics postings: "$35,700 Annu. - Max $48,300
# Annu." A stray space after a comma in the second number ("$103, 270") is also
# tolerated -- confirmed scraping artifact, also from General Dynamics.
RANGE_PATTERN = re.compile(
    r'\$(\d{2,3}(?:,\d{3})+(?:\.\d{1,2})?)\s*(?:Annu\.?\s*)?(?:-|to|and|MIN\s*-)\s*(?:Max\s*)?\$?(\d{2,3}(?:,\s?\d{3})+(?:\.\d{1,2})?)',
    re.IGNORECASE
)
SINGLE_PATTERN = re.compile(r'\$(\d{2,3}(?:,\d{3})+(?:\.\d{1,2})?)')

# SCA/Union/Intern hourly rate, e.g. "Rate or Range $26.07" -- annualized at 2,080 hrs/yr
# to match the existing salary-by-seniority convention used elsewhere in this project.
HOURLY_RATE_PATTERN = re.compile(r'Rate or Range\s*\$(\d{1,3}\.\d{2})', re.IGNORECASE)
# Second hourly phrasing, confirmed on Flyzipline: "$44 per hour" (no decimal required,
# since intern/hourly rates are sometimes posted as whole dollars).
PER_HOUR_PATTERN = re.compile(r'\$(\d{1,3}(?:\.\d{1,2})?)\s*per\s*hour', re.IGNORECASE)
HOURS_PER_YEAR = 2080

CDN_MARKERS = re.compile(r'\bCDN\b|\bCanadian\b|\bCAD\b', re.IGNORECASE)

# Some platforms (confirmed: all Greenhouse-based scrapes) store description_text
# as raw HTML, not stripped plain text. A salary range like "$100,000 - $110,000"
# can be split across markup -- e.g. $100,000</span><span class="divider">&mdash;
# </span><span>$110,000 -- so RANGE_PATTERN never sees them as adjacent. Strip tags
# before range detection only; this does not touch the original description_text
# column or any other part of the pipeline (function_analysis.py's own matching
# is unaffected and untouched by this change).
#
# Dash entities are normalized in TWO forms: the encoded HTML entity (&mdash;) and
# the literal Unicode em-dash/en-dash character (—, –), confirmed present as a raw
# character (not an entity) on Heart Aerospace's postings.
HTML_TAG = re.compile(r'<[^>]+>')
HTML_ENTITY_DASH = re.compile(r'&mdash;|&ndash;|&#8212;|&#8211;|\u2014|\u2013')


def strip_html_for_salary(text):
    """Remove HTML tags and normalize common dash entities/characters to a literal
    '-' so RANGE_PATTERN can see ranges that were split across markup or use a
    real em-dash/en-dash character instead of a hyphen."""
    if not isinstance(text, str):
        return text
    text = HTML_ENTITY_DASH.sub('-', text)
    text = HTML_TAG.sub(' ', text)
    return text


def parse_number(s):
    """Convert '139,500' or '139,500.00' to float. Returns None if malformed.
    Also tolerates a stray space after the comma (e.g. '103, 270', a confirmed
    scraping artifact on General Dynamics postings)."""
    cleaned = s.replace(',', '').replace(' ', '')
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
    nearby context -- checked BOTH before and after the match, since the
    currency marker can appear in either position depending on phrasing:
    "CAD $225,900 - 282,400" (marker before) vs "$X - Y CDN annually"
    (marker after, confirmed real case on L3Harris/Quebec postings).
    The "before" window is bounded by the nearest line-break/list-item
    boundary so it cannot reach backward across a PRECEDING unrelated
    range in the same description (confirmed real failure mode: Mercury's
    "US employees: $239,000-298,800 / Canadian employees: CAD $225,900-282,400"
    -- without this bound, "Canadian" from the second line was being picked
    up as context for the first, unrelated, US-labeled range).
    HTML tags are stripped before matching (see strip_html_for_salary) so
    ranges split across markup, e.g. on Greenhouse-platform descriptions,
    are still detected.
    """
    if not isinstance(text, str):
        return []
    clean_text = strip_html_for_salary(text)
    results = []
    for m in RANGE_PATTERN.finditer(clean_text):
        low_str, high_str = m.group(1), m.group(2)
        if is_malformed_pair(low_str, high_str):
            continue
        low, high = parse_number(low_str), parse_number(high_str)

        # "before" window: from the last line-break/bullet boundary up to
        # the match start, capped at 60 chars -- never crosses into a
        # different line/list item.
        before_start = max(0, m.start() - 60)
        before_window = clean_text[before_start:m.start()]
        last_break = max(before_window.rfind('\n'), before_window.rfind('<li>'),
                          before_window.rfind('. '))
        if last_break != -1:
            before_window = before_window[last_break + 1:]

        # "after" window: capped at 60 chars, stops at the next line-break
        # so it doesn't reach forward into the NEXT range's context either.
        after_window_full = clean_text[m.end():m.end() + 60]
        next_break = after_window_full.find('\n')
        after_window = after_window_full if next_break == -1 else after_window_full[:next_break]

        has_cdn = bool(CDN_MARKERS.search(before_window)) or bool(CDN_MARKERS.search(after_window))
        currency = 'CAD' if has_cdn else 'USD'
        results.append((low, high, currency))
    return results


def compute_salary(text):
    """
    Returns (salary_value, currency, num_ranges_found, source) for a single description.
    salary_value = midpoint of each range, averaged across all valid ranges found
    THAT SHARE THE SAME CURRENCY. If a description contains ranges in multiple
    currencies (confirmed real case: Mercury posts separate US and Canadian pay
    bands for the same role), USD ranges are preferred and non-USD ranges are
    dropped entirely from the average -- they are never averaged together, since
    a USD figure and a CAD figure blended into one number is meaningless. If no
    USD range is present at all, falls back to averaging whatever single
    non-USD currency is present (e.g. a CAD-only posting still gets a CAD value).
    If no range found, falls back to a single hourly SCA/Union rate, annualized
    at 2,080 hrs/yr (source='hourly_annualized').
    If neither found -> (None, None, 0, None).
    """
    ranges = extract_ranges(text)
    if ranges:
        usd_ranges = [r for r in ranges if r[2] == 'USD']
        ranges_to_average = usd_ranges if usd_ranges else ranges
        currencies = set(r[2] for r in ranges_to_average)
        currency = currencies.pop() if len(currencies) == 1 else 'MIXED'
        midpoints = [(low + high) / 2 for low, high, _ in ranges_to_average]
        avg_midpoint = sum(midpoints) / len(midpoints)
        return round(avg_midpoint, 2), currency, len(ranges_to_average), 'range'

    if isinstance(text, str):
        m = HOURLY_RATE_PATTERN.search(text)
        if m:
            hourly = float(m.group(1))
            annualized = round(hourly * HOURS_PER_YEAR, 2)
            return annualized, 'USD', 1, 'hourly_annualized'

        m2 = PER_HOUR_PATTERN.search(text)
        if m2:
            hourly = float(m2.group(1))
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

    # Some files (confirmed: greenhouse_andurilindustries.csv) have a small number
    # of pre-existing non-numeric salary values (e.g. "$22 - $28.84/hour; ...").
    # These rows are excluded by the isna() filter below and left completely
    # untouched. Casting the column to object dtype here only allows the new
    # float values we write to coexist with those pre-existing strings --
    # it does not modify, parse, or clear any existing value.
    df['salary'] = df['salary'].astype(object)

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
