"""
check_data.py — Comprehensive data quality check on all CSV files.

Checks:
- Row counts
- Description coverage (>50 chars)
- Schema field completeness
- Sample titles and companies
- Duplicate job_ids within each file
- Apply URL format validity

Usage:
    python3 analysis/check_data.py
    python3 analysis/check_data.py --verbose
"""

import argparse
import pandas as pd
from pathlib import Path

DATA_DIR = Path("data")

# All files we expect to have — in merge order
FILES = [
    # Enriched files (have descriptions)
    ("l3harris_enriched.csv",        "L3Harris — TalentBrew enriched"),
    ("brassring_enriched.csv",       "Lockheed Martin — Brassring enriched"),
    ("greenhouse_jobs.csv",          "Greenhouse — 8 companies"),
    ("wing_jobs.csv",                "Wing Aviation — Greenhouse"),
    ("lever_jobs.csv",               "Hermeus + Elroy Air — Lever"),
    # Workday
    ("workday_jobs_enriched.csv",    "Workday — 14 companies enriched"),
    ("workday_jobs.csv",             "Workday — 21 companies no-desc fallback"),
    ("workday_test.csv",             "Booz Allen — Workday"),
    ("workday_fixes.csv",            "Vantor/Wisk/SNC — Workday"),
    ("workday_fixes_enriched.csv",   "Vantor/Wisk/SNC — enriched attempt"),
    # Taleo
    ("textron_jobs.csv",             "Textron — Taleo"),
    ("bell_jobs.csv",                "Bell — Taleo"),
    ("aar_jobs.csv",                 "AAR Corp — Taleo"),
    # iCIMS
    ("joby_jobs.csv",                "Joby Aviation — iCIMS"),
    ("gd_jobs.csv",                  "General Dynamics — iCIMS"),
    ("peraton_jobs.csv",             "Peraton — iCIMS"),
    # SuccessFactors
    ("bombardier_jobs.csv",          "Bombardier — SuccessFactors"),
    # TalentBrew (no desc)
    ("boeing_jobs.csv",              "Boeing — TalentBrew no-desc"),
]

SCHEMA = [
    "company", "title", "job_id", "location", "country",
    "salary", "remote", "seniority", "us_citizenship_required",
    "security_clearance", "relocation_assistance", "source_platform",
    "date_posted", "scraped_at", "apply_url", "description_text",
]


def check_file(path: Path, label: str, verbose: bool = False) -> dict:
    if not path.exists():
        return {"status": "MISSING", "rows": 0}

    try:
        df = pd.read_csv(path, dtype=str, low_memory=False)
    except Exception as e:
        return {"status": f"ERROR: {e}", "rows": 0}

    rows = len(df)
    if rows == 0:
        return {"status": "EMPTY", "rows": 0}

    # Description coverage
    desc_col = df.get("description_text", pd.Series(dtype=str)).fillna("")
    has_desc = (desc_col.str.len() > 50).sum()
    desc_pct = int(100 * has_desc / rows) if rows else 0

    # Missing schema fields
    missing_fields = [f for f in SCHEMA if f not in df.columns]

    # Duplicate job_ids
    if "job_id" in df.columns:
        dupe_count = df["job_id"].duplicated().sum()
    else:
        dupe_count = 0

    # Bad apply_urls
    if "apply_url" in df.columns:
        bad_urls = df["apply_url"].fillna("").apply(
            lambda u: not str(u).startswith("http")
        ).sum()
    else:
        bad_urls = 0

    # Company breakdown
    companies = {}
    if "company" in df.columns:
        companies = df["company"].value_counts().head(5).to_dict()

    result = {
        "status": "OK",
        "rows": rows,
        "desc_pct": desc_pct,
        "missing_fields": missing_fields,
        "dupes": dupe_count,
        "bad_urls": bad_urls,
        "companies": companies,
    }

    if verbose and companies:
        result["sample_titles"] = df["title"].dropna().head(3).tolist() if "title" in df.columns else []

    return result


def main(verbose: bool = False) -> None:
    print(f"\n{'File':<42} {'Status':<8} {'Rows':>6} {'Desc%':>6} {'Dupes':>6} {'BadURL':>7} {'Issues'}")
    print("=" * 100)

    total_rows = 0
    total_desc = 0
    all_issues = []

    for fname, label in FILES:
        path = DATA_DIR / fname
        r = check_file(path, label, verbose)

        if r["status"] == "MISSING":
            print(f"  {'  MISSING':42} {'---':<8}")
            print(f"    {label}")
            continue

        if r["status"] == "EMPTY" or r["status"].startswith("ERROR"):
            print(f"  {fname:<42} {r['status']:<8}")
            continue

        issues = []
        if r["missing_fields"]:
            issues.append(f"missing: {', '.join(r['missing_fields'])}")
        if r["dupes"] > 0:
            issues.append(f"{r['dupes']} dupes")
        if r["bad_urls"] > 0:
            issues.append(f"{r['bad_urls']} bad URLs")

        issue_str = "; ".join(issues) if issues else "none"
        status = "⚠️ " if issues else "✅"

        print(f"  {fname:<42} {status:<8} {r['rows']:>6} {str(r['desc_pct'])+'%':>6} {r['dupes']:>6} {r['bad_urls']:>7}  {issue_str}")

        if verbose and r.get("companies"):
            for co, cnt in list(r["companies"].items())[:3]:
                print(f"    {co}: {cnt} jobs")

        total_rows += r["rows"]
        total_desc += int(r["rows"] * r["desc_pct"] / 100)

        if issues:
            all_issues.append((fname, issues))

    print("=" * 100)
    overall_pct = int(100 * total_desc / total_rows) if total_rows else 0
    print(f"  {'TOTAL':<42} {'':8} {total_rows:>6} {str(overall_pct)+'%':>6}")

    if all_issues:
        print(f"\n⚠️  Files with issues:")
        for fname, issues in all_issues:
            print(f"  {fname}: {'; '.join(issues)}")
    else:
        print(f"\n✅ All files look clean.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    main(args.verbose)
