"""
workday_scraper.py — Scrapes companies using the Workday ATS.

Workday is JavaScript-heavy with no public API. Requires Playwright.
Some Workday instances are behind Cloudflare Bot Management — those
companies are documented below with their block status.

Install: pip install playwright && playwright install chromium

Companies in this dataset using Workday:
  - Sierra Nevada Corporation  → sncorp.wd5.myworkdayjobs.com
  - The Boeing Company         → boeing.wd1.myworkdayjobs.com  [partial — see note]
  - Booz Allen Hamilton        → bah.wd1.myworkdayjobs.com
  - Vantor Services Inc.       → careers.vantor.com
  - Wisk Aero LLC              → wisk.wd5.myworkdayjobs.com

NOTE ON BOEING: Boeing's Workday instance returns partial results via
Playwright. Their tenant appears to have enhanced bot detection. The
48 Boeing jobs in this dataset were captured before throttling kicked in
and are NOT representative of Boeing's actual hiring volume (~170K employees).
Do not cite Boeing data from this dataset for quantitative claims.

NOTE ON RTX / RAYTHEON: Consistently blocked by Cloudflare. Not included.
NOTE ON LEIDOS: Consistently blocked by Cloudflare. Not included.

Workday URL pattern: https://{tenant}.myworkdayjobs.com/{instance}/jobs
Workday API pattern: https://{tenant}.myworkdayjobs.com/wday/cxs/{tenant}/{instance}/jobs

Usage:
    pip install playwright httpx && playwright install chromium
    python workday_scraper.py --output data/workday_jobs.csv
    python workday_scraper.py --companies sncorp wisk
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from base import Job, RateLimiter, infer_seniority, infer_remote, save_jobs, Job

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "sncorp": {
        "name":     "Sierra Nevada Corporation",
        "tenant":   "sncorp",
        "instance": "EXTERNAL_CAREERS",
        "blocked":  False,
    },
    "boeing": {
        "name":     "The Boeing Company",
        "tenant":   "boeing",
        "instance": "EXTERNAL_CAREERS",
        "blocked":  False,
        "note":     "Partial results only — see module docstring",
    },
    "bah": {
        "name":     "Booz Allen Hamilton",
        "tenant":   "bah",
        "instance": "External_Career_Site",
        "blocked":  False,
    },
    "wisk": {
        "name":     "Wisk Aero LLC",
        "tenant":   "wisk",
        "instance": "External",
        "blocked":  False,
    },
    "rtx": {
        "name":     "RTX / Raytheon",
        "tenant":   "rtx",
        "instance": "RTX_Careers",
        "blocked":  True,
        "note":     "Cloudflare Bot Management blocks Playwright. Manual scrape or enterprise solution required.",
    },
    "leidos": {
        "name":     "Leidos",
        "tenant":   "leidos",
        "instance": "External",
        "blocked":  True,
        "note":     "Cloudflare blocks all automated access.",
    },
}

# ---------------------------------------------------------------------------
# Workday CXS API (internal REST endpoint, available on most tenants)
# ---------------------------------------------------------------------------

CXS_URL = "https://{tenant}.myworkdayjobs.com/wday/cxs/{tenant}/{instance}/jobs"

HEADERS = {
    "Accept":       "application/json",
    "Content-Type": "application/json",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

PAYLOAD = {
    "appliedFacets": {},
    "limit": 20,
    "offset": 0,
    "searchText": "",
}


async def scrape_via_api(config: dict, limiter: RateLimiter) -> list[Job]:
    """
    Try the Workday CXS API first. This works on most Workday tenants
    and is far faster than Playwright. Falls back to Playwright if needed.
    """
    try:
        import httpx
    except ImportError:
        print("  httpx not installed: pip install httpx")
        return []

    tenant   = config["tenant"]
    instance = config["instance"]
    company  = config["name"]
    url      = CXS_URL.format(tenant=tenant, instance=instance)

    jobs     = []
    offset   = 0
    total    = None

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        while True:
            await limiter.wait()
            payload = {**PAYLOAD, "offset": offset}

            try:
                resp = await client.post(url, json=payload, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                print(f"  [{tenant}] API error at offset {offset}: {e}")
                break

            data       = resp.json()
            total      = total or data.get("total", 0)
            page_jobs  = data.get("jobPostings", [])

            if not page_jobs:
                break

            for raw in page_jobs:
                job_id    = raw.get("bulletFields", [None])[0] or Job.stable_id(raw.get("externalPath", ""))
                title     = raw.get("title", "").strip()
                ext_path  = raw.get("externalPath", "")
                apply_url = f"https://{tenant}.myworkdayjobs.com{ext_path}" if ext_path else ""
                location  = raw.get("locationsText", "")
                posted    = raw.get("postedOn", "")

                jobs.append(Job(
                    job_id          = str(job_id),
                    title           = title,
                    company         = company,
                    location        = location,
                    country         = _infer_country(location),
                    remote          = infer_remote(location),
                    apply_url       = apply_url,
                    description_text= "",   # CXS API doesn't return descriptions; use Playwright for full text
                    seniority       = infer_seniority(title),
                    salary          = "",
                    source_platform = "workday",
                ))

            offset += len(page_jobs)
            print(f"  [{tenant}] {offset}/{total} jobs fetched...")
            if offset >= total:
                break

    return jobs


async def enrich_descriptions(jobs: list[Job], limiter: RateLimiter) -> list[Job]:
    """
    Fetch full job description text for each job using Playwright.
    The CXS API returns listings without description text. This step
    is slow (~3-5 seconds/job) but produces complete records.

    For large scrapes: run overnight, or sample 20% for analysis.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not installed. Descriptions will be empty.")
        print("  Install: pip install playwright && playwright install chromium")
        return jobs

    enriched = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        for i, job in enumerate(jobs):
            if not job.apply_url:
                enriched.append(job)
                continue

            await limiter.wait()
            try:
                await page.goto(job.apply_url, wait_until="networkidle", timeout=30000)
                # Workday job description lives in the main content section
                desc_el = await page.query_selector(
                    "[data-automation-id='jobPostingDescription'],"
                    "[class*='jobDescription'],"
                    "section[aria-label*='Description']"
                )
                desc = (await desc_el.inner_text()) if desc_el else ""
                job.description_text = re.sub(r"\s{2,}", " ", desc).strip()
            except Exception as e:
                print(f"  [{job.company}] Failed to load description for '{job.title}': {e}")

            enriched.append(job)
            if (i + 1) % 10 == 0:
                print(f"  Enriched {i+1}/{len(jobs)}...")

        await browser.close()

    return enriched


def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["canada", " bc", " ab", "ontario", "toronto"]):
        return "CA"
    if any(k in loc for k in ["uk", "london", "england"]):
        return "GB"
    return "US"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(company_keys: list[str], output: Path, skip_enrichment: bool) -> None:
    limiter  = RateLimiter(calls_per_minute=20)  # Conservative for Workday
    all_jobs = []

    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown company key: {key}. Available: {', '.join(COMPANIES)}")
            continue
        if config.get("blocked"):
            print(f"\nSkipping {config['name']} — {config.get('note', 'blocked')}")
            continue

        print(f"\nScraping {config['name']}...")
        jobs = await scrape_via_api(config, limiter)
        print(f"  Found {len(jobs)} listings via API")

        if not skip_enrichment and jobs:
            print(f"  Fetching full descriptions via Playwright...")
            jobs = await enrich_descriptions(jobs, limiter)

        all_jobs.extend(jobs)

    save_jobs(all_jobs, output)
    print(f"\nTotal: {len(all_jobs)} jobs from {len(company_keys)} companies")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Workday ATS job boards")
    active = [k for k, v in COMPANIES.items() if not v.get("blocked")]
    parser.add_argument("--companies", nargs="*", default=active,
                        help=f"Company keys. Available (unblocked): {', '.join(active)}")
    parser.add_argument("--output", type=Path, default=Path("data/workday_jobs.csv"))
    parser.add_argument("--skip-enrichment", action="store_true",
                        help="Skip Playwright description fetch (fast, but descriptions will be empty)")
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output, args.skip_enrichment))
