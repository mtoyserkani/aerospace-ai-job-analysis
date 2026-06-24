# Aerospace AI Job Analysis

**34 companies. 25,046 job postings. 96% with full description text.**

This repo is the toolkit behind ["The Job Seeker Toolkit: How to Use Job
Posting Data to Find the Skills Employers in Your Industry Actually
Want"](ARTICLE_LINK) — a step-by-step framework for turning public job
data into a personalized skill gap analysis, for any industry, any role.

The aerospace dataset here is the worked example. The scrapers, the
job-function matching, the certification/tool/clearance lookups — all of
it is built so you can point the same toolkit at a different industry's
job postings and run your own analysis.

---

## Get the full dataset

`master_dataset.csv` (25,046 rows, 159MB) is gitignored — it exceeds
GitHub's 100MB file limit and is gated content for newsletter subscribers.

**[Download the full dataset →](NEWSLETTER_LINK)**

The repo includes a small sample in `data/sample_output.csv` so you can
inspect the schema and test the analysis scripts without the full file.
Or rebuild it yourself from the 34 source CSVs already in this repo — see
"Run it yourself" below.

---

## What's in this repo

```
scrapers/
  base.py                       Shared schema, rate limiting, inference functions
  workday_scraper.py             Playwright + CXS API
  greenhouse_scraper.py          API-first
  lever_scraper.py                API-first
  brassring_scraper.py            Playwright
  icims_scraper.py                Playwright
  talentbrew_scraper.py           Playwright
  taleo_scraper.py                 Playwright
  dayforce_scraper.py              Playwright (built for Elbit Systems of America)
  detect_ats.py                    Identify which ATS a careers page runs on
  dom_inspector.py                 Diagnostic — inspect page structure before writing a scraper
  parked/                          Scrapers that produced zero usable rows in this dataset:
                                    eightfold_scraper.py, phenom_scraper.py, successfactors_scraper.py,
                                    plus two one-off debug scripts

analysis/
  merge_dataset.py                Merges the 34 source CSVs into master_dataset.csv, deduplicates
  function_analysis.py            Job-function skill discovery — the tool this article walks through
  report_to_pdf.py                 Turns function_analysis.py's text report into a clean PDF
  verify_job_functions.py         Runs every job_functions/*.txt file against live data, reports match counts
  extract_salary.py                Extracts salary from description_text when the salary column is empty
  parse_workday_salary.py          Parses pre-existing raw salary strings in Workday source files
  enrich_descriptions.py          Fills in missing description_text via secondary fetch
  check_data.py                    Data quality checks

job_functions/
  18 .txt files — title-matching term lists, one per function (see "Job functions" below)

data/
  [34 source CSVs]                  One per company, named platform_company.csv
  companies.csv                     All 34 companies — platform, output file, job count, description coverage
  schema.md                         Full field reference, including known data-quality caveats
  reference/onet_software_skills.txt  O*NET "Hot Technology" software skills list (8,753 rows), ships with this repo
  sample_output.csv                  Small schema sample
```

---

## Run it yourself

Requires Python 3.11+.

```bash
git clone https://github.com/mtoyserkani/aerospace-ai-job-analysis
cd aerospace-ai-job-analysis
pip install -r requirements.txt
playwright install chromium
```

**1. Merge the 34 source CSVs into one dataset:**

```bash
python3 analysis/merge_dataset.py
```

This is the only step most people need. It reads every
`platform_company.csv` file already in `data/`, deduplicates on
`(company, job_id)`, and writes `data/master_dataset.csv` — 25,046 rows.

Everything else — `location`, `country`, `remote`, `seniority`,
`us_citizenship_required`, `security_clearance`, `relocation_assistance`,
and most of `salary` — is **already set inside each source CSV**, computed
at scrape time, not at merge time. Merging doesn't add or change any of
that; it only combines files and drops duplicates. See `data/schema.md`
for exactly how each field gets its value, including two known
inconsistencies (country detection isn't identical across all 8 platforms,
and `remote` has a gap on Workday's first pass) worth knowing before you
treat those two fields as fully reliable.

**2. Fill in any remaining salary gaps:**

```bash
python3 analysis/extract_salary.py data/master_dataset.csv data/master_dataset.csv
```

Most salary data is already in the source CSVs by the time you reach this
step. This scans `description_text` for salary figures only on the rows
where `salary` is still empty after merge. See `data/schema.md`'s "Salary
fields" section for the exact extraction rules and the order this fits
into the rest of the pipeline.

No O*NET download step needed — `data/reference/onet_software_skills.txt`
ships with this repo.

---

## Job functions

This is the actual point of the toolkit: matching job postings to a
function you define, then pulling certifications, clearances, salary, and
tools for just that slice — without writing a single line of analysis
code yourself.

### Use an existing function

18 are already built:

`aerospace_engineer`, `ai_ml_engineer`, `business_development`,
`capture_proposal_management`, `configuration_data_management`,
`cybersecurity`, `data_analyst`, `data_engineer`, `mechanical_engineer`,
`product_management`, `product_operations`, `program_management`,
`qa_engineer`, `sales`, `software_engineer`, `structural_engineer`,
`systems_engineer`, `verification_validation_engineer`

```bash
python3 analysis/function_analysis.py --function program_management
```

This skips straight to the report — seniority breakdown, salary by
seniority, top hiring companies, certifications, clearances, and tools —
using the title terms already saved in `job_functions/program_management.txt`.

After loading the function, it'll still prompt you once for any ad hoc
keywords you personally want to check (your own hypothesis, checked fresh
each run, separate from everything else). Press enter to skip, or skip the
prompt entirely:

```bash
python3 analysis/function_analysis.py --function program_management --no-prompt-keywords
```

Or pass keywords non-interactively:

```bash
python3 analysis/function_analysis.py --function program_management --keywords "agile, scrum master, OKRs"
```

### Define a new function — no file editing required

```bash
python3 analysis/function_analysis.py
```

With no `--function` flag, it prompts you directly:

```
Enter the job titles that define the role you're researching.
Comma-separated, e.g.: program manager, project manager, technical program manager
Titles:
```

Type your titles, comma-separated. It then asks if you want to save this
as a reusable function — give it a short name, and it writes
`job_functions/<name>.txt` for you. Next time, skip straight to
`--function <name>`.

Title matching is fuzzy (typos and word order don't matter — "Programm
Manager" still matches "program manager") but token-based, so distinct
words aren't treated as typos of each other ("project" won't match
"product"). See "Matching logic — three different strategies, used in
different places" below before editing any term list by hand.

### What you get in the report

For whichever function you ran:

- **Seniority breakdown** — % of matched jobs at each level
- **Salary by seniority** — US postings only, in USD, with coverage shown
  per level so you know how much of each number is real signal
- **Top companies hiring for this function**
- **Certifications** — a maintained list of real, nameable credentials
  (PMP, CISSP, Six Sigma, AWS Certified, and more), count and top companies
  per cert, including zero-count entries (absence is itself a finding)
- **Aerospace-specific compliance & certifications** — AS9100, ITAR,
  DO-178C, GD&T, and more, kept separate from the general list above
- **Security clearances** — Top Secret, TS/SCI, Secret, and others
- **AI / data engineering tools** — PyTorch, LangChain, Kubernetes, and
  other modern tools O*NET's slower update cycle doesn't yet cover
- **PM / design / collaboration tools and frameworks** — Figma, Smartsheet,
  JTBD, RICE, Kanban, and more
- **Tools & software (O*NET)** — matched against the U.S. Department of
  Labor's O*NET Software Skills database, with a match-strength score so
  you can judge confidence yourself

Export the full results to CSV:

```bash
python3 analysis/function_analysis.py --function program_management --export results/program_management.csv
```

### Generate a clean PDF report

The terminal output above (ASCII bar charts, fixed-width columns) is
readable in a terminal but doesn't paste or print well anywhere else.
`report_to_pdf.py` parses that exact text output and rebuilds it as
proper bordered tables — zero-count rows kept and grayed out, not
hidden, same "absence is a finding" rule as everywhere else in this
toolkit.

```bash
python3 analysis/function_analysis.py --function program_management \
    --no-prompt-keywords > results/program_management_report.txt

python3 analysis/report_to_pdf.py \
    results/program_management_report.txt \
    results/program_management_report.pdf \
    "Program Management — Job Function Breakdown"
```

Requires `reportlab` (already in `requirements.txt`). This produces a
file, not a CSV — for the structured data itself, use `--export` above
instead.

### Verify before you commit a new function

If you've added or edited a job-function file, check it against real data
before relying on it:

```bash
python3 analysis/verify_job_functions.py
```

Runs every file in `job_functions/` against the live dataset and prints
match counts side by side, so an unexpectedly high or low number is
visible immediately. Includes a regression check on `sales.txt`
specifically — that file's "account manager" term once also matched
"Control Account Manager" (an unrelated program-finance title), inflating
the count by 70% before the term was narrowed.

---

## Matching logic — three different strategies, used in different places

This toolkit doesn't use one "fuzzy matching" approach. It uses three,
deliberately different, because each section of the report needs a
different tradeoff between precision and recall.

### 1. Job-title matching — fuzzy, token-based, used to decide which jobs belong to your function

This is what runs when you define title terms like `program manager,
project manager, technical program manager` and the script decides which
of the 25,046 job titles match.

Every word ("token") in your search term has to be found somewhere in the
job title — but each individual word is allowed to fuzzy-match, not just
exact-match, to catch real typos and minor variants:

- `"Programm Manager"` (typo) still matches a search for `program manager`
  — edit distance 1, a real typo. But `"Project Engineer"` does **not**
  match a search for `product engineer`, even though "project" and
  "product" look similar — that's edit distance 2, too far to be a typo,
  and was specifically capped out after an earlier version let it through
  and matched every Project Manager posting against Product Manager
  searches. This is also why you should never relabel a
  `program_management` run as "Product Management" or vice versa — the
  two functions are kept deliberately separate, with two different term
  lists, precisely because the matching logic treats "project/program"
  and "product" as unrelated words, not synonyms.
- A term list only needs the *role* words, not every seniority or domain
  prefix that might sit in front of them — the matcher requires both
  tokens (`systems` and `engineer`/`engineering`) to be present somewhere
  in the title, but doesn't care what else surrounds them. A
  `job_functions/systems_engineer.txt` containing just `systems engineer`
  and `systems engineering` already catches `Principal Systems Engineer`,
  `Senior Systems Engineer`, `Lead, Systems Engineer`, `Systems
  Engineering Director`, and `Aerospace Systems Engineer III` — no need to
  write a separate line for every seniority level or qualifier. Checked
  directly against this dataset: no `VP` or `Vice President` variant of
  this title exists at all here — systems engineering leadership tops out
  at Director in this data, and that's a real finding about the dataset,
  not a gap in the term list.
- Short tokens (3 letters or fewer, e.g. `"ISS"` for International Space
  Station) require an **exact** match with no fuzzy tolerance at all —
  short strings fuzzy-match too many unrelated short strings to be safe
  (a 3-letter title token was once matching three unrelated 4-letter
  cybersecurity acronyms by typo-distance).

This logic lives in `_tokens_match()` and `title_matches_term()` in
`analysis/function_analysis.py`.

### 2. O*NET tool/software matching — phrase match first, partial-credit fallback second

Used only in the "Tools & Software" section of the report, matching job
descriptions against the 8,753-row O*NET reference list.

- **Strength 1.0**: the tool's full name appears as an adjacent phrase in
  the job text, word order respected (e.g. "Microsoft Project" found as
  written).
- **Strength 0.5**: the full phrase isn't there, but at least one
  genuinely distinctive word from the tool's name is present — an acronym
  (`SAP`, `JIRA`) or a real brand word (`Adobe`, `Atlassian`), specifically
  *not* an ordinary English word that happens to be capitalized in O*NET's
  verbose naming style (`Analysis`, `Program`, `Reactor` don't count,
  even capitalized, because O*NET's tool names are full descriptive
  phrases like "Reactor excursion and release analysis program RELAP,"
  and most of those words aren't actually distinctive).

This is a different mechanism from job-title matching — no Levenshtein
distance involved, no typo tolerance. It exists because O*NET's naming
convention is so verbose that naive substring matching against it
produced 95%+ false-match rates in testing.

### 3. Certifications, clearances, and hand-curated tool lists — exact word-boundary match, deliberately not fuzzy

The certifications, clearances, AI/data tools, and PM/design tool
sections all use simple exact phrase matching with word boundaries — no
fuzzy logic, no partial credit. These are closed, nameable vocabularies
(you either have a PMP or you don't; the credential's name doesn't have
typo variants worth tolerating), so exact matching is both sufficient and
safer than fuzzy matching would be here.

**One deliberate exception: case sensitivity.** `SAFe` (Scaled Agile
Framework) is matched against the original-case text instead of the
lowercased version, because matching it case-insensitively also catches
every ordinary use of the word "safe" — "safe operations," "safety-
critical equipment," and so on. Checked directly: case-insensitive
matching inflated the real count of 471 genuine SAFe mentions to 9,191 by
pulling in the ordinary word. Case-sensitive matching against the literal
string `SAFe` returns the real number. `LeSS` (Large-Scale Scrum) has the
same problem in a more extreme form — case-insensitive `less` is one of
the most common words in English. This is the `CASE_SENSITIVE_TERMS` set
in `analysis/function_analysis.py` — if you add a new term whose name is
also an ordinary English word, check whether it needs the same treatment
before trusting its count.

### General notes that apply across all three

- **Zero-count results are findings, not failures.** If a function or
  term returns zero matches, report that — don't drop it from the list.
- **Watch for single-company boilerplate contamination.** A term can look
  like strong signal dataset-wide and turn out to be one company's "About
  us" paragraph pasted into every posting regardless of role. Check what
  fraction of hits come from one company before trusting a headline
  number.
- **Watch for collisions with short acronyms in other languages**, not
  just English. A real aerospace credential abbreviation can be
  unverifiable in a multinational dataset if it happens to spell a common
  word in another language present in your postings.
- **Always spot-check a sample of what actually matched** before trusting
  a count, regardless of which of the three strategies produced it. The
  `sales.txt` term list once included "account manager," which also
  matched "Control Account Manager" — an EVM/program-finance title with
  no connection to sales — inflating the count by 70% before anyone
  checked the actual matched titles. Run `verify_job_functions.py` before
  committing changes to any function file; it includes this exact check
  as a standing regression guard.

---

## Re-scraping and adding companies or functions

### Re-run the full pipeline end to end

```bash
# 1. Re-scrape a company (example: Workday-based)
python3 scrapers/workday_scraper.py --companies boeing --output-dir data

# 2. Re-merge everything
python3 analysis/merge_dataset.py

# 3. Re-run salary extraction on whatever's still empty
python3 analysis/extract_salary.py data/master_dataset.csv data/master_dataset.csv

# 4. Re-run your function analysis
python3 analysis/function_analysis.py --function program_management
```

Each scraper overwrites its own output file completely — it doesn't
append to or merge with the existing file. If you only want to refresh
one company, re-run only that scraper; you don't need to touch the other
33 source files.

### Add a new company

**1. Find out what ATS it uses:**

```bash
python3 scrapers/detect_ats.py --company "Company Name"
```

This checks the company's careers page and tells you which of the 8
supported platforms it's running (or flags it as unidentified, blocked,
or not found). If it's blocked or unidentified, `dom_inspector.py` is the
next step — it dumps page structure so you can see what you're dealing
with before writing anything.

**2. Confirm the right scraper exists for that platform**, then run it
against just that one company first — never start with a large company.
Test on something small, confirm the output looks right, then scale up.

**3. Add the company to `data/companies.csv`** with its platform, output
filename, and scraper — this is the reference table the rest of the repo
(and your own future self) uses to track what's live.

**4. Register the company's slug in that scraper's own `COMPANIES` dict,
then scrape it.** Each scraper (e.g. `greenhouse_scraper.py`,
`workday_scraper.py`) keeps its own small dict at the top of the file
mapping a short slug to a display name — for Greenhouse, the slug is the
literal board identifier from the company's careers API URL
(`boards-api.greenhouse.io/v1/boards/{slug}/jobs`); for other platforms
it's whatever identifier that platform's API or URL structure uses.
`detect_ats.py` tells you the *platform*, not the slug — you still need
to find the slug yourself, usually by viewing the company's careers page
source or running `dom_inspector.py` against it.

Once the slug is added to the dict, scrape it:

```bash
python3 scrapers/greenhouse_scraper.py --companies newcompanyslug --output-dir data
```

This writes `data/greenhouse_newcompanyslug.csv`, matching the
`platform_company.csv` naming convention every other source file in
`data/` already follows — `merge_dataset.py` only picks up files that
follow this pattern.

**5. Re-run `merge_dataset.py`** to fold the new company's CSV into
`master_dataset.csv`.

### Add a new job function

Two ways:

- **Interactively**, the way described above — run
  `function_analysis.py` with no flags, type your titles, save when
  prompted.
- **By hand**, if you'd rather: create `job_functions/<name>.txt`, one
  title-term per line, no header row needed. Then run
  `verify_job_functions.py` to check its match count against real data
  before you trust it or commit it.

Either way, see "Matching logic — three different strategies, used in
different places" above — in particular the spot-check guidance and the
`sales.txt` example — before you trust a new function's match count.

---

## Help shape what this becomes

This is an MVP. Three signals would help me turn it into something more
reliable:

1. **Companies or industries worth adding** — which ones would actually
   matter for your question, if you pointed this at a different industry.
2. **What's missing from the toolkit** — a field, a matching edge case, a
   report section.
3. **What's worth collecting that isn't yet** — a field type, a data
   source, a cross-reference.

And if you built your own dataset with this: share what you ran into.
What broke, what surprised you, what you'd do differently.

Open an issue, comment on GitHub, or reply to the article.

---

## Known limitations

See `data/schema.md` for the full list, including the country-inference
inconsistency across platforms and the `remote`/`unknown` field caveats.
Salary coverage is 56.9% — not every posting discloses pay. Data was
scraped through mid-June 2026 — re-scrape before citing anything
time-sensitive.

---

## License

[ADD LICENSE]
