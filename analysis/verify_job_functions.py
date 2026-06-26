"""
Consolidated verification script for job_functions/*.txt files.

Runs every job function in job_functions/ against the live master_dataset.csv
and prints match counts side by side. Also spot-checks sales.txt's matched
titles for "account manager" variants -- a broad version of that term once
caught "Control Account Manager" (an EVM/program-finance title, unrelated to
sales). The term was narrowed and re-verified; this check stays as a cheap
regression guard in case the term list changes again later.

This script does NOT modify any job_functions file. It only reports findings.
"""
import sys
sys.path.insert(0, '/root/aerospace-ai-job-analysis/analysis')
from function_analysis import title_matches_term
import pandas as pd
from pathlib import Path

DATASET = 'data/master_dataset.csv'
JOB_FUNCTIONS_DIR = 'job_functions'


def load_terms(path):
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def match_titles(titles, terms):
    """Returns boolean mask: True if title matches ANY term in terms."""
    return titles.apply(lambda t: any(title_matches_term(t, term) for term in terms) if isinstance(t, str) else False)


def main():
    df = pd.read_csv(DATASET, low_memory=False)
    titles = df['title']
    total = len(df)

    files = sorted(Path(JOB_FUNCTIONS_DIR).glob('*.txt'))

    print(f"{'FUNCTION':38s} {'MATCHES':>8s} {'% OF TOTAL':>10s}")
    print("-" * 60)

    results = {}
    for f in files:
        terms = load_terms(f)
        mask = match_titles(titles, terms)
        n = mask.sum()
        results[f.stem] = (mask, terms)
        print(f"{f.stem:38s} {n:8d} {n/total*100:9.2f}%")

    print()
    print("=" * 60)
    print("sales.txt regression check: 'account manager' titles")
    print("=" * 60)
    if 'sales' in results:
        mask, terms = results['sales']
        matched_titles = df.loc[mask, 'title'].dropna().unique()
        account_mgr_titles = [t for t in matched_titles if 'account manager' in t.lower() or 'control account' in t.lower()]
        print(f"Total sales.txt matches: {mask.sum()}")
        print(f"Titles containing 'account manager' or 'control account': {len(account_mgr_titles)}")
        for t in sorted(account_mgr_titles)[:20]:
            flag = "  <-- CONTROL ACCOUNT (likely false positive)" if 'control' in t.lower() else ""
            print(f"  {t}{flag}")
    else:
        print("sales.txt not found in job_functions/")


if __name__ == '__main__':
    main()
