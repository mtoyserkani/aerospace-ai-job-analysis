"""
brassring_scraper.py — Scrapes companies using IBM Kenexa BrassRing ATS.

BrassRing is IBM's enterprise ATS used by large defense primes.
It has no public API — requires Playwright to navigate the job search UI.

Architecture:
  - Job search runs at https://kenexa.com/psc/TENANT/...
  - Or via the company's own domain redirecting to BrassRing
  - Pagination via URL params or form submission
  - Job descriptions on individual detail pages

Companies:
  - Lockheed Martin → lockheedmartin.com/en-us/careers
  - Sikorsky         → same BrassRing instance (Lockheed subsidiary)

Detection notes (from detect_ats.py run 2026-06-06):
  Lockheed Martin confirmed BrassRing via source signature.
  Sikorsky redirects to Lockheed Martin careers (same instance).

Usage:
    python3 -m playwright install chromium
    python3 scrapers/brassring_scraper.py --output data/brassring_jobs.csv
    python3 scrapers/brassring_scraper.py --companies lockheed --output data/lm_jobs.csv
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from base import (
    Job, RateLimiter, infer_seniority, infer_remote, save_jobs,
    extract_salary, extract_citizenship, extract_clearance, extract_relocation,
)

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "lockheed": {
        "name":       "Lockheed Martin",
        "search_url": "https://www.lockheedmartinjobs.com/search-jobs",
        "base_url":   "https://www.lockheedmartinjobs.com",
        "note":       "BrassRing via lockheedmartinjobs.com. Includes Sikorsky roles.",
    },
}

# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["canada", "ontario", "toronto"]):
        return "Canada"
    if any(k in loc for k in ["uk", "united kingdom", "england"]):
        return "United Kingdom"
    return "United States of America"


def _clean_text(html_or_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_or_text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

async def scrape_company(config: dict, limiter: RateLimiter) -> list[Job]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not installed: python3 -m playwright install chromium")
        return []

    name       = config["name"]
    search_url = config["search_url"]
    base_url   = config["base_url"]
    jobs       = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        # Intercept API calls to find job data endpoint
        api_responses = []

        async def handle_response(response):
            if any(x in response.url for x in ["search-jobs", "job-search", "/jobs/", "api"]):
                if "json" in response.headers.get("content-type", ""):
                    try:
                        body = await response.text()
                        api_responses.append({"url": response.url, "body": body})
                    except Exception:
                        pass

        page.on("response", handle_response)

        print(f"  Loading {search_url}...")
        await page.goto(search_url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)

        # Get total job count
        total_text = ""
        for selector in ["[class*='total']", "[class*='count']", "[class*='result']"]:
            el = await page.query_selector(selector)
            if el:
                total_text = await el.inner_text()
                if any(c.isdigit() for c in total_text):
                    break

        total_match = re.search(r"([\d,]+)\s*(?:jobs|results|positions)", total_text, re.IGNORECASE)
        total = int(total_match.group(1).replace(",", "")) if total_match else 0
        print(f"  Total jobs: {total or 'unknown'}")

        # Scrape job cards from search results
        page_num = 1
        while True:
            await limiter.wait()

            # Extract job cards from current page
            cards = await page.query_selector_all(
                "[class*='job-result'], [class*='job-card'], "
                "[class*='search-result'], li[class*='result'], "
                "article[class*='job']"
            )

            if not cards:
                # Fallback: find all job links
                cards = await page.query_selector_all("a[href*='/job/'], a[href*='/jobs/']")

            if not cards and page_num == 1:
                print(f"  No job cards found on page 1. Dumping page structure...")
                h_tags = await page.query_selector_all("h2, h3")
                for h in h_tags[:5]:
                    print(f"    heading: {await h.inner_text()}")
                break

            page_jobs_count = 0
            for card in cards:
                try:
                    # Get link
                    link = await card.query_selector("a") if await card.get_attribute("href") is None else card
                    href = await link.get_attribute("href") if link else ""
                    if not href or "job" not in href.lower():
                        continue

                    apply_url = href if href.startswith("http") else base_url + href
                    job_id = apply_url.rstrip("/").split("/")[-1]

                    # Get title
                    title_el = await card.query_selector("h2, h3, h4, [class*='title'], [class*='name']")
                    title = await title_el.inner_text() if title_el else await card.inner_text()
                    title = _clean_text(title).split("\n")[0][:150]

                    # Get location
                    loc_el = await card.query_selector("[class*='location'], [class*='city'], span[class*='loc']")
                    location = _clean_text(await loc_el.inner_text()) if loc_el else ""

                    if title and len(title) > 3:
                        jobs.append(Job(
                            company                = name,
                            title                  = title,
                            job_id                 = job_id,
                            location               = location,
                            country                = _infer_country(location),
                            salary                 = "",
                            remote                 = infer_remote(location),
                            seniority              = infer_seniority(title),
                            us_citizenship_required= "unknown",
                            security_clearance     = "unknown",
                            relocation_assistance  = "unknown",
                            source_platform        = "brassring",
                            date_posted            = "N/A",
                            apply_url              = apply_url,
                            description_text       = "",
                        ))
                        page_jobs_count += 1
                except Exception:
                    continue

            print(f"  Page {page_num}: {page_jobs_count} jobs extracted ({len(jobs)} total)")

            # Try to go to next page
            next_btn = await page.query_selector(
                "a[aria-label='Next'], button[aria-label='Next'], "
                "[class*='next']:not([disabled]), a[class*='next']"
            )
            if not next_btn:
                break

            try:
                await next_btn.click()
                await page.wait_for_timeout(2000)
                await limiter.wait()
                page_num += 1
            except Exception:
                break

        await browser.close()

    return jobs


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(company_keys: list, output: Path) -> None:
    limiter  = RateLimiter(calls_per_minute=20)
    all_jobs = []

    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown: {key}. Available: {', '.join(COMPANIES)}")
            continue
        print(f"\nScraping {config['name']}...")
        if config.get("note"):
            print(f"  Note: {config['note']}")
        jobs = await scrape_company(config, limiter)
        print(f"  Done: {len(jobs)} jobs")
        all_jobs.extend(jobs)

    save_jobs(all_jobs, output)
    print(f"\nTotal: {len(all_jobs)} jobs → {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape IBM BrassRing ATS")
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()))
    parser.add_argument("--output",    type=Path, default=Path("data/brassring_jobs.csv"))
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output))
