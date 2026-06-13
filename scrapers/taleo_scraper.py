"""
taleo_scraper.py — Scrapes companies using Oracle Taleo ATS.

Taleo exposes a REST API on most instances:
  GET https://{tenant}.taleo.net/careersection/rest/jobboard/vacancy/list
  Params: multiline=true&subjectFilesNbToGet=1&format=json&sortColumn=TITLE&sortDirection=ASC&start=0

Companies:
  - Textron          → textron.taleo.net
  - Bell (Textron)   → bellhelicopter.taleo.net (Textron subsidiary)
  - AAR Corp         → aar.taleo.net
  - American Airlines Tech Ops → aa.taleo.net

Usage:
    python3 scrapers/taleo_scraper.py --output data/taleo_jobs.csv
    python3 scrapers/taleo_scraper.py --companies textron --output data/textron_jobs.csv
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from base import (
    Job, RateLimiter, infer_seniority, infer_remote, save_jobs, sample_check,
    extract_salary, extract_citizenship, extract_clearance, extract_relocation,
)

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "textron": {
        "name":    "Textron",
        "tenant":  "textron",
        "section": "textron",
        "portal":  "8140753014",
    },
    "bell": {
        "name":    "Bell (Textron)",
        "tenant":  "textron",
        "section": "bell",
        "portal":  "20140753014",
        "note":    "Bell uses textron.taleo.net with section=bell. Portal confirmed 2026-06-06.",
    },
    "aarcorp": {
        "name":    "AAR Corp",
        "tenant":  "aarcorp",
        "section": "2",
        "portal":  "101430233",
        "note":    "Tenant aarcorp.taleo.net. Portal confirmed 2026-06-06 via dom_inspector.",
    },
    # "americanairlines": PARKED — custom Taleo site (jobs.aa.com), client-side rendering,
    # no interceptable API. Low AI signal (mostly AMT / mechanic roles). See README.
}

# Taleo uses POST /searchjobs with portal ID (confirmed via network capture 2026-06-06)
# Pattern: POST https://{tenant}.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal={portal}
API_URL = "https://{tenant}.taleo.net/careersection/rest/jobboard/searchjobs"

HEADERS = {
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Content-Type":     "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    import re as _re
    text = _re.sub(r"<[^>]+>", " ", text or "")
    return _re.sub(r"\s+", " ", text).strip()


def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["canada", "ontario", "toronto", "montreal"]):
        return "Canada"
    if any(k in loc for k in ["uk", "england", "london"]):
        return "United Kingdom"
    return "United States of America"


def _parse_date(raw: dict) -> str:
    for key in ["postingDate", "lastModifiedDate", "expirationDate"]:
        val = raw.get(key, "")
        if val:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", str(val))
            if m:
                return m.group(1)
    return "N/A"


def _build_apply_url(tenant: str, section: str, job_id) -> str:
    return (
        f"https://{tenant}.taleo.net/careersection/{section}/jobdetail.ftl"
        f"?job={job_id}"
    )


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

async def scrape_company(config: dict, limiter: RateLimiter, test: bool = False) -> list[Job]:
    """
    Taleo scraper using network interception.
    
    Taleo's TEE server requires full JS-initialized session state — cookies alone
    are insufficient. Strategy: intercept API responses as Playwright navigates
    paginated search results, capturing job data directly from network traffic.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not installed: python3 -m playwright install chromium")
        return []

    import json as _json

    tenant     = config["tenant"]
    section    = config["section"]
    company    = config["name"]
    portal     = config.get("portal")
    search_url = f"https://{tenant}.taleo.net/careersection/{section}/jobsearch.ftl"
    jobs       = []
    all_raw    = []
    total_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = await context.new_page()

        # Intercept searchjobs API responses
        async def handle_response(response):
            if "searchjobs" in response.url and response.status == 200:
                try:
                    body = await response.text()
                    data = _json.loads(body)
                    reqs = data.get("requisitionList", [])
                    if reqs:
                        all_raw.extend(reqs)
                        nonlocal total_count
                        if not total_count:
                            total_count = data.get("totalCount", 0)
                        print(f"  [{tenant}] Intercepted page: +{len(reqs)} jobs ({len(all_raw)}/{total_count})")
                except Exception as e:
                    pass

        page.on("response", handle_response)

        print(f"  Loading {search_url}...")
        await page.goto(search_url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2000)

        if not all_raw:
            print(f"  [{tenant}] No jobs intercepted on page load — trying scroll trigger...")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)

        if not all_raw:
            print(f"  [{tenant}] Still no jobs. Page may require interaction.")
            print(f"  [{tenant}] Page title: {await page.title()}")
            await browser.close()
            return []

        # Navigate remaining pages by clicking Next
        page_num = 1
        while True:
            if total_count and len(all_raw) >= total_count:
                break

            # Check for Next button that is enabled (not disabled)
            next_btn = await page.query_selector(
                "a.btn-next, a[title='Next'], "
                "a[class*='next']:not([class*='disabled']), "
                "a:has-text('Next')"
            )
            if not next_btn:
                break

            # Check if button is disabled before clicking
            is_disabled = await next_btn.get_attribute("disabled")
            aria_disabled = await next_btn.get_attribute("aria-disabled")
            class_attr = await next_btn.get_attribute("class") or ""
            if is_disabled or aria_disabled == "true" or "disabled" in class_attr:
                print(f"  [{tenant}] Next button disabled — end of results")
                break

            await limiter.wait()
            page_num += 1
            prev_count = len(all_raw)

            try:
                await next_btn.click(timeout=5000)
            except Exception:
                # Button found but not clickable — end of results
                print(f"  [{tenant}] Next button not clickable — end of results")
                break
            await page.wait_for_timeout(2000)

            # Wait for new jobs to be intercepted
            waited = 0
            while len(all_raw) == prev_count and waited < 10:
                await page.wait_for_timeout(500)
                waited += 0.5

            print(f"  [{tenant}] Page {page_num}: {len(all_raw)} jobs total")

            if len(all_raw) == prev_count:
                print(f"  [{tenant}] No new jobs on page {page_num} — stopping")
                break

        await browser.close()

    if test:
        all_raw = all_raw[:20]
        print(f"  [{tenant}] TEST MODE: limiting to {len(all_raw)} jobs")
    print(f"  [{tenant}] Total intercepted: {len(all_raw)} jobs")

    # Build Job objects
    # Taleo returns data in column array: [title, location_json, date]
    import json as _json2
    for raw in all_raw:
        job_id   = str(raw.get("jobId", raw.get("requisitionId", raw.get("id", ""))))
        columns  = raw.get("column", [])
        title    = (columns[0] if columns else raw.get("jobTitle", raw.get("title", "")) or "").strip()
        loc_raw  = columns[1] if len(columns) > 1 else ""
        try:
            locs = _json2.loads(loc_raw) if loc_raw and loc_raw.startswith("[") else [loc_raw]
            location = locs[0].replace("US-", "").replace("-", ", ") if locs else ""
        except Exception:
            location = str(loc_raw)
        date_str = columns[2] if len(columns) > 2 else ""
        try:
            from datetime import datetime as _dt2
            date_posted = _dt2.strptime(date_str.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
        except Exception:
            date_posted = _parse_date(raw)
        desc     = _clean(raw.get("jobDescription", raw.get("description", "")) or "")
        apply_url = _build_apply_url(tenant, config["section"], job_id)

        jobs.append(Job(
            company                = company,
            title                  = title,
            job_id                 = job_id,
            location               = location,
            country                = _infer_country(location),
            salary                 = extract_salary(desc),
            remote                 = infer_remote(location, desc),
            seniority              = infer_seniority(title),
            us_citizenship_required= extract_citizenship(desc),
            security_clearance     = extract_clearance(desc),
            relocation_assistance  = extract_relocation(desc),
            source_platform        = "taleo",
            date_posted            = date_posted,
            apply_url              = apply_url,
            description_text       = desc,
        ))

    return jobs



# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(company_keys: list, output_dir: Path, test: bool = False) -> None:
    limiter  = RateLimiter(calls_per_minute=25)
    all_jobs = []

    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown: {key}. Available: {', '.join(COMPANIES)}")
            continue
        print(f"\nScraping {config['name']} ({config['tenant']}.taleo.net)...")
        if config.get("note"):
            print(f"  Note: {config['note']}")
        jobs = await scrape_company(config, limiter)
        print(f"  Done: {len(jobs)} jobs")
        all_jobs.extend(jobs)

    # Save per company - iterate jobs by company
    from collections import defaultdict
    jobs_by_company = defaultdict(list)
    for job in all_jobs:
        jobs_by_company[job.company].append(job)
    total = 0
    for company_jobs in jobs_by_company.values():
        if not company_jobs:
            continue
        if not sample_check(company_jobs[:20], company_jobs[0].company, "taleo"):
            continue
        key = company_jobs[0].company.lower().replace(" ", "_").replace("(", "").replace(")", "")
        output_path = output_dir / f"taleo_{key}.csv"
        save_jobs(company_jobs, output_path)
        total += len(company_jobs)
    print(f"\nTotal: {len(all_jobs)} jobs → {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Oracle Taleo ATS")
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()))
    parser.add_argument("--output-dir", type=Path, default=Path("data"),
                        help="Directory for output files (one CSV per company)")
    parser.add_argument("--test", action="store_true",
                        help="Test mode: scrape and enrich first 20 jobs only")
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output_dir, test=args.test))
