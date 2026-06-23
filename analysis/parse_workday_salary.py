"""
Parse pre-existing messy salary strings in Workday-platform CSV files.

Unlike extract_salary.py (which extracts salary from description_text when
the salary column is empty), this script PARSES the salary column itself --
Workday's salary field is already populated with raw strings, not numbers,
e.g. "$74,500 - $105,525", "$32.19/hr; $39.17/hr", "$83,200 - $102,000; $32.19/hr; $39.17/hr".

Locked scoping decisions (confirmed, do not change without re-confirming):
- Magnitude cutoff for hourly vs annual: any number < $200 is hourly, >= $200 is annual.
  No legitimate annual aerospace salary is under $200; no real hourly rate exceeds $200.
- Multiple semicolon-separated ANNUAL ranges in one cell (no hourly mixed in):
  average all midpoints (same convention as geographic multi-range averaging
  in extract_salary.py).
- Multiple semicolon-separated HOURLY figures, no annual range present at all:
  average the hourly midpoints, then annualize at 2,080 hrs/yr.
- Mixed cell (annual range PRESENT alongside standalone hourly figure(s)):
  use ONLY the annual range; the hourly figures are dropped entirely.
  (The "OT rate" theory was tested against real data and rejected -- hourly
  figures in mixed cells were below the annual range's low end, not at the
  1.5x premium OT pay would require. No defensible interpretation of what
  the hourly figures represent in this case, so they are discarded rather
  than guessed at.)
- Dash normalization: real en-dash (\u2013) and no-space-before-dash variants
  ("$76,200- $110,000") are both normalized to a standard " - " before parsing.
- Malformed/unparseable numbers: skipped, left as the ORIGINAL STRING value
  (not nulled, not guessed) -- this script never destroys data it can't
  confidently parse; it only overwrites a cell when it produces a clean number.
"""
import pandas as pd
import re
import sys

HOURLY_CUTOFF = 200.0
HOURS_PER_YEAR = 2080

# Normalize en-dash and no-space-before-dash variants to a standard " - "
DASH_NORMALIZE = re.compile(r'\s*[\u2013\u2014]\s*|(?<=\d)-\s*(?=\$)')

# A single dollar figure, with or without thousands-separator commas, with or
# without decimals, with or without /hr suffix. Comma grouping is optional --
# confirmed real format on Aerovironment postings: "$115000 - $170000" (no
# commas at all). The no-comma branch is tried FIRST and requires 4-6 digits,
# so it greedily captures the full number instead of the comma-grouped branch
# matching only the first 1-3 digits and stopping early.
FIGURE_PATTERN = re.compile(
    r'\$(\d{4,6}(?:\.\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,3}(?:\.\d{1,2})?)\s*(/\s*h(?:ou)?r)?',
    re.IGNORECASE
)


def normalize_dashes(s):
    return DASH_NORMALIZE.sub(' - ', s)


def is_plausible_annual(val):
    """Reject obviously corrupted annual figures. No real aerospace annual
    salary in this dataset is below 15000 or above 600000; anything
    outside that is almost certainly a scraping/typo artifact (e.g. a stray
    digit or missing comma producing 1,023,721 instead of 102,372)."""
    return 15000 <= val <= 600000


def is_plausible_hourly(val):
    """Reject obviously corrupted hourly figures. No real hourly rate in
    this dataset is below 10 or above 100; a 2/hr figure is almost
    certainly a scraping artifact, not a real wage."""
    return 10 <= val <= 100


def reject_group_outliers(values):
    """
    Given a list of same-unit figures meant to represent a coherent group
    (e.g. several hourly rates for the same role/shift), drop any value
    that is wildly inconsistent with the rest of the group -- more than
    3x away from the group's median. This catches cases like
    "$2/hr; $28.04/hr; $37.12/hr" where one figure is clearly corrupted
    relative to its own group, even though each individual figure passes
    the absolute plausibility check on its own.
    Returns (kept_values, n_dropped).
    """
    if len(values) < 2:
        return values, 0
    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    median = sorted_vals[mid] if len(sorted_vals) % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    if median == 0:
        return values, 0
    kept = [v for v in values if (1 / 3) <= (v / median) <= 3]
    return kept, len(values) - len(kept)


def parse_figures(segment):
    """
    Parse a single semicolon-delimited segment (e.g. "$74,500 - $105,525" or
    "$32.19/hr"). Returns a list of (value, is_hourly) tuples for every
    dollar figure found in the segment, in order. Figures that fail the
    absolute plausibility check (is_plausible_annual/is_plausible_hourly)
    are dropped here -- before any grouping or averaging happens.
    """
    results = []
    for m in FIGURE_PATTERN.finditer(segment):
        raw = m.group(1).replace(',', '')
        try:
            val = float(raw)
        except ValueError:
            continue
        has_hr_suffix = m.group(2) is not None
        is_hourly = has_hr_suffix or val < HOURLY_CUTOFF
        if is_hourly and not is_plausible_hourly(val):
            continue
        if not is_hourly and not is_plausible_annual(val):
            continue
        results.append((val, is_hourly))
    return results


def parse_salary_cell(raw):
    """
    Parse one salary cell. Returns (annual_value, source_note) or
    (None, None) if nothing could be confidently parsed.
    source_note documents which rule fired, for logging/audit purposes.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, None

    text = normalize_dashes(raw)
    segments = [seg.strip() for seg in text.split(';') if seg.strip()]

    all_figures = []
    for seg in segments:
        all_figures.extend(parse_figures(seg))

    if not all_figures:
        return None, None

    annual_figures = [v for v, hourly in all_figures if not hourly]
    hourly_figures = [v for v, hourly in all_figures if hourly]

    if annual_figures:
        # Drop wild outliers relative to the group before pairing/averaging --
        # same defense-in-depth as the hourly path.
        annual_figures, n_dropped_outliers = reject_group_outliers(annual_figures)
        if not annual_figures:
            return None, None
        # Mixed cell or annual-only cell: use ONLY the annual figures.
        # Pair them into ranges if there are exactly 2, 4, 6... (range pairs);
        # if an odd count or ambiguous grouping, average them all as midpoints
        # of consecutive pairs where possible, else just average the raw figures.
        if len(annual_figures) % 2 == 0:
            midpoints = [
                (annual_figures[i] + annual_figures[i + 1]) / 2
                for i in range(0, len(annual_figures), 2)
            ]
        else:
            # Odd count -- can't pair cleanly into ranges; average the raw
            # figures directly rather than guess at pairing.
            midpoints = annual_figures
        value = round(sum(midpoints) / len(midpoints), 2)
        if hourly_figures:
            note = 'annual_preferred_over_mixed_hourly'
        elif n_dropped_outliers:
            note = 'annual_only_outlier_dropped'
        else:
            note = 'annual_only'
        return value, note

    # No annual figures at all -- average hourly figures and annualize.
    # First, drop any figure that's a wild outlier relative to the rest of
    # the group (catches corrupted figures that pass the absolute plausibility
    # bounds individually but are still inconsistent with their own group).
    hourly_figures, n_dropped_outliers = reject_group_outliers(hourly_figures)
    if not hourly_figures:
        return None, None
    if len(hourly_figures) % 2 == 0:
        hourly_midpoints = [
            (hourly_figures[i] + hourly_figures[i + 1]) / 2
            for i in range(0, len(hourly_figures), 2)
        ]
    else:
        hourly_midpoints = hourly_figures
    avg_hourly = sum(hourly_midpoints) / len(hourly_midpoints)
    value = round(avg_hourly * HOURS_PER_YEAR, 2)
    note = 'hourly_annualized' if n_dropped_outliers == 0 else 'hourly_annualized_outlier_dropped'
    return value, note


def process_file(path):
    df = pd.read_csv(path, low_memory=False)
    if 'salary' not in df.columns:
        print(f"SKIP {path}: no salary column")
        return df, None

    if 'salary_currency' not in df.columns:
        df['salary_currency'] = None

    df['salary'] = df['salary'].astype(object)

    candidates = df['salary'].notna()
    n_candidates = candidates.sum()

    stats = {'parsed': 0, 'unparseable_left_as_string': 0}
    log_rows = []

    for idx in df.loc[candidates].index:
        raw = df.at[idx, 'salary']
        value, note = parse_salary_cell(raw)
        if value is not None:
            df.at[idx, 'salary'] = value
            df.at[idx, 'salary_currency'] = 'USD'
            stats['parsed'] += 1
            log_rows.append({
                'row_index': idx,
                'title': df.at[idx, 'title'] if 'title' in df.columns else None,
                'original_value': raw,
                'parsed_value': value,
                'rule': note,
            })
        else:
            stats['unparseable_left_as_string'] += 1
            log_rows.append({
                'row_index': idx,
                'title': df.at[idx, 'title'] if 'title' in df.columns else None,
                'original_value': raw,
                'parsed_value': None,
                'rule': 'unparseable_left_unchanged',
            })

    print(f"=== {path} ===")
    print(f"  Pre-existing non-null salary cells: {n_candidates}")
    print(f"  Parsed to clean number: {stats['parsed']}")
    print(f"  Left unchanged (unparseable): {stats['unparseable_left_as_string']}")

    log_df = pd.DataFrame(log_rows)
    return df, log_df


if __name__ == '__main__':
    path = sys.argv[1]
    df, log_df = process_file(path)
    log_path = path.replace('.csv', '_workday_salary_parse_log.csv')
    if log_df is not None:
        log_df.to_csv(log_path, index=False)
        print(f"  Log written to {log_path}")
