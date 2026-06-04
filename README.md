# Aerospace AI Job Analysis

**4,563 job postings. 89 companies. Zero AI governance roles.**

This repo contains the scrapers and analysis toolkit behind the article:
["I scanned 4,563 aerospace job postings for AI governance language. Here's what I found."](ARTICLE_LINK)

How it was built: ["How I scraped 4,563 job postings from six ATS platforms — and what broke"](SCRAPING_ARTICLE_LINK)

---

## Get the full dataset

The complete 4,563-row dataset is available to subscribers.

**[Download the full dataset →](NEWSLETTER_LINK)**

The repo includes a 50-row sample across 10 companies in `data/sample_output.csv`.  
Subscribe to get the complete dataset + future analyses when they publish.

---

## The finding

Aerospace companies are deploying AI into safety-critical systems — autonomous flight, electronic warfare, launch vehicles — but not one of 89 companies is hiring to certify it. The vocabulary of AI governance (ARP6983, learning assurance, ODD definition, model validation) is completely absent from 4,563 job descriptions. The regulatory gate is forming. The org chart hasn't moved.

---

## What's in this repo

```
scrapers/
  greenhouse_scraper.py     API-first. Covers Relativity Space, Rocket Lab, Planet Labs, SpaceX.
  lever_scraper.py          API-first. Covers Hermeus.
  workday_scraper.py        Playwright + CXS API. Boeing, Sierra Nevada, Booz Allen, Wisk.
  phenom_scraper.py         Phenom People. Northrop Grumman, GE Aerospace.
  dom_inspector.py          Diagnostic — inspect any careers page before writing a scraper.
  base.py                   Shared schema, rate limiting, output normalization.

analysis/
  keyword_analysis.py       Full governance + capability keyword scan. Reproduces article findings.
  false_positive_detector.py  Context-aware categorizer. Separates AI signals from noise.

keywords/
  governance.txt            47 AI governance / certification terms — all zero in the dataset.
  capability.txt            43 AI capability terms — what they ARE hiring for.
  certification_adjacent.txt  33 traditional cert terms — for false positive context.

data/
  sample_output.csv         50 rows across 10 companies. Full dataset via newsletter above.
  companies.csv             91 companies with ATS platform, scraper status, known issues.
  schema.md                 Field definitions, platform breakdown, known limitations.
```

---

## Quick start

```bash
git clone https://github.com/mtoyserkani/aerospace-ai-job-analysis
cd aerospace-ai-job-analysis

pip install -r requirements.txt
playwright install chromium

# Download the full dataset (newsletter link above), save to data/
# Then run the analysis:
python analysis/keyword_analysis.py --input data/aerospace_all_jobs.csv

# Or run on the sample:
python analysis/keyword_analysis.py --input data/sample_output.csv

# Diagnose a careers page before scraping it
python scrapers/dom_inspector.py --url https://careers.geaerospace.com --intercept-api
```

---

## Reproducing the analysis

```bash
# Run on sample (included) or full dataset (via newsletter)
python analysis/keyword_analysis.py --input data/sample_output.csv

# What does "certification" actually mean in these postings?
python analysis/false_positive_detector.py --input data/aerospace_all_jobs.csv --keyword certification

# Export results as JSON
python analysis/keyword_analysis.py --input data/aerospace_all_jobs.csv --export results/
```

---

## The dataset schema

| Field | Description |
|---|---|
| `job_id` | Native ATS ID or SHA-256 hash of URL |
| `title` | Job title as posted |
| `company` | Company name |
| `location` | Location string |
| `country` | ISO 3166-1 alpha-2 |
| `remote` | `remote` \| `hybrid` \| `onsite` \| `unknown` |
| `apply_url` | Direct application URL |
| `description_text` | Full description, HTML stripped |
| `seniority` | Inferred from title |
| `salary` | Raw string if present |
| `source_platform` | `phenom` \| `greenhouse` \| `workday` \| `lever` \| `oracle` \| `talentbrew` \| `generic` |
| `scraped_at` | ISO 8601 UTC timestamp |

Full field definitions and known limitations: `data/schema.md`

---

## Platform coverage

| Platform | Jobs | Key companies |
|---|---|---|
| Phenom | 3,216 | Northrop Grumman (2,501), GE Aerospace (715) |
| Greenhouse | 707 | Relativity Space (290), Rocket Lab (280), Planet Labs (85), SpaceX (50) |
| Workday | 453 | Sierra Nevada (48), Boeing (40), Booz Allen (40), Wisk (23) |
| Oracle HCM | 50 | Standard Aero |
| Lever | 50 | Hermeus |
| TalentBrew | 15 | L3Harris |
| Generic | 72 | Mixed |

**Known limitations:** Boeing undersampled (40 jobs captured — scraper throttled). GE Aerospace may be undercounted (DOM selector issue — see `dom_inspector.py`). RTX and Leidos blocked by Cloudflare. Full details in `data/schema.md` and `data/companies.csv`.

---

## Adding more companies

```python
# Add a Greenhouse company — edit scrapers/greenhouse_scraper.py:
COMPANIES = {
    ...
    "jobyaviation": "Joby Aviation",
}
# Then: python scrapers/greenhouse_scraper.py --companies jobyaviation
```

Not sure which ATS a company uses? Run:
```bash
python scrapers/dom_inspector.py --url https://careers.[company].com --intercept-api
```

---

## Limitations and honest notes

**This is market research, not a production system.** Built to answer a specific question about aerospace AI hiring. Works for the companies listed; may need adjustment for others.

**Check ToS before scraping.** All scrapers include rate limiting (20–30 req/min) and honest User-Agent headers. Check each company's robots.txt before running at scale.

**The false positive problem is real.** "Certification" in aerospace postings almost always means A&P mechanic credentials, security clearances, or ITAR compliance — not AI certification. Run `false_positive_detector.py` before drawing conclusions from keyword counts.

---

## License

MIT. Use freely. Link back if you build on it.

---

## About

[Your name] — [LinkedIn URL]

I write about AI in regulated industries.  
**[Subscribe →](NEWSLETTER_LINK)** to get the full dataset and future analyses.
