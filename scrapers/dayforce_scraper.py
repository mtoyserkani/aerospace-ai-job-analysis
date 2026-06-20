"""
dayforce_scraper.py — Scrapes companies using the Dayforce (Ceridian) HCM job board.

KEY FINDING (confirmed 2026-06-20):
  Dayforce exposes a clean JSON API:
    POST https://jobs.dayforcehcm.com/api/geo/{namespace}/jobposting/search
    Body: {"clientNamespace": ..., "jobBoardCode": ..., "cultureCode": "en-US",
           "distanceUnit": 0, "paginationStart": N}
  Response includes FULL job data inline — title, description, location —
  no separate enrichment phase needed, unlike iCIMS/Workday/Taleo.

  HOWEVER: Cloudflare blocks this endpoint when called via:
    - plain `requests` library (403 Forbidden)
    - Playwright page.evaluate() calling fetch() directly (403 Forbidden)
  Only a REAL user-triggered click (via Playwright .click()) on the pagination
  control succeeds (200 OK). Cloudflare is fingerprinting something about
  script-invoked fetch vs. genuine click-triggered requests — not just
  cookies/session, since both attempts ran in the same authenticated page.

  Workaround: navigate normally, then click the pagination "next" control
  for each page and intercept the resulting response via page.on("response").
  Never call the API directly.

Companies confirmed using Dayforce:
  - Elbit America (Elbit Systems of America) → jobs.dayforcehcm.com/esa/ESACAREERSITE
    ~175 jobs across 7 pages, 25 jobs/page, confirmed 2026-06-20.
    (Previously misconfigured as iCIMS — careers-elbitsystemsofamerica.icims.com
    is dead; Elbit migrated to Dayforce. Update COMPANIES in icims_scraper.py
    to remove this stale entry.)

To add a new Dayforce company:
  1. Confirm their careers page is on jobs.dayforcehcm.com
  2. Find the namespace (e.g. "esa") and jobBoardCode (e.g. "ESACAREERSITE")
     — visible in the search request payload via DevTools Network tab
  3. Add to COMPANIES dict below
  4. Run and verify pagination click + intercept works

Usage:
    python3 scrapers/dayforce_scraper.py --companies elbitamerica --output-dir data

NOTE: A browser window will open and navigate/click automatically. Do not interact with it.
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
    _infer_country,
)

COMPANIES = {
    "elbitamerica": {
        "name":            "Elbit America",
        "search_url":      "https://jobs.dayforcehcm.com/esa/ESACAREERSITE",
        "client_namespace": "esa",
        "job_board_code":  "ESACAREERSITE",
        "jobs_per_page":   25,
        "note":            "Migrated off iCIMS to Dayforce. Confirmed 2026-06-20.",
    },
}


def strip_html(raw: str) -> str:
    """Strip HTML tags and decode common entities from Dayforce's jobDescription field."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = (text.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&ndash;", "-")
                .replace("&rsquo;", "'")
                .replace("&bull;", "-"))
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def parse_job(raw: dict, company_name: str, search_url: str) -> Job:
    """Convert one Dayforce jobPostings[] entry into our normalized Job schema."""
    job_id = str(raw.get("jobPostingId", ""))
    title = raw.get("jobTitle", "").strip()
    desc_html = raw.get("jobDescription", "")
    desc = strip_html(desc_html)

    locations = raw.get("postingLocations") or []
    location = locations[0].get("formattedAddress", "") if locations else ""

    apply_url = f"{search_url.rsplit('/', 1)[0]}/jobs/{job_id}" if job_id else search_url

    return Job(
        company=company_name,
        title=title,
        job_id=job_id,
        location=location,
        country=_infer_country(location),
        salary=extract_salary(desc),
        remote=infer_remote(location, desc),
        seniority=infer_seniority(title),
        us_citizenship_required=extract_citizenship(desc),
        security_clearance=extract_clearance(desc),
        relocation_assistance=extract_relocation(desc),
        source_platform="dayforce",
        date_posted=(raw.get("postingStartTimestampUTC") or "N/A")[:10],
        apply_url=apply_url,
        description_text=desc,
    )


async def scrape_company(key: str, config: dict, limiter: RateLimiter) -> list:
    from playwright.async_api import async_playwright

    company_name = config["name"]
    search_url = config["search_url"]
    jobs_per_page = config.get("jobs_per_page", 25)

    all_raw_jobs = {}  # keyed by jobPostingId to dedupe

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=150)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        captured = {}

        async def handle_response(response):
            if "jobposting/search" in response.url and response.status == 200:
                try:
                    body = await response.json()
                    captured["body"] = body
                except Exception:
                    pass

        page.on("response", lambda r: asyncio.create_task(handle_response(r)))

        print(f"  Loading search page...")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(6000)  # let Cloudflare clearance settle

        # First page loads automatically on goto — capture it
        if "body" in captured:
            for j in captured["body"].get("jobPostings", []):
                all_raw_jobs[j["jobPostingId"]] = j
            print(f"  Page 1: {len(captured['body'].get('jobPostings', []))} jobs (total: {len(all_raw_jobs)})")
        captured.clear()

        # Detect total pages from pagination control text (numbered buttons)
        page_num = 2
        max_pages = 30  # safety cap
        while page_num <= max_pages:
            await limiter.wait()
            try:
                btn = page.get_by_text(str(page_num), exact=True).first
                await btn.click(timeout=8000)
            except Exception:
                print(f"  No page {page_num} control found — stopping pagination.")
                break

            await page.wait_for_timeout(3500)

            if "body" not in captured:
                print(f"  Page {page_num}: no response captured — stopping pagination.")
                break

            new_jobs = captured["body"].get("jobPostings", [])
            before = len(all_raw_jobs)
            for j in new_jobs:
                all_raw_jobs[j["jobPostingId"]] = j
            after = len(all_raw_jobs)

            print(f"  Page {page_num}: {len(new_jobs)} jobs (total: {after})")
            captured.clear()

            if after == before:
                # No new unique jobs — likely looped back or end of results
                print(f"  No new jobs on page {page_num} — stopping pagination.")
                break

            page_num += 1

        await browser.close()

    jobs = [parse_job(raw, company_name, search_url) for raw in all_raw_jobs.values()]
    return jobs


async def main(company_keys: list, output_dir: Path) -> None:
    limiter = RateLimiter(calls_per_minute=20)
    total = 0

    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown company: {key}. Available: {', '.join(COMPANIES)}")
            continue

        print(f"\nScraping {config['name']}... (browser will open — do not interact)")
        if config.get("note"):
            print(f"  Note: {config['note']}")

        jobs = await scrape_company(key, config, limiter)
        print(f"  Total: {len(jobs)} jobs")

        if not jobs:
            print(f"  No jobs found — skipping")
            continue

        if not sample_check(jobs[:20], config["name"], "dayforce"):
            print(f"  Skipping save due to sample check failure.")
            continue

        output_path = output_dir / f"dayforce_{key}.csv"
        save_jobs(jobs, output_path)
        total += len(jobs)

    print(f"\nDone: {total} total jobs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Dayforce HCM job boards")
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()),
                        help=f"Companies to scrape. Options: {', '.join(COMPANIES.keys())}")
    parser.add_argument("--output-dir", type=Path, default=Path("data"),
                        help="Directory for output files (one CSV per company)")
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output_dir))
