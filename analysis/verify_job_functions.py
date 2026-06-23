"""
Consolidated verification script for job_functions/*.txt files.

Runs every job function in job_functions/ against the live master_dataset.csv,
prints match counts side by side, and specifically inspects two known risks
flagged before any of these files were committed:

1. sales.txt's "account manager" term may catch "Control Account Manager"
   (an EVM/program-finance title, not a sales role) -- same false-positive
   shape as the Site Reliability Engineer / hardware Reliability Engineer
   collision caught earlier in this project.
2. reliability_engineer.txt has no site_reliability_engineer.txt counterpart.
   Without the split, "reliability engineer" may be matching Site Reliability
   Engineer (a software/DevOps title) as well as hardware reliability roles --
   two different job markets that should not be blended.

This script does NOT modify any job_functions file. It only reports findings
so each flagged file can be fixed and re-verified before committing.
"""
import sys
sys.path.insert(0, '/root/aerospace-ai-job-analysis/analysis')
from function_analysis import title_matches_term
import pandas as pd
from pathlib import Path

DATASET = '/root/aerospace-ai-job-analysis/data/master_dataset.csv'
JOB_FUNCTIONS_DIR = '/root/aerospace-ai-job-analysis/job_functions'


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
    print("FLAGGED CHECK 1: sales.txt 'account manager' -- inspecting matched titles")
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

    print()
    print("=" * 60)
    print("FLAGGED CHECK 2: reliability_engineer.txt -- checking for SRE leakage")
    print("=" * 60)
    if 'reliability_engineer' in results:
        mask, terms = results['reliability_engineer']
        matched_titles = df.loc[mask, 'title'].dropna().unique()
        sre_titles = [t for t in matched_titles if 'site reliability' in t.lower() or t.lower().strip() in ('sre', 'sre engineer')]
        print(f"Total reliability_engineer.txt matches: {mask.sum()}")
        print(f"Titles that are actually Site Reliability Engineer (SRE): {len(sre_titles)}")
        for t in sorted(sre_titles)[:20]:
            print(f"  {t}  <-- SRE, not hardware reliability (likely false positive)")
        if not sre_titles:
            print("  None found -- no SRE leakage detected in this dataset run.")
    else:
        print("reliability_engineer.txt not found in job_functions/")


if __name__ == '__main__':
    main()
