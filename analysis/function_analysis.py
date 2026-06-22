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
     small typos don't matter, but distinct words like "project" vs
     "product" are NOT treated as typos of each other - see _tokens_match).
     What you type is optionally saved to job_functions/<name>.txt so next
     time you can skip the prompt by passing --function <name>. Pass
     --label "Display Name" for a clean section header.
  2. Optionally prompts for your own ad hoc keywords to check (skippable -
     press enter for none). This is YOUR hypothesis, checked fresh each
     run, separate from discovery and separate from Article B's governance
     keyword files. Reported in its own "USER KEYWORD CHECK" section.
  3. Within the matched bucket, reports:
       - total jobs matched / total jobs in dataset (with %)
       - seniority breakdown (% of jobs matched in this function)
       - salary by seniority, US POSTINGS ONLY, in USD. Non-US postings
         (Canada, UK, Europe, India, Australia, Germany, etc. - this
         dataset mixes country labels, e.g. both "United States of
         America" and "US" appear, both are treated as US) are excluded
         from salary math rather than silently averaged in. Excluded
         count is shown so nothing is hidden.
       - top companies hiring for this function
       - your ad hoc keyword hits, if any
       - CERTIFICATIONS - small maintained list of real, nameable certs
         (PMP, CISSP, Six Sigma, etc - see CERTIFICATIONS list below).
         Count, %, top companies per cert.
       - SECURITY CLEARANCES - small maintained list of real US clearance
         tiers (Top Secret, TS/SCI, Secret, Public Trust, etc - see
         CLEARANCES list below). Count, %, top companies per tier.
       - TOOLS & SOFTWARE - matched against the O*NET Software Skills
         database (31,821 total rows across all 923 O*NET occupations,
         filtered down to Hot Technology=Y only - see load_onet_tools.
         Unfiltered, the list includes thousands of generic category
         descriptions like "Database reporting software" or "Security
         testing software" that aren't real product names and were
         dominating results with 95%+ false match rates in testing.
         Filtering to Hot=Y trades some completeness (a real but obscure
         or older tool may lack the Hot flag) for much higher precision.
         CC BY 4.0, U.S. Dept. of Labor / O*NET - see
         https://www.onetcenter.org/database.html). The full filtered
         list is used regardless of which occupation O*NET ties a tool
         to - function-bucket scoping (you've already filtered to your
         job titles) does the real filtering, not the O*NET occupation
         code. Matching reuses the same fuzzy token logic as job-function
         title matching (_tokens_match), but with a looser rule: ANY
         core token match counts (not ALL, unlike job-function matching),
         so a posting saying just "Adobe" still matches "Adobe Acrobat" -
         at a lower reported match strength. Match strength (1.0 = every
         core token found, <1.0 = partial/brand-only) is shown so you can
         judge confidence yourself. Generic words ("software", "Inc",
         "Corp", "Corporation", "Systems") are stripped from tool names
         before matching so they don't inflate scores.

Requires data/reference/onet_software_skills.txt to exist (tab-delimited,
as downloaded from O*NET - see SKILL.md or project handoff for the exact
curl command). If missing, the Tools & Software section is skipped with
a message telling you how to get it, rather than failing.

A job can match more than one function bucket (e.g. "Cybersecurity Program
Manager" matches both cybersecurity and program_management terms). This is
intentional - crossover roles are informative, not noise to be removed.

Usage:
    python3 analysis/function_analysis.py
        (prompts you for everything - job function, optional keywords)
    python3 analysis/function_analysis.py --function cybersecurity
        (skips the job-function prompt, reuses job_functions/cybersecurity.txt;
         still prompts for optional ad hoc keywords unless --no-prompt-keywords)
    python3 analysis/function_analysis.py --function program_management --label "Product Management"
        (clean display name in the report header instead of the saved filename)
    python3 analysis/function_analysis.py --function cybersecurity --no-prompt-keywords
    python3 analysis/function_analysis.py --input data/master_dataset.csv --export data/function_results.csv
"""

import argparse
import html
import re
from collections import Counter, defaultdict
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

ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "as", "at", "by", "this", "that", "from", "will",
    "you", "your", "we", "our", "have", "has", "may", "can", "all", "more",
    "their", "they", "it", "its", "than", "into", "if", "not", "but", "who",
    "what", "when", "where", "how", "which", "while", "during", "between",
}

# Generic words stripped from O*NET tool names before matching, so
# "SAP software" reduces to just {sap} and doesn't require literally
# matching the word "software" too.
GENERIC_TOOL_WORDS = {
    "software", "inc", "corp", "corporation", "systems", "system",
    "technologies", "technology", "solutions", "solution", "company",
    "the",
}

SENIORITY_ORDER = ["Junior", "Mid", "Senior", "Lead", "Principal", "Manager", "Director"]

# US country labels are inconsistent in this dataset - both full name and
# abbreviation appear. Both are treated as US for salary scoping.
US_COUNTRY_LABELS = {"United States of America", "US"}

# Small, maintained, real-world lists - not discovered, because both
# certifications and clearances are closed/nameable vocabularies where a
# maintained list is more reliable than open-ended phrase discovery
# (see project notes on why bigram discovery fragments proper nouns).
CERTIFICATIONS = [
    "PMP", "CAPM", "PgMP", "PMI-ACP", "CSM", "CSPO", "Six Sigma",
    "Lean Six Sigma", "CISSP", "CISM", "CISA", "Security+", "CompTIA Security+",
    "ITIL", "PE", "CCNA", "AWS Certified", "Scrum Master", "Agile Certified",
    "A&P License", "FAA Certificate", "DAWIA",
]

CLEARANCES = [
    "Top Secret", "TS/SCI", "TS SCI", "Secret Clearance", "Secret",
    "Public Trust", "Confidential Clearance", "Interim Clearance",
    "Security Clearance", "SSBI", "Polygraph",
]


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
    if len(term_token) < 4 or len(title_token) < 4:
        return False
    # Distance capped at 1 regardless of word length. Distance 2 was
    # matching genuinely different words as typos of each other - e.g.
    # "project" vs "product" sits at distance 2, which caused every
    # Project Manager posting to match a "product manager" search. Real
    # typos (enginer/engineer, manger/manager) sit at distance 1.
    #
    # Both sides must clear the length-4 minimum, not just term_token -
    # a 3-letter title token like "ISS" (International Space Station)
    # was fuzzy-matching 4-letter terms like "isso"/"issm"/"isse" at
    # distance 1, causing an unrelated RF Communications title to match
    # three different cybersecurity acronym searches. Short tokens on
    # either side now require an exact match, no fuzzy tolerance.
    return _levenshtein(title_token, term_token) <= 1


def title_matches_term(title: str, term: str) -> bool:
    """ALL core tokens of `term` must fuzzy-match somewhere in `title`.
    Used for job-function matching, where precision matters more than
    recall - we want "program manager" to match titles, not just any
    title containing the word "manager"."""
    title_tokens = _tokenize(title)
    term_tokens = _tokenize(term)
    if not term_tokens:
        return False
    for term_tok in term_tokens:
        if not any(_tokens_match(tt, term_tok) for tt in title_tokens):
            return False
    return True


def _distinctive_tokens_from_original(name: str) -> list:
    """Identifies which tokens in a tool name are actually distinctive
    (acronym or brand-like), using the ORIGINAL case of the name rather
    than the lowercased version. This replaces an earlier stopword-list
    approach (COMMON_WORDS_NOT_SIGNAL) that tried to blacklist ordinary
    English words one at a time - that approach is fundamentally
    unwinnable against O*NET's verbose descriptive naming convention,
    where obscure tools have names like "Reactor excursion and release
    analysis program RELAP". No finite exclusion list covers every
    possible descriptive word O*NET might use, and testing confirmed
    these names were matching 95%+ of jobs because common words like
    "analysis" and "program" were never explicitly excluded.

    The actual reliable signal: a token is distinctive if, in the
    ORIGINAL (non-lowercased) name, it is either (a) short and entirely
    uppercase - an acronym like RELAP, SAP, JIRA, SPICE - or (b) a
    capitalized word that is NOT a common English/business word (e.g.
    "Adobe", "Atlassian", "MathWorks" pass; "Analysis", "Program",
    "System" fail even though they're capitalized, because they're
    ordinary words that happen to start a sentence/title-case phrase).
    """
    raw_words = re.findall(r"[A-Za-z0-9]+", name)
    distinctive = []
    for w in raw_words:
        lw = w.lower()
        if lw in GENERIC_TOOL_WORDS or lw in ENGLISH_STOPWORDS or lw in JOB_POSTING_STOPWORDS:
            continue
        # All-caps acronym (2-6 letters) - RELAP, SAP, JIRA, SPICE, NX
        if w.isupper() and 2 <= len(w) <= 8:
            distinctive.append(lw)
            continue
        # Capitalized word that isn't a generic English business word -
        # treat as a likely brand name (Adobe, Atlassian, MathWorks,
        # Autodesk). Reject ordinary capitalized words from title-case
        # descriptive phrases (Analysis, Program, Reactor, Release).
        if w[0].isupper() and lw not in ORDINARY_CAPITALIZED_WORDS:
            distinctive.append(lw)
    return distinctive


# Ordinary words that frequently appear capitalized in O*NET's
# descriptive/title-case tool names but are NOT brand-distinctive on
# their own - without this, "Analysis", "Program", "System", "Reactor"
# etc. would be wrongly treated as brand names just for being capitalized
# at the start of a title-case phrase.
ORDINARY_CAPITALIZED_WORDS = {
    "analysis", "program", "system", "systems", "software", "application",
    "applications", "management", "reporting", "report", "tracking",
    "tracker", "design", "development", "planning", "scheduling",
    "database", "network", "security", "service", "services", "data",
    "process", "processing", "control", "controls", "model", "modeling",
    "simulation", "simulator", "reactor", "release", "excursion",
    "emergency", "response", "operations", "operation", "record",
    "records", "information", "communication", "communications",
    "education", "training", "consortium", "research", "institute",
    "national", "international", "center", "centers", "agency",
    "organization", "department", "division", "bureau", "office",
    "administration", "standard", "standards", "assessment", "display",
    "monitoring", "evaluation", "documentation", "library", "resource",
    "resources", "toolbox", "tool", "tools", "framework", "suite",
    "package", "platform", "solution", "solutions", "technology",
    "technologies", "engineering", "manufacturing", "construction",
    "accounting", "financial", "medical", "health", "environment",
    "environmental", "quality", "safety", "compliance", "regulatory",
    "consortium", "interuniversity", "occupational", "conservation",
    "atlas", "wind", "circuit", "integrated", "hierarchical",
    "emphasis", "mapping", "disease", "with", "and", "for", "of", "the",
    "project", "manager", "manage", "managing", "team", "office",
    "access", "exchange", "word", "excel", "outlook", "publisher",
    "laboratory", "laboratories", "university", "college", "academy",
    "foundation", "society", "association", "federation", "union",
}


def _build_phrase_pattern(name_tokens: list):
    """Compiles the adjacent-phrase regex once per tool name, not once
    per job. Returns a compiled pattern object."""
    pattern = r"\b" + r"(?:\W+\w+){0,2}\W+".join(re.escape(t) for t in name_tokens) + r"\b"
    return re.compile(pattern)


def match_strength_precomputed(text_lower: str, text_tokens: set,
                                name_tokens: list, compiled_phrase,
                                distinctive_tokens: list) -> float:
    """Same two-tier logic as before, but takes pre-tokenized/pre-lowered
    job text, a pre-compiled phrase pattern, and pre-computed distinctive
    tokens (see _distinctive_tokens_from_original), so all expensive/
    one-time work happens upstream in onet_tool_lookup.

    Tier 1 (1.0): full phrase found, word order respected.
    Tier 2 (0.5): at least one genuinely distinctive token (acronym or
    real brand word, NOT just any non-stopword) is present. distinctive_
    tokens is now computed from the tool's ORIGINAL capitalization, not
    from a stopword blacklist - see _distinctive_tokens_from_original
    for why the blacklist approach failed on O*NET's verbose names."""
    if compiled_phrase.search(text_lower):
        return 1.0
    if not distinctive_tokens:
        return 0.0
    for t in distinctive_tokens:
        if t in text_tokens:
            return 0.5
    return 0.0


def partial_match_strength(full_text: str, name: str) -> tuple:
    """Single-pair convenience wrapper (used by tests / one-off checks).
    For bulk lookups across many jobs x many tools, use
    match_strength_precomputed via onet_tool_lookup instead - this
    version recomputes tokenization, regex compilation, and distinctive-
    token detection every call, fine for one pair but wasteful at scale."""
    name_tokens = [t for t in _tokenize(name) if t not in GENERIC_TOOL_WORDS]
    if not name_tokens:
        return 0.0, []
    text_lower = full_text.lower()
    compiled = _build_phrase_pattern(name_tokens)
    if compiled.search(text_lower):
        return 1.0, name_tokens
    distinctive = _distinctive_tokens_from_original(name)
    if not distinctive:
        return 0.0, []
    text_tokens = set(_tokenize(full_text))
    matched = [t for t in distinctive if t in text_tokens]
    if matched:
        return 0.5, matched
    return 0.0, []


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


def load_onet_tools(path: Path) -> list:
    """Loads the O*NET Software Skills file (tab-delimited). Returns a
    deduplicated list of distinct "Workplace Example" tool/software names
    (the proper-noun column), regardless of which O*NET occupation they're
    tied to - function-bucket scoping already filters the job postings,
    so filtering the tool list by occupation code would be redundant and
    risks excluding a tool just because O*NET happened to tag it under an
    occupation outside your function bucket's exact title match."""
    if not path.exists():
        return []
    names = set()
    skipped_not_hot = 0
    with path.open(encoding="utf-8") as f:
        header = f.readline()
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5 or not parts[1].strip():
                continue
            workplace_example = parts[1].strip()
            hot_technology = parts[4].strip()
            if hot_technology == "Y":
                names.add(workplace_example)
            else:
                skipped_not_hot += 1
    if skipped_not_hot:
        print(f"  (filtered to Hot Technology=Y only - excluded {skipped_not_hot:,} non-hot rows, "
              f"including most generic category names like 'Database reporting software')")
    return sorted(names)


def count_keyword_per_job(corpus_lower: pd.Series, term: str) -> int:
    escaped = re.escape(term.lower())
    pattern = r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])"
    return int(corpus_lower.str.contains(pattern, regex=True, na=False).sum())


def named_list_lookup(matched: pd.DataFrame, cleaned_corpus: pd.Series,
                       names: list, top_n_companies: int = 3) -> list:
    """For a small maintained list (certifications, clearances): exact
    word-boundary phrase match per job, with top companies per hit.
    Returns list of dicts sorted by count descending, including zero-count
    entries (a real "not found" result is informative)."""
    results = []
    corpus_lower = cleaned_corpus.str.lower()
    for name in names:
        pattern = r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9])"
        mask = corpus_lower.str.contains(pattern, regex=True, na=False)
        count = int(mask.sum())
        companies = Counter(matched.loc[mask, "company"]).most_common(top_n_companies) if count else []
        results.append({"name": name, "count": count, "companies": companies})
    results.sort(key=lambda r: -r["count"])
    return results


def _build_tool_index(onet_names: list) -> dict:
    """Inverted index: distinctive_token -> set of tool names. Lets us
    find which tools are even POSSIBLE candidates for a job by looking
    up the job's own tokens against this index (cheap dict lookups),
    instead of testing every one of 8,753 tool patterns against every
    job's raw text.

    Indexed by DISTINCTIVE tokens only (see
    _distinctive_tokens_from_original) - not every non-generic token.
    Earlier versions indexed by all non-generic tokens, which meant
    ordinary descriptive words inside long O*NET names (e.g. "analysis",
    "program", "reactor" in "Reactor excursion and release analysis
    program RELAP") were treated as index keys and, combined with a
    flawed distinctiveness check downstream, caused those obscure tools
    to match 95%+ of jobs. Indexing by distinctive tokens only means a
    tool like RELAP has exactly one index entry ("relap"), so it only
    becomes a candidate for jobs that actually mention "relap" - which
    in an aerospace PM dataset is correctly almost never.

    This is also the fix for the multi-minute runtime: testing showed
    that running thousands of compiled regex patterns against every
    job's raw text was O(tools x text_length); the index inverts this so
    only realistic candidates (tools whose distinctive token appears in
    that job) are ever checked."""
    index = defaultdict(set)
    name_token_map = {}
    name_distinctive_map = {}
    for name in onet_names:
        name_tokens = tuple(t for t in _tokenize(name) if t not in GENERIC_TOOL_WORDS)
        if not name_tokens:
            continue
        distinctive = _distinctive_tokens_from_original(name)
        if not distinctive:
            continue  # no real signal in this name at all - skip entirely
        name_token_map[name] = name_tokens
        name_distinctive_map[name] = distinctive
        for tok in set(distinctive):
            index[tok].add(name)
    return index, name_token_map, name_distinctive_map


def onet_tool_lookup(matched: pd.DataFrame, cleaned_corpus: pd.Series,
                      onet_names: list, top_n_companies: int = 3,
                      min_strength: float = 0.5) -> list:
    """For each O*NET tool name, computes per-job match strength (1.0 =
    exact adjacent phrase, 0.5 = a distinctive non-generic token present,
    0.0 = no match - see match_strength_precomputed), keeps jobs at or
    above min_strength, reports count/percent/avg strength/top companies.

    Uses an inverted token index (_build_tool_index) so only tools whose
    tokens actually appear in a given job are ever checked against that
    job - see _build_tool_index's docstring for why this was necessary
    (brute-force regex-per-tool was ~24 minutes at full O*NET scale;
    indexed approach is ~1 second).
    """
    texts = cleaned_corpus.fillna("").tolist()
    job_data = []
    for text in texts:
        if not text:
            job_data.append(None)
            continue
        job_data.append((text.lower(), set(_tokenize(text))))

    tool_index, name_token_map, name_distinctive_map = _build_tool_index(onet_names)
    phrase_cache = {}  # compiled regex per name, built lazily, reused across jobs

    name_hits = defaultdict(list)  # name -> list of (job_idx, strength)

    for idx, jd in enumerate(job_data):
        if jd is None:
            continue
        text_lower, text_tokens = jd

        candidates = set()
        for tok in text_tokens:
            if tok in tool_index:
                candidates.update(tool_index[tok])

        for name in candidates:
            name_tokens = name_token_map[name]
            distinctive = name_distinctive_map[name]
            if name not in phrase_cache:
                phrase_cache[name] = _build_phrase_pattern(list(name_tokens))
            compiled_phrase = phrase_cache[name]
            strength = match_strength_precomputed(text_lower, text_tokens, list(name_tokens),
                                                   compiled_phrase, distinctive)
            if strength >= min_strength:
                name_hits[name].append((idx, strength))

    results = []
    for name, hits in name_hits.items():
        hit_indices = [h[0] for h in hits]
        strengths = [h[1] for h in hits]
        companies = Counter(matched.iloc[hit_indices]["company"]).most_common(top_n_companies)
        results.append({
            "name": name,
            "count": len(hit_indices),
            "avg_strength": sum(strengths) / len(strengths),
            "companies": companies,
        })

    results.sort(key=lambda r: -r["count"])
    return results


def analyze_function(df: pd.DataFrame, function_name: str, title_terms: list,
                      user_keywords: list, onet_names: list) -> dict:
    total_dataset_jobs = len(df)

    mask = df["title"].fillna("").apply(
        lambda title: any(title_matches_term(title, term) for term in title_terms)
    )
    matched = df[mask].copy()

    result = {
        "function": function_name,
        "total_jobs": len(matched),
        "total_dataset_jobs": total_dataset_jobs,
        "seniority": Counter(),
        "user_keyword_hits": {},
        "certifications": [],
        "clearances": [],
        "tools": [],
        "companies": Counter(),
        "salary_by_seniority": {},
        "non_us_excluded_from_salary": 0,
    }

    if len(matched) == 0:
        return result

    seniority_norm = matched["seniority"].fillna("Unknown").str.strip().str.title()
    result["seniority"] = Counter(seniority_norm)
    result["companies"] = Counter(matched["company"])

    # --- Salary: US-only, USD. Two US labels exist in this dataset
    # ("United States of America" and "US") - both included. Non-US
    # postings are excluded from the math, not silently averaged in,
    # and the excluded count is reported. ---
    matched["_seniority_norm"] = seniority_norm
    matched["_parsed_salary"] = matched["salary"].apply(parse_salary_to_annual)
    is_us = matched["country"].isin(US_COUNTRY_LABELS)
    result["non_us_excluded_from_salary"] = int((~is_us & matched["_parsed_salary"].notna()).sum())
    us_matched = matched[is_us]

    salary_by_seniority = {}
    for level in us_matched["_seniority_norm"].unique():
        level_jobs = us_matched[us_matched["_seniority_norm"] == level]
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
    full_corpus = cleaned_desc + " " + cleaned_title

    # --- User ad hoc keyword check ---
    if user_keywords:
        corpus_lower = full_corpus.str.lower()
        hits = {}
        for term in user_keywords:
            count = count_keyword_per_job(corpus_lower, term)
            if count > 0:
                hits[term] = count
        result["user_keyword_hits"] = dict(sorted(hits.items(), key=lambda x: -x[1]))

    # --- Certifications & clearances: small maintained lists, exact
    # word-boundary match, zero-count entries kept (a real "not found"
    # result is informative). ---
    result["certifications"] = named_list_lookup(matched, full_corpus, CERTIFICATIONS)
    result["clearances"] = named_list_lookup(matched, full_corpus, CLEARANCES)

    # --- Tools & software: O*NET reference list, fuzzy partial match. ---
    if onet_names:
        result["tools"] = onet_tool_lookup(matched, full_corpus, onet_names)

    return result


# ---------------------------------------------------------------------------
# Salary parsing (unchanged from prior version)
# ---------------------------------------------------------------------------

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


def print_function_report(result: dict) -> None:
    total = result["total_jobs"]
    total_dataset = result["total_dataset_jobs"]
    pct_of_dataset = (total / total_dataset * 100) if total_dataset else 0

    print(f"\n{'='*70}")
    print(f"JOB FUNCTION: {result['function']}")
    print(f"{'='*70}")
    print(f"Jobs matched: {total:,} / {total_dataset:,} total in dataset ({pct_of_dataset:.1f}%)")

    if total == 0:
        print("  No jobs matched this function's title terms.")
        return

    print(f"\n{'-'*70}")
    print(f"SENIORITY BREAKDOWN (% of the {total:,} jobs matched in this function)")
    print("Bar scale: each # = 3 percentage points")
    print(f"{'-'*70}")
    for level, count in result["seniority"].most_common():
        pct = count / total * 100
        bar = "#" * min(int(pct / 3), 33)
        print(f"  {level:<15} {count:>5}  {bar} {pct:.1f}%")

    if result["salary_by_seniority"]:
        excluded = result["non_us_excluded_from_salary"]
        print(f"\n{'-'*70}")
        print("AVG SALARY BY SENIORITY - US POSTINGS ONLY, IN USD")
        print("Annualized; hourly rates converted at 2,080 hrs/year.")
        print("Only jobs with parseable salary data - coverage shown per level.")
        if excluded:
            print(f"({excluded} non-US postings with salary data excluded from this section)")
        print(f"{'-'*70}")
        ordered_levels = [l for l in SENIORITY_ORDER if l in result["salary_by_seniority"]]
        remaining = [l for l in result["salary_by_seniority"] if l not in ordered_levels]
        for level in ordered_levels + sorted(remaining):
            stats = result["salary_by_seniority"][level]
            coverage_pct = stats["n_with_salary"] / stats["n_total"] * 100
            print(f"  {level:<15} avg ${stats['avg']:>10,.0f}   median ${stats['median']:>10,.0f}   "
                  f"({stats['n_with_salary']}/{stats['n_total']} jobs, {coverage_pct:.0f}% had salary data)")
    else:
        print(f"\n{'-'*70}")
        print("AVG SALARY BY SENIORITY - no US postings with parseable salary data in this function.")
        print(f"{'-'*70}")

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
            pct = count / total * 100
            bar = "#" * min(int(pct / 3), 33)
            print(f"    {term:<35} {count:>5}  {bar} {pct:.1f}%")

    def print_named_list_section(title, subtitle, items):
        print(f"\n{'-'*70}")
        print(title)
        print(subtitle)
        print(f"{'-'*70}")
        if not items:
            print("    (no reference list loaded)")
            return
        for item in items:
            pct = item["count"] / total * 100 if total else 0
            bar = "#" * min(int(pct / 3), 33)
            companies_str = ", ".join(f"{c}: {n}" for c, n in item["companies"]) if item["companies"] else "not found"
            print(f"    {item['name']:<28} {item['count']:>5}  {bar} {pct:.1f}%   [{companies_str}]")

    print_named_list_section(
        "CERTIFICATIONS",
        "Counted per job. Zero-count entries kept - absence is also a finding.",
        result["certifications"],
    )

    print_named_list_section(
        "SECURITY CLEARANCES",
        "Counted per job. Zero-count entries kept - absence is also a finding.",
        result["clearances"],
    )

    print(f"\n{'-'*70}")
    print("TOOLS & SOFTWARE (source: O*NET Software Skills database, CC BY 4.0)")
    print("Match strength: 1.00 = full tool name found, <1.00 = partial/brand-only.")
    print("Minimum strength shown: 0.50. Top 3 companies per tool.")
    print(f"{'-'*70}")
    if not result["tools"]:
        print("    (no O*NET reference file loaded - see data/reference/onet_software_skills.txt)")
    else:
        for item in result["tools"][:30]:
            pct = item["count"] / total * 100 if total else 0
            bar = "#" * min(int(pct / 3), 33)
            companies_str = ", ".join(f"{c}: {n}" for c, n in item["companies"])
            print(f"    {item['name']:<35} {item['count']:>5}  {bar} {pct:.1f}%  "
                  f"(avg strength {item['avg_strength']:.2f})   [{companies_str}]")


def export_results(results: list, export_path: Path) -> None:
    rows = []
    for r in results:
        for level, stats in r["salary_by_seniority"].items():
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "salary_by_seniority_us_usd", "category": level,
                "term": "avg_annual_salary", "count": round(stats["avg"]),
                "pct_of_function": round(stats["n_with_salary"] / stats["n_total"] * 100, 1),
            })
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "salary_by_seniority_us_usd", "category": level,
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
        for item in r["certifications"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "certification", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["clearances"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "clearance", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["tools"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "tool_software_onet", "category": f"strength_{item['avg_strength']:.2f}",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
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
    print("checked fresh this run - separate from certifications/clearances/tools below)")
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
    parser.add_argument("--onet-file", type=Path, default=Path("data/reference/onet_software_skills.txt"))
    parser.add_argument("--function", type=str, default=None,
                        help="Reuse a saved job function (filename stem in job_functions/). Skips the title prompt.")
    parser.add_argument("--label", type=str, default=None,
                        help="Clean display name for the report header (e.g. 'Product Management'). "
                             "Defaults to the --function name if not set.")
    parser.add_argument("--keywords", type=str, default=None,
                        help="Comma-separated ad hoc keywords to check, non-interactively.")
    parser.add_argument("--no-prompt-keywords", action="store_true",
                        help="Skip the ad hoc keyword prompt entirely.")
    parser.add_argument("--export", type=Path, default=None,
                        help="Export full results to CSV")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input file not found: {args.input}")
        return

    print(f"Loading {args.input}...")
    df = pd.read_csv(args.input, low_memory=False)
    print(f"  {len(df):,} jobs loaded")

    onet_names = load_onet_tools(args.onet_file)
    if onet_names:
        print(f"  {len(onet_names):,} distinct tool/software names loaded from {args.onet_file}")
    else:
        print(f"  No O*NET reference file found at {args.onet_file} - Tools & Software section will be skipped.")
        print(f"  To enable it: curl -sL \"https://www.onetcenter.org/dl_files/database/db_30_3_text/Software%20Skills.txt\" -o {args.onet_file}")

    existing_functions = load_job_functions(args.job_functions_dir)

    if args.function and args.function in existing_functions:
        function_key = args.function
        title_terms = existing_functions[args.function]
        print(f"\nUsing saved job function '{function_key}': {', '.join(title_terms)}")
    elif args.function:
        print(f"\nNo saved job function named '{args.function}' found in {args.job_functions_dir}/.")
        function_key, title_terms = prompt_for_job_function(args.job_functions_dir, existing_functions)
    else:
        function_key, title_terms = prompt_for_job_function(args.job_functions_dir, existing_functions)

    if not title_terms:
        print("No job titles entered. Nothing to analyze.")
        return

    display_name = args.label if args.label else function_key

    if args.keywords is not None:
        user_keywords = [t.strip().lower() for t in args.keywords.split(",") if t.strip()]
    elif args.no_prompt_keywords:
        user_keywords = []
    else:
        user_keywords = prompt_for_user_keywords()

    result = analyze_function(df, display_name, title_terms, user_keywords, onet_names)
    print_function_report(result)

    if args.export:
        export_results([result], args.export)


if __name__ == "__main__":
    main()
