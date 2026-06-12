"""
merge_dataset.py — Merge all scraped CSVs into one master dataset.

Auto-discovers all CSV files in data/ matching platform_company.csv pattern.
Deduplicates on (company, job_id) — keeps row with longest description.
Skips diagnostic/reference files (companies.csv, ats_map.csv, etc.)

Usage:
    python3 analysis/merge_dataset.py
    python3 analysis/merge_dataset.py --data-dir data --output data/master_dataset.csv
"""

import argparse
import re
from pathlib import Path
from datetime import datetime

import pandas as pd

SCHEMA = [
    "company", "title", "job_id", "location", "country",
    "salary", "remote", "seniority", "us_citizenship_required",
    "security_clearance", "relocation_assistance", "source_platform",
    "date_posted", "scraped_at", "apply_url", "description_text",
]

# Files to skip — not scraped job data
SKIP_FILES = {
    "companies.csv", "ats_map.csv", "master_schema_empty.csv",
    "schema.md", "sample_output.csv", "mock_aerospace_jobs.csv",
    "master_dataset.csv",
}

# Known platform prefixes — used to identify scraped files
PLATFORMS = {
    "workday", "greenhouse", "lever", "icims", "talentbrew",
    "brassring", "taleo", "successfactors", "eightfold", "phenom",
}


def is_scraped_file(path: Path) -> bool:
    """Return True if this looks like a scraped jobs file."""
    name = path.name
    if name in SKIP_FILES:
        return False
    if not name.endswith(".csv"):
        return False
    # Accept files matching platform_company.csv OR legacy names like greenhouse_jobs.csv
    stem = path.stem
    prefix = stem.split("_")[0]
    if prefix in PLATFORMS:
        return True
    # Legacy enriched files
    if "_enriched" in stem or "_jobs" in stem:
        return True
    return False


def _normalize(df: pd.DataFrame, filepath: Path) -> pd.DataFrame:
    defaults = {
        "salary":                  "",
        "remote":                  "unknown",
        "seniority":               "unknown",
        "us_citizenship_required": "unknown",
        "security_clearance":      "unknown",
        "relocation_assistance":   "unknown",
        "date_posted":             "N/A",
        "scraped_at":              datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "country":                 "United States of America",
        "description_text":        "",
        "apply_url":               "",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = df[col].fillna(default)

    for col in SCHEMA:
        if col not in df.columns:
            df[col] = ""

    # Strip HTML from descriptions
    import re as _re
    df["description_text"] = df["description_text"].fillna("").apply(
        lambda t: _re.sub(r"<[^>]+>", " ", str(t)).strip()
    )

    return df[SCHEMA]


def main(data_dir: Path, output: Path) -> None:
    files = sorted([p for p in data_dir.glob("*.csv") if is_scraped_file(p)])

    if not files:
        print(f"No scraped CSV files found in {data_dir}/")
        return

    print(f"Found {len(files)} files to merge:\n")
    frames = []
    total_raw = 0

    for path in files:
        try:
            df = pd.read_csv(path, dtype=str, low_memory=False)
        except Exception as e:
            print(f"  ERROR  {path.name}: {e}")
            continue

        if len(df) == 0:
            print(f"  SKIP   {path.name} — empty")
            continue

        raw_count = len(df)
        total_raw += raw_count
        df = _normalize(df, path)
        frames.append(df)
        has_desc = (df["description_text"].str.len() > 50).sum()
        desc_pct = int(100 * has_desc / raw_count) if raw_count else 0
        print(f"  OK     {path.name:<45} {raw_count:>6} rows  {desc_pct}% desc")

    if not frames:
        print("No valid data found.")
        return

    print(f"\nRaw total: {total_raw:,} rows")

    master = pd.concat(frames, ignore_index=True)

    # Deduplicate — keep row with longest description
    master["_desc_len"] = master["description_text"].str.len().fillna(0)
    master = master.sort_values("_desc_len", ascending=False)
    master = master.drop_duplicates(subset=["company", "job_id"], keep="first")
    master = master.drop(columns=["_desc_len"]).reset_index(drop=True)

    print(f"After dedup: {len(master):,} unique jobs\n")

    print("By platform:")
    for platform, count in master["source_platform"].value_counts().items():
        print(f"  {platform:<20} {count:>6}")

    print(f"\nBy company (top 20):")
    for company, count in master["company"].value_counts().head(20).items():
        print(f"  {company:<40} {count:>6}")

    has_desc = (master["description_text"].str.len() > 50).sum()
    print(f"\nDescription coverage: {has_desc:,} / {len(master):,} ({100*has_desc//len(master)}%)")

    output.parent.mkdir(parents=True, exist_ok=True)
    master.to_csv(output, index=False)
    print(f"\nSaved {len(master):,} jobs → {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge all scraped CSVs into master dataset")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output",   type=Path, default=Path("data/master_dataset.csv"))
    args = parser.parse_args()
    main(args.data_dir, args.output)
