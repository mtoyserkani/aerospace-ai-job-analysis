"""
keyword_analysis.py — Governance and capability keyword scan across job postings.

Reproduces the analysis behind:
"I scanned 4,563 aerospace job postings for AI governance language. Here's what I found."

METHODOLOGY NOTE — Entities vs. parent companies:
  The dataset contains 89 legal entities (e.g. Boeing Company, Boeing Distribution,
  Boeing Aerospace Operations). For analysis, these are normalized to parent companies
  so all Boeing entities are counted as Boeing, all Airbus entities as Airbus, etc.
  The article reports "89 companies scraped" (entities) while analysis groups by
  parent company. The parent mapping is in data/parent_company_map.json.

Keywords are loaded from the keywords/ directory — edit those files to
customise the analysis for a different industry or research question.

Usage:
    python analysis/keyword_analysis.py
    python analysis/keyword_analysis.py --input data/aerospace_all_jobs.csv
    python analysis/keyword_analysis.py --export results/
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Parent company normalization
# ---------------------------------------------------------------------------

def load_parent_map(data_dir: Path) -> dict[str, str]:
    path = Path(data_dir) / "parent_company_map.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    print(f"  Warning: {path} not found. Analysis will use raw entity names.")
    return {}


def normalize_company(name: str, parent_map: dict) -> str:
    return parent_map.get(name, name) if name else "Unknown"


# ---------------------------------------------------------------------------
# Load keywords
# ---------------------------------------------------------------------------

def load_keywords(keywords_dir: Path) -> dict[str, list[str]]:
    result = {}
    kw_dir = Path(keywords_dir)
    if not kw_dir.exists():
        print(f"  Warning: keywords/ directory not found. Using built-in defaults.")
        return _default_keywords()
    for path in sorted(kw_dir.glob("*.txt")):
        keywords = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        result[path.stem] = keywords
        print(f"  Loaded {len(keywords)} keywords from {path.name}")
    return result


def _default_keywords() -> dict[str, list[str]]:
    return {
        "governance": [
            "ai governance", "ai certification", "ai compliance", "responsible ai",
            "ai safety", "ai assurance", "model monitoring", "ml validation",
            "learning assurance", "arp6983", "explainability", "mlops",
            "model validation", "data governance", "uncertainty quantification",
        ],
        "capability": [
            "machine learning", "artificial intelligence", "autonomous systems",
            "autonomous flight", "computer vision", "large language model",
            "data scientist", "dataset", "training data", "telemetry", "ci/cd",
        ],
    }


# ---------------------------------------------------------------------------
# Load jobs
# ---------------------------------------------------------------------------

def load_jobs(csv_path: Path, parent_map: dict) -> list[dict]:
    jobs = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["parent_company"] = normalize_company(
                row.get("company", ""), parent_map
            )
            jobs.append(row)
    return jobs


def corpus(job: dict) -> str:
    return (job.get("title", "") + " " + job.get("description_text", "")).lower()


# ---------------------------------------------------------------------------
# Keyword scan
# ---------------------------------------------------------------------------

def scan(jobs: list[dict], keywords: list[str]) -> dict[str, dict]:
    results = {}
    for kw in keywords:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b"
        hits = [j for j in jobs if re.search(pattern, corpus(j))]

        # Group by PARENT company for reporting
        company_counts: dict[str, int] = defaultdict(int)
        for h in hits:
            company_counts[h.get("parent_company", "Unknown")] += 1

        results[kw] = {
            "count":         len(hits),
            "companies":     dict(sorted(company_counts.items(), key=lambda x: -x[1])[:5]),
            "sample_titles": list({h.get("title", "") for h in hits})[:3],
        }
    return results


# ---------------------------------------------------------------------------
# Two-track overlap
# ---------------------------------------------------------------------------

def compute_overlap(jobs: list[dict], capability_keywords: list[str]) -> dict:
    ai_pat   = "|".join(r"\b" + re.escape(k) + r"\b" for k in capability_keywords[:15])
    cert_pat = r"\b(certification|airworthiness|verification|assurance|compliance)\b"

    ai_set   = {i for i, j in enumerate(jobs) if re.search(ai_pat,   corpus(j))}
    cert_set = {i for i, j in enumerate(jobs) if re.search(cert_pat, corpus(j))}
    both     = ai_set & cert_set

    overlap_jobs = [jobs[i] for i in both]
    return {
        "ai_only":     len(ai_set - cert_set),
        "cert_only":   len(cert_set - ai_set),
        "both":        len(both),
        "total":       len(jobs),
        "overlap_pct": round(len(both) / max(len(jobs), 1) * 100, 1),
        "sample_overlap_titles": [j.get("title", "") for j in overlap_jobs[:8]],
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(jobs, keyword_results, overlap, parent_map):
    total      = len(jobs)
    entities   = len({j.get("company") for j in jobs})
    parents    = len({j.get("parent_company") for j in jobs if j.get("parent_company") != "Unknown"})

    print(f"\n{'='*62}")
    print(f"AEROSPACE AI JOB ANALYSIS")
    print(f"Dataset: {total:,} jobs")
    print(f"Entities scraped: {entities}  |  Parent companies: {parents}")
    print(f"Note: analysis groups all subsidiary entities under parent company")
    print(f"{'='*62}")

    for section_name, results in keyword_results.items():
        print(f"\n{'─'*62}")
        print(f"{section_name.upper().replace('_', ' ')} KEYWORDS")
        print(f"{'─'*62}")
        max_count = max((v["count"] for v in results.values()), default=1) or 1
        for kw, data in results.items():
            count = data["count"]
            bar   = "█" * int(count / max_count * 30) if count else ""
            cos   = ", ".join(list(data["companies"].keys())[:2]) if count else ""
            print(f"  {kw:<40} {count:>4}  {bar}")
            if cos and count:
                print(f"  {'':40}       → {cos}")

    print(f"\n{'─'*62}")
    print("TWO-TRACK OVERLAP")
    print(f"{'─'*62}")
    print(f"  AI/ML language only:        {overlap['ai_only']:>5}")
    print(f"  Cert/V&V language only:     {overlap['cert_only']:>5}")
    print(f"  Both (overlap):             {overlap['both']:>5}  ({overlap['overlap_pct']}% of dataset)")
    print(f"\n  Sample overlap titles:")
    for t in overlap["sample_overlap_titles"][:5]:
        print(f"    → {t}")
    print(f"\n  The bridge role connecting AI teams to certification teams")
    print(f"  does not appear in the data.")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main(csv_path, keywords_dir, data_dir, export_dir):
    print(f"\nLoading parent company map...")
    parent_map = load_parent_map(data_dir)
    print(f"  {len(parent_map)} entity → parent mappings loaded")

    print(f"\nLoading {csv_path}...")
    jobs = load_jobs(csv_path, parent_map)
    print(f"  {len(jobs):,} jobs loaded")

    print(f"\nLoading keywords from {keywords_dir}/...")
    all_keywords = load_keywords(keywords_dir)

    print("\nRunning keyword scan...")
    keyword_results = {name: scan(jobs, kws) for name, kws in all_keywords.items()}

    cap_kws = all_keywords.get("capability", list(_default_keywords()["capability"]))
    overlap = compute_overlap(jobs, cap_kws)

    print_report(jobs, keyword_results, overlap, parent_map)

    if export_dir:
        out = Path(export_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name, results in keyword_results.items():
            with open(out / f"{name}_results.json", "w") as f:
                json.dump(results, f, indent=2)
        with open(out / "overlap_results.json", "w") as f:
            json.dump(overlap, f, indent=2)
        print(f"\nResults exported → {out}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",        type=Path, default=Path("data/aerospace_all_jobs.csv"))
    parser.add_argument("--keywords-dir", type=Path, default=Path("keywords"))
    parser.add_argument("--data-dir",     type=Path, default=Path("data"))
    parser.add_argument("--export",       type=Path, default=None)
    args = parser.parse_args()
    main(args.input, args.keywords_dir, args.data_dir, args.export)
