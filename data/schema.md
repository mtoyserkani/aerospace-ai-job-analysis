# Dataset Schema

## Getting the full dataset

The complete 4,563-row dataset is available to newsletter subscribers:
**[Subscribe to download →](NEWSLETTER_LINK)**

`sample_output.csv` (included in this repo) contains 50 rows across 10 companies
to let you inspect the schema and test the analysis scripts before downloading.

---

## Fields

| Field | Type | Description |
|---|---|---|
| `job_id` | string | Native ATS job ID, or SHA-256 hash of apply_url if no native ID |
| `title` | string | Job title as posted |
| `company` | string | Company name |
| `location` | string | Location string as posted |
| `country` | string | ISO 3166-1 alpha-2 country code, inferred from location |
| `remote` | string | `remote` \| `hybrid` \| `onsite` \| `unknown` |
| `apply_url` | string | Direct application URL |
| `description_text` | string | Full job description, HTML stripped |
| `seniority` | string | `senior` \| `lead` \| `director` \| `junior` \| `mid` — inferred from title |
| `salary` | string | Raw salary string if present, else empty |
| `source_platform` | string | `phenom` \| `greenhouse` \| `workday` \| `lever` \| `oracle` \| `talentbrew` \| `generic` |
| `scraped_at` | string | ISO 8601 UTC timestamp |

## Platform breakdown (full dataset)

| Platform | Jobs | Key companies |
|---|---|---|
| phenom | 3,216 | Northrop Grumman (2,501), GE Aerospace (715) |
| greenhouse | 707 | Relativity Space (290), Rocket Lab (280), Planet Labs (85), SpaceX (50) |
| workday | 453 | Sierra Nevada (48), Boeing (40), Booz Allen (40), Wisk (23) |
| generic | 72 | Mixed (BrassRing / IBM Kenexa portals) |
| oracle | 50 | Standard Aero |
| lever | 50 | Hermeus |
| talentbrew | 15 | L3Harris |

## Known limitations

**Boeing undersampled.** 40 jobs captured vs. ~170,000 employees.
Do not cite Boeing data for quantitative claims.

**GE Aerospace may be undercounted.** DOM selector mismatch in phenom_scraper.
Run `dom_inspector.py` against `careers.geaerospace.com` to diagnose.

**RTX / Raytheon and Leidos absent.** Cloudflare blocks all automated access.

**Descriptions vary in completeness.** Some Workday postings have empty
`description_text` — CXS API used for listing but Playwright enrichment
not run for all. Keyword analysis may undercount for Workday companies.

**Scrape date.** Data collected Q1–Q2 2026. Re-scrape before citing for anything time-sensitive.

## Parent company normalization

`parent_company_map.json` maps 89 legal entities to parent companies for analysis.

The article reports "89 companies scraped" (entities). Analysis groups by parent so
all Boeing entities (Boeing Company, Boeing Distribution, Boeing Aerospace Operations,
Boeing India) are counted together as Boeing.

Key groupings: Boeing (4 entities, 50 jobs), Curtiss-Wright (6 entities, 46 jobs),
Airbus (13 entities, 37 jobs), CAE (10 entities, 37 jobs), Moog (7 entities, 48 jobs).
