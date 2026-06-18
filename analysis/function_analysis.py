"""
function_analysis.py - Job-function-level skill frequency analysis.

The problem: keyword_analysis.py reports frequency across the whole dataset.
That's useful for an industry-level thesis, but useless for a job seeker
who wants to know "what does the market want from a Cybersecurity Engineer"
specifically - not diluted by 20,000 unrelated Mechanical Engineer postings.

This script:
  1. Buckets jobs into job functions using user-defined title-matching files
     in job_functions/*.txt (same format as keywords/*.txt - one term per line)
  2. Matches titles to those terms using fuzzy matching: token-based
     (word order / phrasing doesn't matter) plus typo tolerance
  3. Within each matched bucket, reports:
       - total jobs matched
       - seniority breakdown
       - frequency of every keywords/*.txt term, scoped to that bucket only,
         counted per JOB (not per occurrence) with word-boundary matching
         so short acronyms like "DER" don't match inside "order"
       - SKILL DISCOVERY: most common multi-word technical phrases in that
         bucket's descriptions, for job seekers who don't know what
         keywords to search for. Company names, EEO/legal boilerplate,
         and HTML artifacts are excluded - this surfaces actual skill
         and tool signal, not posting structure.

A job can match more than one function bucket (e.g. "Cybersecurity AI/ML
Engineer" matches both cybersecurity.txt and data_ai.txt). This is
intentional - crossover roles are informative, not noise to be removed.

Usage:
    python3 analysis/function_analysis.py --input data/master_dataset.csv
    python3 analysis/function_analysis.py --input data/master_dataset.csv --function cybersecurity
    python3 analysis/function_analysis.py --input data/master_dataset.csv --export data/function_results.csv
"""

import argparse
import html
import re
from collections import Counter
from pathlib import Path

import pandas as pd


JOB_POSTING_STOPWORDS = {
    "experience", "ability", "strong", "team", "responsible", "responsibility",
    "responsibilities", "skills", "skill", "knowledge", "work", "working",
    "years", "year", "required", "preferred", "plus", "candidate", "candidates",
    "position", "role", "job", "qualified", "qualifications", "qualification",
    "applicants", "applicant", "apply", "application", "please", "include",
    "including", "minimum", "maximum", "basic", "demonstrated", "proven",
    "excellent", "outstanding", "highly", "must", "ideal", "ideally", "looking",
    "seeking", "join", "environment", "fast", "paced", "dynamic", "innovative",
    "collaborative", "communication", "verbal", "written", "interpersonal",
    "detail", "oriented", "self", "starter", "motivated", "passionate",
    "across", "within", "such", "various", "other", "ensure", "ensuring",
    "support", "supporting", "perform", "performing", "performs", "develop",
    "developing", "develops", "provide", "providing", "provides", "help",
    "helps", "make", "makes", "use", "uses", "using", "new", "also", "well",
    "good", "high", "level", "levels", "related", "field", "degree", "bachelor",
    "master", "equivalent", "combination", "based", "able", "willing",
    "duties", "tasks", "etc",
}

EEO_LEGAL_BENEFITS_STOPWORDS = {
    "equal", "opportunity", "employer", "employee", "employment", "veteran",
    "disability", "disabilities", "race", "color", "religion", "sex", "gender",
    "identity", "expression", "national", "origin", "ancestry", "age", "status",
    "protected", "without", "regard", "regardless", "diversity", "inclusion",
    "inclusive", "background", "check", "drug", "screen", "physical", "lift",
    "pounds", "sit", "stand", "walk", "benefits", "salary", "compensation",
    "bonus", "insurance", "401k", "pto", "vacation", "time", "off", "paid",
    "remote", "hybrid", "onsite", "office", "travel", "relocation",
    "clearance", "citizen", "citizenship", "sexual", "orientation", "marital",
    "pregnancy", "genetic", "information", "personal", "privacy", "policy",
    "third", "party", "service", "provider", "cookies", "cookie", "consent",
    "browser", "window", "opens", "click", "here", "link", "nbsp", "amp",
    "lt", "gt", "quot", "rsquo", "lsquo", "ldquo", "rdquo", "mdash", "ndash",
    "accommodation", "accommodations", "harassment", "retaliation", "applies",
    "law", "laws", "local", "state", "federal", "eeo", "affirmative", "action",
    "complies", "compliance", "form", "forms", "notice", "request", "requesting",
    "data", "subject", "rights", "process", "processing", "purposes", "purpose",
    "collected", "collect", "share", "shared", "transfer", "transferred",
    "vendor", "vendors", "partner", "partners", "site", "sites", "website",
    "cross", "functional", "full", "part", "employees",
}

ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "as", "at", "by", "this", "that", "from", "will",
    "you", "your", "we", "our", "have", "has", "may", "can", "all", "more",
    "their", "they", "it", "its", "than", "into", "if", "not", "but", "who",
    "what", "when", "where", "how", "which", "while", "during", "between",
}

ALL_STOPWORDS = JOB_POSTING_STOPWORDS | EEO_LEGAL_BENEFITS_STOPWORDS | ENGLISH_STOPWORDS

SENIORITY_ORDER = ["Junior", "Mid", "Senior", "Lead", "Principal", "Manager", "Director"]


# ---------------------------------------------------------------------------
# Salary parsing
# ---------------------------------------------------------------------------
# Salary strings in this dataset are inconsistent: ranges with hyphens or
# en-dashes, hourly rates with or without an explicit "/hr" marker,
# multiple ranges bundled into one posting (different locations or levels
# separated by semicolons), and comma/decimal noise. This parses each
# into a single annualized midpoint, averaging multiple ranges when present.
# Only ~30% of jobs in this dataset have any salary data at all — averages
# below are computed over that subset, not the full dataset, and that
# caveat should always travel with any reported number.

def parse_salary_to_annual(raw) -> float:
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None

    is_hourly = bool(re.search(r'/\s*(hr|hour)', raw, re.IGNORECASE))

    segments = [s.strip() for s in raw.split(';') if s.strip()]
    if not segments:
        return None

    midpoints = []
    for seg in segments:
        numbers = re.findall(r'[\d,]+\.?\d*', seg)
        nums = []
        for n in numbers:
            cleaned = n.replace(',', '')
            try:
                nums.append(float(cleaned))
            except ValueError:
                continue
        if not nums:
            continue
        midpoint = (nums[0] + nums[1]) / 2 if len(nums) >= 2 else nums[0]
        midpoints.append(midpoint)

    if not midpoints:
        return None

    avg_midpoint = sum(midpoints) / len(midpoints)

    # Explicit /hr marker, OR magnitude heuristic: no real full-time
    # annual salary in this dataset is under ~$100. If the midpoint
    # lands below that with no marker, it's still hourly — the "/hr"
    # suffix was likely lost somewhere upstream in scraping.
    if is_hourly or avg_midpoint < 100:
        avg_midpoint = avg_midpoint * 2080  # standard full-time annual hours

    return avg_midpoint


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&\w+;", " ", text)
    return text


def _tokenize(text: str) -> list:
    return re.findall(r"[a-z0-9]+", clean_text(text).lower())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) == 0:
        return len(b)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def _tokens_match(title_token: str, term_token: str) -> bool:
    if title_token == term_token:
        return True
    if len(term_token) < 4:
        return False
    max_dist = 1 if len(term_token) <= 6 else 2
    return _levenshtein(title_token, term_token) <= max_dist


def title_matches_term(title: str, term: str) -> bool:
    title_tokens = _tokenize(title)
    term_tokens = _tokenize(term)
    if not term_tokens:
        return False
    for term_tok in term_tokens:
        if not any(_tokens_match(tt, term_tok) for tt in title_tokens):
            return False
    return True


def load_term_file(path: Path) -> list:
    terms = []
    if not path.exists():
        return terms
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line.lower())
    return terms


def load_job_functions(job_functions_dir: Path) -> dict:
    functions = {}
    if not job_functions_dir.exists():
        return functions
    for f in sorted(job_functions_dir.glob("*.txt")):
        terms = load_term_file(f)
        if terms:
            functions[f.stem] = terms
    return functions


def load_keywords(keywords_dir: Path) -> dict:
    categories = {}
    if not keywords_dir.exists():
        return categories
    for f in sorted(keywords_dir.glob("*.txt")):
        terms = load_term_file(f)
        if terms:
            categories[f.stem] = terms
    return categories


def count_keyword_per_job(corpus_lower: pd.Series, term: str) -> int:
    escaped = re.escape(term.lower())
    pattern = r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])"
    return int(corpus_lower.str.contains(pattern, regex=True, na=False).sum())


def discover_phrases(descriptions: pd.Series, titles: pd.Series, companies: pd.Series,
                      total_jobs: int, top_n: int = 25) -> list:
    company_tokens = set()
    for name in companies.dropna().unique():
        company_tokens.update(_tokenize(str(name)))
        company_tokens.add(re.sub(r"[^a-z0-9]", "", str(name).lower()))

    exclude = ALL_STOPWORDS | company_tokens | {"com", "www", "http", "https"}

    phrase_job_counts = Counter()

    for desc in descriptions.fillna(""):
        tokens = _tokenize(str(desc))
        filtered = [t for t in tokens if t not in exclude and len(t) > 2 and not t.isdigit()]

        seen_in_this_job = set()
        for i in range(len(filtered) - 1):
            seen_in_this_job.add((filtered[i], filtered[i + 1]))
        for i in range(len(filtered) - 2):
            seen_in_this_job.add((filtered[i], filtered[i + 1], filtered[i + 2]))

        for phrase in seen_in_this_job:
            phrase_job_counts[" ".join(phrase)] += 1

    noise_floor = 3
    ceiling = int(total_jobs * 0.95)

    filtered_counts = {
        phrase: count
        for phrase, count in phrase_job_counts.items()
        if noise_floor <= count <= max(ceiling, noise_floor)
    }

    return Counter(filtered_counts).most_common(top_n)


def analyze_function(df: pd.DataFrame, function_name: str, title_terms: list,
                      keyword_categories: dict) -> dict:
    mask = df["title"].fillna("").apply(
        lambda title: any(title_matches_term(title, term) for term in title_terms)
    )
    matched = df[mask].copy()

    result = {
        "function": function_name,
        "total_jobs": len(matched),
        "seniority": Counter(),
        "keyword_hits": {},
        "discovered_phrases": [],
        "companies": Counter(),
        "salary_by_seniority": {},
    }

    if len(matched) == 0:
        return result

    seniority_norm = matched["seniority"].fillna("Unknown").str.strip().str.title()
    result["seniority"] = Counter(seniority_norm)
    result["companies"] = Counter(matched["company"])

    # Salary by seniority — only over jobs where salary parses to a number.
    # Reported counts are jobs-with-salary-data, which is almost always
    # smaller than the seniority bucket's total job count, and that gap
    # is shown explicitly rather than silently averaged over.
    matched["_seniority_norm"] = seniority_norm
    matched["_parsed_salary"] = matched["salary"].apply(parse_salary_to_annual)

    salary_by_seniority = {}
    for level in matched["_seniority_norm"].unique():
        level_jobs = matched[matched["_seniority_norm"] == level]
        with_salary = level_jobs["_parsed_salary"].dropna()
        if len(with_salary) > 0:
            salary_by_seniority[level] = {
                "avg": float(with_salary.mean()),
                "median": float(with_salary.median()),
                "n_with_salary": int(len(with_salary)),
                "n_total": int(len(level_jobs)),
            }
    result["salary_by_seniority"] = salary_by_seniority

    cleaned_desc = matched["description_text"].fillna("").apply(clean_text)
    cleaned_title = matched["title"].fillna("").apply(clean_text)
    corpus_lower = (cleaned_desc + " " + cleaned_title).str.lower()

    for category, terms in keyword_categories.items():
        hits = {}
        for term in terms:
            count = count_keyword_per_job(corpus_lower, term)
            if count > 0:
                hits[term] = count
        if hits:
            result["keyword_hits"][category] = dict(sorted(hits.items(), key=lambda x: -x[1]))

    result["discovered_phrases"] = discover_phrases(
        cleaned_desc, cleaned_title, matched["company"], len(matched)
    )

    return result


def print_function_report(result: dict) -> None:
    print(f"\n{'='*70}")
    print(f"JOB FUNCTION: {result['function']}")
    print(f"{'='*70}")
    print(f"Jobs matched: {result['total_jobs']}")

    if result["total_jobs"] == 0:
        print("  No jobs matched this function's title terms.")
        return

    print(f"\n{'-'*70}")
    print("SENIORITY BREAKDOWN")
    print(f"{'-'*70}")
    for level, count in result["seniority"].most_common():
        pct = count / result["total_jobs"] * 100
        bar = "#" * int(pct / 3)
        print(f"  {level:<15} {count:>5}  {bar} {pct:.1f}%")

    if result["salary_by_seniority"]:
        print(f"\n{'-'*70}")
        print("AVG SALARY BY SENIORITY (annualized; hourly rates converted)")
        print("Only jobs with parseable salary data — coverage shown per level.")
        print(f"{'-'*70}")
        # Order by typical career progression where possible, unknowns last
        ordered_levels = [l for l in SENIORITY_ORDER if l in result["salary_by_seniority"]]
        remaining = [l for l in result["salary_by_seniority"] if l not in ordered_levels]
        for level in ordered_levels + sorted(remaining):
            stats = result["salary_by_seniority"][level]
            coverage_pct = stats["n_with_salary"] / stats["n_total"] * 100
            print(f"  {level:<15} avg ${stats['avg']:>10,.0f}   median ${stats['median']:>10,.0f}   "
                  f"({stats['n_with_salary']}/{stats['n_total']} jobs, {coverage_pct:.0f}% had salary data)")

    print(f"\n{'-'*70}")
    print("TOP COMPANIES HIRING FOR THIS FUNCTION")
    print(f"{'-'*70}")
    for company, count in result["companies"].most_common(10):
        print(f"  {company:<40} {count:>5}")

    if result["keyword_hits"]:
        print(f"\n{'-'*70}")
        print("KEYWORD FREQUENCY (from your keywords/ files, scoped to this function)")
        print("Counted per job - a job mentioning a term 5x still counts once.")
        print(f"{'-'*70}")
        for category, hits in result["keyword_hits"].items():
            print(f"\n  [{category}]")
            for term, count in list(hits.items())[:15]:
                pct = count / result["total_jobs"] * 100
                bar = "#" * int(pct / 3)
                print(f"    {term:<35} {count:>5}  {bar} {pct:.1f}%")

    if result["discovered_phrases"]:
        print(f"\n{'-'*70}")
        print("SKILL DISCOVERY - most common technical phrases in this function")
        print("(use this if you don't know what keywords to search for yet)")
        print("Company names and EEO/legal/benefits boilerplate excluded.")
        print(f"{'-'*70}")
        for phrase, count in result["discovered_phrases"]:
            pct = count / result["total_jobs"] * 100
            bar = "#" * int(pct / 3)
            print(f"    {phrase:<40} {count:>5}  {bar} {pct:.1f}%")
    else:
        print(f"\n{'-'*70}")
        print("SKILL DISCOVERY - no phrases cleared the noise floor (3+ jobs)")
        print(f"{'-'*70}")


def export_results(results: list, export_path: Path) -> None:
    rows = []
    for r in results:
        for level, stats in r["salary_by_seniority"].items():
            rows.append({
                "function": r["function"],
                "total_jobs_in_function": r["total_jobs"],
                "source": "salary_by_seniority",
                "category": level,
                "term": "avg_annual_salary",
                "count": round(stats["avg"]),
                "pct_of_function": round(stats["n_with_salary"] / stats["n_total"] * 100, 1),
            })
            rows.append({
                "function": r["function"],
                "total_jobs_in_function": r["total_jobs"],
                "source": "salary_by_seniority",
                "category": level,
                "term": "median_annual_salary",
                "count": round(stats["median"]),
                "pct_of_function": round(stats["n_with_salary"] / stats["n_total"] * 100, 1),
            })
        for category, hits in r["keyword_hits"].items():
            for term, count in hits.items():
                rows.append({
                    "function": r["function"],
                    "total_jobs_in_function": r["total_jobs"],
                    "source": "keyword_list",
                    "category": category,
                    "term": term,
                    "count": count,
                    "pct_of_function": round(count / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
                })
        for phrase, count in r["discovered_phrases"]:
            rows.append({
                "function": r["function"],
                "total_jobs_in_function": r["total_jobs"],
                "source": "discovered_phrase",
                "category": "",
                "term": phrase,
                "count": count,
                "pct_of_function": round(count / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
    out_df = pd.DataFrame(rows)
    out_df.to_csv(export_path, index=False)
    print(f"\nResults exported -> {export_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Job-function-scoped skill frequency analysis for job seekers"
    )
    parser.add_argument("--input", type=Path, default=Path("data/master_dataset.csv"))
    parser.add_argument("--job-functions-dir", type=Path, default=Path("job_functions"))
    parser.add_argument("--keywords-dir", type=Path, default=Path("keywords"))
    parser.add_argument("--function", type=str, default=None,
                        help="Run only one job function (filename stem, e.g. 'cybersecurity'). Default: all.")
    parser.add_argument("--export", type=Path, default=None,
                        help="Export full results to CSV")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input file not found: {args.input}")
        return

    print(f"Loading {args.input}...")
    df = pd.read_csv(args.input, low_memory=False)
    print(f"  {len(df):,} jobs loaded")

    job_functions = load_job_functions(args.job_functions_dir)
    if not job_functions:
        print(f"\nNo job function files found in {args.job_functions_dir}/")
        print("Create one file per job function, e.g.:")
        print(f"  {args.job_functions_dir}/cybersecurity.txt")
        print(f"  {args.job_functions_dir}/program_management.txt")
        print("One title-matching term per line. See keywords/ for the format.")
        return

    keyword_categories = load_keywords(args.keywords_dir)

    print(f"\nLoaded {len(job_functions)} job function(s): {', '.join(job_functions.keys())}")
    print(f"Loaded {len(keyword_categories)} keyword categor(y/ies) to scope: {', '.join(keyword_categories.keys())}")

    targets = (
        {args.function: job_functions[args.function]}
        if args.function and args.function in job_functions
        else job_functions
    )

    results = []
    for function_name, title_terms in targets.items():
        result = analyze_function(df, function_name, title_terms, keyword_categories)
        results.append(result)
        print_function_report(result)

    if args.export:
        export_results(results, args.export)


if __name__ == "__main__":
    main()
