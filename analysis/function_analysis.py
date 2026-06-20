"""
function_analysis.py - Job-function-level skill DISCOVERY for job seekers.

This script is discovery-only. It does NOT score jobs against pre-built
keyword files (governance.txt, capability.txt, certification_adjacent.txt,
cybersecurity_skills.txt) - that hypothesis-testing job belongs to
keyword_analysis.py, scoped to the whole dataset, for the Article B thesis.

This script:
  1. Prompts you (interactively, no file editing required) for the job
     titles that define your function - e.g. "program manager, project
     manager" - matched with fuzzy, token-based matching (word order and
     small typos don't matter). What you type is optionally saved to
     job_functions/<name>.txt so next time you can skip the prompt by
     passing --function <name>.
  2. Optionally prompts for your own ad hoc keywords to check (skippable -
     press enter for none). This is YOUR hypothesis, checked fresh each
     run, separate from discovery and separate from Article B's governance
     keyword files. Reported in its own "USER KEYWORD CHECK" section.
  3. Within the matched bucket, reports:
       - total jobs matched, seniority breakdown, salary by seniority
       - top companies hiring for this function
       - your ad hoc keyword hits, if you entered any
       - SKILL DISCOVERY, split two ways:
           TITLE SIGNAL    - phrases found in job TITLES. Smaller volume,
                              higher confidence - this is what a company
                              chose to lead with. Clean: company names,
                              EEO/legal boilerplate, and ATS template
                              artifacts are excluded.
           BODY SIGNAL     - phrases found in description text. Larger
                              volume, lower confidence per hit - this is
                              what shows up once you read the fine print.
                              Same exclusions applied, but every company
                              phrases its own EEO/benefits/legal disclaimers
                              differently, so some boilerplate fragments
                              still slip through. Treat this list as a
                              starting point for discovery, not a verified
                              clean feed - read each phrase with judgment.

A job can match more than one function bucket (e.g. "Cybersecurity Program
Manager" matches both cybersecurity and program_management terms). This is
intentional - crossover roles are informative, not noise to be removed.

Usage:
    python3 analysis/function_analysis.py
        (prompts you for everything - job function, optional keywords)
    python3 analysis/function_analysis.py --function cybersecurity
        (skips the job-function prompt, reuses job_functions/cybersecurity.txt;
         still prompts for optional ad hoc keywords unless --no-prompt-keywords)
    python3 analysis/function_analysis.py --function cybersecurity --no-prompt-keywords
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
    "dental", "vision", "posting", "date", "end", "education", "training",
    "limited", "factors", "total", "package", "obtain", "maintain", "long",
    "term", "considerations", "internal", "external", "unit", "leave",
    "parental", "acquisition", "talent", "schedule", "cost", "potential",
    "risks", "risk", "wage", "wages", "hourly", "annual", "annually",
    "eligibility", "eligible", "enroll", "enrollment", "401", "k", "match",
    "matching", "stock", "equity", "rsu", "rsus", "esop", "holiday", "holidays",
    "sick", "disability", "fmla", "cobra", "hsa", "fsa", "dependent",
    "dependents", "spouse", "domestic", "summary", "overview", "description",
    "posted", "post", "expires", "expiration", "req", "requisition",
    "scams", "scam", "protecting", "yourself", "recruitment", "range",
    "estimate", "estimated", "disclosed", "adjusted", "geographic",
    "differential", "century", "21st", "innovative", "flexible",
    "arrangements", "stimulate", "foster", "fostering", "thinking",
    "explore", "learn", "more", "information", "contact", "reach",
    "wherever", "possible", "easily", "changing", "designed", "bringing",
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

    if is_hourly or avg_midpoint < 100:
        avg_midpoint = avg_midpoint * 2080

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


def count_keyword_per_job(corpus_lower: pd.Series, term: str) -> int:
    escaped = re.escape(term.lower())
    pattern = r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])"
    return int(corpus_lower.str.contains(pattern, regex=True, na=False).sum())


def discover_phrases(text_series: pd.Series, companies: pd.Series,
                      total_jobs: int, top_n: int = 25) -> list:
    company_tokens = set()
    for name in companies.dropna().unique():
        company_tokens.update(_tokenize(str(name)))
        company_tokens.add(re.sub(r"[^a-z0-9]", "", str(name).lower()))

    exclude = ALL_STOPWORDS | company_tokens | {"com", "www", "http", "https"}

    phrase_job_counts = Counter()

    for text in text_series.fillna(""):
        tokens = _tokenize(str(text))
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
                      user_keywords: list) -> dict:
    mask = df["title"].fillna("").apply(
        lambda title: any(title_matches_term(title, term) for term in title_terms)
    )
    matched = df[mask].copy()

    result = {
        "function": function_name,
        "total_jobs": len(matched),
        "seniority": Counter(),
        "user_keyword_hits": {},
        "title_phrases": [],
        "body_phrases": [],
        "companies": Counter(),
        "salary_by_seniority": {},
    }

    if len(matched) == 0:
        return result

    seniority_norm = matched["seniority"].fillna("Unknown").str.strip().str.title()
    result["seniority"] = Counter(seniority_norm)
    result["companies"] = Counter(matched["company"])

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

    if user_keywords:
        corpus_lower = (cleaned_desc + " " + cleaned_title).str.lower()
        hits = {}
        for term in user_keywords:
            count = count_keyword_per_job(corpus_lower, term)
            if count > 0:
                hits[term] = count
        result["user_keyword_hits"] = dict(sorted(hits.items(), key=lambda x: -x[1]))

    result["title_phrases"] = discover_phrases(
        cleaned_title, matched["company"], len(matched)
    )
    result["body_phrases"] = discover_phrases(
        cleaned_desc, matched["company"], len(matched)
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
        print("Only jobs with parseable salary data - coverage shown per level.")
        print(f"{'-'*70}")
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

    if result["user_keyword_hits"]:
        print(f"\n{'-'*70}")
        print("USER KEYWORD CHECK (your own search terms, this run only)")
        print("Counted per job - a job mentioning a term 5x still counts once.")
        print(f"{'-'*70}")
        for term, count in result["user_keyword_hits"].items():
            pct = count / result["total_jobs"] * 100
            bar = "#" * int(pct / 3)
            print(f"    {term:<35} {count:>5}  {bar} {pct:.1f}%")

    print(f"\n{'-'*70}")
    print("SKILL DISCOVERY - TITLE SIGNAL")
    print("Phrases found in job titles. Lower volume, higher confidence -")
    print("this is what companies chose to lead with.")
    print(f"{'-'*70}")
    if result["title_phrases"]:
        for phrase, count in result["title_phrases"]:
            pct = count / result["total_jobs"] * 100
            bar = "#" * int(pct / 3)
            print(f"    {phrase:<40} {count:>5}  {bar} {pct:.1f}%")
    else:
        print("    No phrases cleared the noise floor (3+ jobs) in titles alone.")

    print(f"\n{'-'*70}")
    print("SKILL DISCOVERY - BODY SIGNAL")
    print("Phrases found in description text. Higher volume, lower confidence")
    print("per hit - this is what shows up once you read the fine print.")
    print("(use this if you don't know what keywords to search for yet)")
    print("Known limitation: every company phrases EEO/benefits/legal disclaimers")
    print("differently, so some boilerplate fragments may still slip through.")
    print("Read each phrase below and use judgment - this list is a starting")
    print("point for discovery, not a verified clean feed.")
    print(f"{'-'*70}")
    if result["body_phrases"]:
        for phrase, count in result["body_phrases"]:
            pct = count / result["total_jobs"] * 100
            bar = "#" * int(pct / 3)
            print(f"    {phrase:<40} {count:>5}  {bar} {pct:.1f}%")
    else:
        print("    No phrases cleared the noise floor (3+ jobs).")


def export_results(results: list, export_path: Path) -> None:
    rows = []
    for r in results:
        for level, stats in r["salary_by_seniority"].items():
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "salary_by_seniority", "category": level,
                "term": "avg_annual_salary", "count": round(stats["avg"]),
                "pct_of_function": round(stats["n_with_salary"] / stats["n_total"] * 100, 1),
            })
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "salary_by_seniority", "category": level,
                "term": "median_annual_salary", "count": round(stats["median"]),
                "pct_of_function": round(stats["n_with_salary"] / stats["n_total"] * 100, 1),
            })
        for term, count in r["user_keyword_hits"].items():
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "user_keyword", "category": "",
                "term": term, "count": count,
                "pct_of_function": round(count / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for phrase, count in r["title_phrases"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "discovered_phrase_title", "category": "",
                "term": phrase, "count": count,
                "pct_of_function": round(count / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for phrase, count in r["body_phrases"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "discovered_phrase_body", "category": "",
                "term": phrase, "count": count,
                "pct_of_function": round(count / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
    out_df = pd.DataFrame(rows)
    out_df.to_csv(export_path, index=False)
    print(f"\nResults exported -> {export_path}")


def prompt_for_job_function(job_functions_dir: Path, existing: dict) -> tuple:
    if existing:
        print("\nSaved job functions you can reuse: " + ", ".join(existing.keys()))
    print("\nEnter the job titles that define the role you're researching.")
    print("Comma-separated, e.g.: program manager, project manager, technical program manager")
    raw = input("Titles: ").strip()
    if not raw:
        return None, []

    terms = [t.strip().lower() for t in raw.split(",") if t.strip()]

    name = input("Save this as a job function for next time? Enter a short name, or leave blank to skip: ").strip()
    if name:
        name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
        job_functions_dir.mkdir(parents=True, exist_ok=True)
        out_path = job_functions_dir / f"{name}.txt"
        out_path.write_text("\n".join(terms) + "\n", encoding="utf-8")
        print(f"Saved -> {out_path}. Next time: --function {name}")
    else:
        name = "custom_" + re.sub(r"[^a-z0-9_]+", "_", terms[0])

    return name, terms


def prompt_for_user_keywords() -> list:
    print("\nWant to check for any specific keywords of your own? (your hypothesis,")
    print("checked fresh this run - separate from the discovery lists above)")
    raw = input("Keywords, comma-separated, or press enter to skip: ").strip()
    if not raw:
        return []
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def main():
    parser = argparse.ArgumentParser(
        description="Job-function-scoped skill DISCOVERY for job seekers (no pre-built keyword files required)"
    )
    parser.add_argument("--input", type=Path, default=Path("data/master_dataset.csv"))
    parser.add_argument("--job-functions-dir", type=Path, default=Path("job_functions"))
    parser.add_argument("--function", type=str, default=None,
                        help="Reuse a saved job function (filename stem in job_functions/). Skips the title prompt.")
    parser.add_argument("--keywords", type=str, default=None,
                        help="Comma-separated ad hoc keywords to check, non-interactively.")
    parser.add_argument("--no-prompt-keywords", action="store_true",
                        help="Skip the ad hoc keyword prompt entirely (discovery only).")
    parser.add_argument("--export", type=Path, default=None,
                        help="Export full results to CSV")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input file not found: {args.input}")
        return

    print(f"Loading {args.input}...")
    df = pd.read_csv(args.input, low_memory=False)
    print(f"  {len(df):,} jobs loaded")

    existing_functions = load_job_functions(args.job_functions_dir)

    if args.function and args.function in existing_functions:
        function_name = args.function
        title_terms = existing_functions[args.function]
        print(f"\nUsing saved job function '{function_name}': {', '.join(title_terms)}")
    elif args.function:
        print(f"\nNo saved job function named '{args.function}' found in {args.job_functions_dir}/.")
        function_name, title_terms = prompt_for_job_function(args.job_functions_dir, existing_functions)
    else:
        function_name, title_terms = prompt_for_job_function(args.job_functions_dir, existing_functions)

    if not title_terms:
        print("No job titles entered. Nothing to analyze.")
        return

    if args.keywords is not None:
        user_keywords = [t.strip().lower() for t in args.keywords.split(",") if t.strip()]
    elif args.no_prompt_keywords:
        user_keywords = []
    else:
        user_keywords = prompt_for_user_keywords()

    result = analyze_function(df, function_name, title_terms, user_keywords)
    print_function_report(result)

    if args.export:
        export_results([result], args.export)


if __name__ == "__main__":
    main()
