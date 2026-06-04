"""
false_positive_detector.py — Context-aware keyword categorizer.

The most important methodological tool in this repo.

The problem: generic keyword search on "certification", "assurance", and
"governance" returns hundreds of hits that look like AI governance signals
but are actually mechanics getting A&P licenses, QA inspectors on the
manufacturing floor, and IT security teams doing NIST audits.

This script categorizes each hit by context so you know what you actually found.

This was built after comparing results with another AI model that found
"302 certification hits" and called it a governance signal. When categorized
correctly, only 5 of those 302 hits were AI/ML certification. The rest were
noise. This kind of false positive is what makes AI-assisted market research
dangerous if you don't validate the methodology.

Usage:
    python false_positive_detector.py --input data/aerospace_all_jobs.csv --keyword certification
    python false_positive_detector.py --input data/aerospace_all_jobs.csv --keyword assurance
    python false_positive_detector.py --input data/aerospace_all_jobs.csv --keyword governance --verbose
"""

import argparse
import csv
import re
from collections import Counter
from pathlib import Path


# ---------------------------------------------------------------------------
# Category definitions — order matters (first match wins)
# ---------------------------------------------------------------------------

CATEGORY_RULES = {
    "certification": [
        ("AI / ML certification",               r"\b(ai certif|ml certif|machine learning certif|algorithm certif|neural network certif)\b"),
        ("Aircraft / type certification",        r"\b(type certif|airworthiness certif|faa certif|easa certif|part 21|do-?178|tc application)\b"),
        ("Software certification (DO-178C)",     r"\bdo-?178\b"),
        ("Security clearance",                   r"\b(security clearance|secret clearance|ts/sci|classified|clearance required)\b"),
        ("Export / ITAR / trade compliance",     r"\b(itar|export compliance|ear |trade compliance|export controlled)\b"),
        ("Quality / manufacturing cert",         r"\b(as910[0-9]|nadcap|iso 900|lean certif|six sigma certif|quality certif)\b"),
        ("Personal / professional credential",   r"\b(a&p|pmp certif|certifications? (required|preferred|a plus)|cpa |pe license)\b"),
        ("General / unclear",                    r".*"),  # catch-all
    ],
    "assurance": [
        ("AI / ML assurance",                    r"\b(ai assurance|ml assurance|model assurance|algorithmic assurance)\b"),
        ("Software / safety / system assurance", r"\b(software assurance|safety assurance|system assurance|design assurance level)\b"),
        ("Information assurance / cybersecurity",r"\b(information assurance|ia certif|cyber assurance|nist)\b"),
        ("Mission assurance",                    r"\bmission assurance\b"),
        ("Quality Assurance (QA/manufacturing)", r"\b(quality assurance|qa\b|qc\b|inspection|manufacturing assurance)\b"),
        ("General / unclear",                    r".*"),
    ],
    "governance": [
        ("AI / ML governance",                   r"\b(ai governance|ml governance|model governance|algorithmic governance)\b"),
        ("Data governance",                      r"\bdata governance\b"),
        ("IT / cybersecurity governance",        r"\b(it governance|information governance|cyber governance|security governance)\b"),
        ("Corporate / financial / supplier",     r"\b(corporate governance|financial governance|supplier governance|esg)\b"),
        ("General / boilerplate",                r".*"),
    ],
    "compliance": [
        ("AI / ML compliance",                   r"\b(ai compliance|ml compliance|responsible ai)\b"),
        ("Export / ITAR compliance",             r"\b(itar|export compliance|ear compliance|ofac)\b"),
        ("Safety / airworthiness compliance",    r"\b(safety compliance|airworthiness compliance|faa compliance|easa compliance)\b"),
        ("IT / security compliance",             r"\b(cmmc|dfars|nist 800|fedramp|hipaa|sox compliance)\b"),
        ("Quality / manufacturing compliance",   r"\b(as910[0-9]|iso 900|nadcap|quality compliance)\b"),
        ("General / boilerplate",                r".*"),
    ],
}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def categorize(text: str, keyword: str) -> str:
    rules = CATEGORY_RULES.get(keyword.lower())
    if not rules:
        return "unknown keyword"
    text_lower = text.lower()
    for category, pattern in rules:
        if re.search(pattern, text_lower):
            return category
    return "General / unclear"


def run(csv_path: Path, keyword: str, verbose: bool) -> None:
    keyword = keyword.lower()
    if keyword not in CATEGORY_RULES:
        print(f"No rules defined for '{keyword}'. Available: {', '.join(CATEGORY_RULES.keys())}")
        return

    with open(csv_path, newline="", encoding="utf-8") as f:
        jobs = list(csv.DictReader(f))

    pattern = rf"\b{re.escape(keyword)}\b"
    hits = [j for j in jobs if re.search(pattern, (j.get("description_text", "") + " " + j.get("title", "")).lower())]

    print(f"\nKeyword: '{keyword}'")
    print(f"Total hits: {len(hits)} of {len(jobs)} jobs ({len(hits)/len(jobs)*100:.1f}%)")
    print(f"{'─'*60}")

    categories = Counter(
        categorize(
            j.get("description_text", "") + " " + j.get("title", ""),
            keyword
        )
        for j in hits
    )

    print("\nBy category:")
    for category, count in categories.most_common():
        pct = count / len(hits) * 100
        bar = "█" * int(pct / 3)
        ai_flag = " ← AI-relevant" if "AI" in category or "ml" in category.lower() else ""
        print(f"  {category:<45} {count:>4}  {bar}{ai_flag}")

    ai_relevant = sum(v for k, v in categories.items() if "AI" in k or "ml" in k.lower())
    print(f"\nAI-relevant hits: {ai_relevant} of {len(hits)} ({ai_relevant/max(len(hits),1)*100:.1f}%)")
    print(f"Noise: {len(hits) - ai_relevant} hits that look like governance but aren't")

    if verbose:
        print(f"\n{'─'*60}")
        print("VERBOSE: All hits with categories")
        print(f"{'─'*60}")
        for job in hits:
            corpus = job.get("description_text", "") + " " + job.get("title", "")
            cat = categorize(corpus, keyword)
            # Find context around the keyword
            idx = corpus.lower().find(keyword)
            ctx = corpus[max(0, idx-60):idx+120].replace("\n", " ").strip()
            print(f"\n  [{cat}]")
            print(f"  Company: {job.get('company', '')}")
            print(f"  Title:   {job.get('title', '')}")
            print(f"  Context: ...{ctx}...")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Context-aware keyword categorizer — separates real AI governance signals from noise"
    )
    parser.add_argument("--input",   type=Path, default=Path("data/aerospace_all_jobs.csv"))
    parser.add_argument("--keyword", type=str,  default="certification",
                        choices=list(CATEGORY_RULES.keys()),
                        help="Keyword to categorize")
    parser.add_argument("--verbose", action="store_true",
                        help="Show every hit with context")
    args = parser.parse_args()
    run(args.input, args.keyword, args.verbose)
