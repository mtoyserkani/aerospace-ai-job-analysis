# Dataset Schema

## Getting the full dataset

The merged dataset (`data/master_dataset.csv`, 25,046 jobs) is gitignored — at
159MB it exceeds GitHub's 100MB file limit, and it's gated content for
newsletter subscribers.

**[Get the full dataset →](NEWSLETTER_LINK)**

Run `python3 analysis/merge_dataset.py` against the 34 CSVs in `data/` to
rebuild it yourself — see the README for the full command sequence.

---

## Fields

`master_dataset.csv` has 16 columns. The **Source** column below tells you
when and how each field gets its value — this matters, because most of this
dataset's fields aren't set at merge time. They're already baked into each
source CSV before `merge_dataset.py` ever runs.

| Field | Type | Source | Description |
|---|---|---|---|
| `company` | string | Scraper | Company name, normalized per source (e.g. `The Boeing Company`, not the raw legal-entity string from the ATS) |
| `title` | string | Scraper | Job title as posted, raw |
| `job_id` | string | Scraper | Native ATS job ID |
| `location` | string | Scraper, raw | The location string exactly as the ATS posted it — not cleaned, not normalized |
| `country` | string | Scraper, inferred from `location` | See "Country logic is not uniform" below — this is the one field where the inference method differs by platform |
| `salary` | float or empty | Mixed — see "Salary fields" below | Annualized USD figure where available |
| `remote` | string | Scraper, inferred from `location` + `description_text` | `remote` \| `hybrid` \| `onsite` \| `unknown` |
| `seniority` | string | Scraper, inferred from `title` | Values include `Director`, `Principal`, `Senior`, `Manager`, `Lead`, `Mid`, and others — not a fixed enum |
| `us_citizenship_required` | string | Scraper, inferred from `description_text` | `us_person` \| `yes` \| `no` \| `unknown` |
| `security_clearance` | string | Scraper, inferred from `description_text` | Clearance level as stated (`Secret`, `TS/SCI`, `Top Secret`, `TS/SCI + Poly`) or `none` / `unknown` |
| `relocation_assistance` | string | Scraper, inferred from `description_text` | `yes` \| `no` \| `unknown` |
| `source_platform` | string | Scraper | `workday` \| `greenhouse` \| `brassring` \| `icims` \| `talentbrew` \| `taleo` \| `dayforce` \| `lever` |
| `date_posted` | string (ISO date) or empty | Scraper, when available | Only ~23% of rows carry this — most ATS platforms in this dataset don't expose a reliable posting date |
| `scraped_at` | string (ISO 8601 UTC) | Scraper | When the row was scraped |
| `apply_url` | string | Scraper | Direct application URL |
| `description_text` | string | Scraper | Full job description, HTML stripped |

**Nothing in this table is touched by `merge_dataset.py`.** Merge only
normalizes column presence (adds a default value if a source file is
missing a column entirely) and deduplicates on `(company, job_id)`. Every
inferred field above already has its final value in the source CSV before
merge runs.

## Country logic is not uniform across platforms

`scrapers/base.py` defines one `_infer_country()` — checks the `location`
string for Canada/UK/Europe/India/Australia keywords, defaults to United
States. But **4 of the 8 scraper files define their own local
`_infer_country()`** that shadows the shared one instead of using it:

| Scraper | Uses shared `base.py` logic? | What it actually checks |
|---|---|---|
| `workday_scraper.py` | Yes | Canada, UK, Europe, India, Australia, defaults US |
| `icims_scraper.py` | Yes | Same as above |
| `dayforce_scraper.py` | Yes | Same as above |
| `lever_scraper.py` | Yes | Same as above |
| `greenhouse_scraper.py` | **No — local override** | Canada, UK, **Germany** (returns the literal string `"Germany"`, not `"Europe"`), defaults US |
| `brassring_scraper.py` | **No — local override** | Canada, UK only, defaults US |
| `talentbrew_scraper.py` | **No — local override** | Canada, UK only, defaults US |
| `taleo_scraper.py` | **No — local override** | Canada, UK only, defaults US |

Practically: a job posted in India or Australia will be correctly tagged
on Workday/iCIMS/Dayforce/Lever, but will fall through to
`"United States of America"` on Greenhouse/BrassRing/TalentBrew/Taleo,
since none of those four local versions check for it. `country` values you
may see in the data: `United States of America`, `Canada`, `United
Kingdom`, `Europe`, `India`, `Australia`, `Germany` (Greenhouse only). This
is a real inconsistency, not a documentation gap — fixing it means either
deleting the four local overrides so all eight scrapers import the shared
function, or updating the shared function and bringing the other four in
line with it. Not yet done.

## `remote` has a first-pass gap on Workday

Every platform's enrichment phase calls `infer_remote(location,
description_text)` — checking both fields gives the best signal, since
many "remote" postings don't say so in the location string. But
`workday_scraper.py`'s **first pass** (the listing scrape, before
descriptions are fetched) calls `infer_remote(location)` with no
description text yet, because none has been fetched at that point. If
enrichment fails or is skipped for a row, `remote` can be left at the
listing-pass value — which defaults to `onsite` for any row where the
location string itself doesn't contain "remote"/"hybrid"/"work from
home"/"wfh", even if the actual posting describes itself as remote
further down in the body text. This is a latent gap, not a confirmed count
of affected rows — checking how many rows in the current dataset never
got the second pass hasn't been done.

## `unknown` is a deliberate default, not missing data

For `us_citizenship_required`, `security_clearance`, and
`relocation_assistance`: each extraction function (`extract_citizenship`,
`extract_clearance`, `extract_relocation` in `base.py`) returns `unknown`
immediately if `description_text` is empty, and returns `unknown` if the
description has text but none of the function's known phrases (e.g. "must
be a U.S. citizen," "does not require a security clearance") appear in
it. Both cases produce the same value, so `unknown` in this dataset means
either "we don't have description text for this row" or "we have it, and
it doesn't say." There's no way to distinguish the two from the `unknown`
value alone — if that distinction matters for your analysis, you'd need
to cross-reference against whether `description_text` is empty.

## Salary fields

Salary coverage: **14,258 / 25,046 jobs (56.9%)**.

Two scripts populate `salary`, run in this order:

1. **`parse_workday_salary.py`** — runs against each `workday_*.csv` source
   file *before* merge, parsing pre-existing raw salary strings already in
   that platform's `salary` column (e.g. `"$74,500 - $105,525"`,
   `"$32.19/hr; $39.17/hr"`). The Workday CSVs currently in this repo have
   already had this run against them — their `salary` cells are clean
   numeric values, not raw strings — so running this script again on them
   today correctly does nothing. It's a function you call from a script
   that captures and writes the returned `df`; running it standalone from
   the CLI only writes a log file, it does not overwrite the source CSV
   by itself.
2. **`extract_salary.py`** — runs against the merged `master_dataset.csv`,
   filling remaining nulls by pattern-matching salary figures out of
   `description_text`.

Locked extraction rules (do not change without re-confirming against real
data):

- Multiple salary ranges in one description → midpoint average across all
- Malformed or truncated numbers → skipped, left null
- Non-USD currency → raw number kept, stored in a separate `salary_currency`
  column (added by these scripts, not present in the base merge), no FX
  conversion applied
- Figures under $200 → treated as hourly, annualized at 2,080 hrs/yr
- When both an annual figure and an hourly figure appear in the same cell →
  annual figure wins, hourly is discarded

`salary_currency` only exists in the dataset after running these two
scripts; it is not part of the 16-column base schema above.

## Platform breakdown (34 companies, 25,046 jobs)

| Platform | Jobs | Companies |
|---|---|---|
| Workday | 10,408 | Airbus, Leidos, Booz Allen Hamilton, Boeing, Blue Origin, Moog, Curtiss-Wright, CAE, Crane, AeroVironment, Sierra Nevada, Woodward, Vantor, Wisk Aero |
| Greenhouse | 5,053 | Anduril Industries, SpaceX, Rocket Lab, Relativity Space, Zipline, Archer Aviation, Planet Labs, Mercury Systems, Wing Aviation, Heart Aerospace |
| BrassRing | 4,720 | Lockheed Martin |
| iCIMS | 2,199 | Peraton, Joby Aviation, General Dynamics |
| TalentBrew | 1,726 | L3Harris |
| Taleo | 676 | Textron, AAR Corp, Bell (Textron) |
| Dayforce | 153 | Elbit America |
| Lever | 111 | Hermeus |

Full company-by-company breakdown, including parked/excluded companies and
why: see `data/companies.csv`.

## Known limitations

**`description_text` is missing or unusable for ~800 jobs (96% coverage,
not 100%).** Under 50 characters after HTML stripping counts as missing.

**`date_posted` is sparse by design, not by bug.** Most ATS platforms in
this dataset don't expose a reliable posting date in their listing or API
response. Don't treat the ~23% that do have a value as representative of
posting recency across the full dataset.

**Scrape window.** Data collected through mid-June 2026. Re-scrape before
citing anything time-sensitive — aerospace hiring volume moves fast, and
at least two ATS migrations are known to be in progress (see README).

## Parent company normalization

This dataset counts by operating brand, not legal entity — `Bell (Textron)`
and `Textron` are kept as two separate rows in `companies.csv` because they
post through two different Taleo career sites, even though Bell is a
Textron subsidiary. No entity-to-parent rollup is applied beyond what's
already reflected in how each company posts its jobs.
