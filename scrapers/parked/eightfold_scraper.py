"""
eightfold_scraper.py — Scrapes companies using the Eightfold AI ATS.

Confirmed API (2026-06-12 via DevTools on jobs.northropgrumman.com):
  GET https://jobs.northropgrumman.com/search?domain=ngc.com&query=&location=&start=0&sort_by=timestamp
  Returns JSON with positions array, 10 per page, increment start by 10.

Fields returned per job:
  id, displayJobId, name, locations, standardizedLocations,
  postedTs, department, workLocationOption, atsJobId, positionUrl

Note: description requires a separate page visit per job.
The /api/apply/v2/jobs endpoint returns 403 — use /search instead.

Usage:
    python3 scrapers/eightfold_scraper.py --output-dir data
    python3 scrapers/eightfold_scraper.py --companies northrop --output-dir data
"""

import argparse
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from base import (
    Job, RateLimiter, infer_seniority, infer_remote, save_jobs, sample_check,
    extract_salary, extract_citizenship, extract_clearance, extract_relocation,
    _infer_country,
)

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "northrop": {
        "name":       "Northrop Grumman",
        "base_url":   "https://jobs.northropgrumman.com",
        "domain":     "ngc.com",
        "page_size":  10,
        "note":       "2,893 jobs confirmed 2026-06-12. Uses Eightfold /search API.",
    },
}

# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

async def scrape_company(config: dict, limiter: RateLimiter) -> list[Job]:
    """
    Scrape via Playwright browser fetch — inherits session cookies.
    Direct httpx calls return 403; browser fetch works.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not installed: python3 -m playwright install chromium")
        return []

    import json as _json

    name      = config["name"]
    base_url  = config["base_url"]
    domain    = config["domain"]
    page_size = config["page_size"]
    careers   = base_url + "/careers"
    jobs      = []
    start     = 0
    total     = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        print(f"  Loading {careers}...")
        await page.goto(careers, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2000)

        while True:
            await limiter.wait()
            url = f"{base_url}/search?domain={domain}&query=&location=&start={start}&sort_by=timestamp"

            result = await page.evaluate("""
                async ([url]) => {
                    try {
                        const resp = await fetch(url, {
                            headers: {"Accept": "application/json"},
                            credentials: "include"
                        });
                        return {status: resp.status, text: await resp.text()};
                    } catch(e) { return {status: 0, text: e.toString()}; }
                }
            """, [url])

            if result["status"] != 200:
                print(f"  [{name}] API returned {result['status']} at start={start}")
                break

            try:
                data = _json.loads(result["text"])
            except Exception as e:
                print(f"  [{name}] JSON error: {e}")
                break

            positions = data.get("positions", [])

            if total is None:
                total = data.get("count", data.get("totalCount", 0))
                print(f"  [{name}] Total jobs: {total}")

            if not positions:
                break

            for raw in positions:
                job_id    = str(raw.get("id", raw.get("atsJobId", "")))
                title     = (raw.get("name", "") or "").strip()
                locations = raw.get("standardizedLocations", raw.get("locations", []))
                location  = locations[0] if locations else ""
                pos_url   = raw.get("positionUrl", "")
                apply_url = base_url + pos_url if pos_url and not pos_url.startswith("http") else pos_url

                # Date from Unix timestamp
                posted_ts = raw.get("postedTs", 0)
                try:
                    date_posted = datetime.fromtimestamp(int(posted_ts) / 1000).strftime("%Y-%m-%d") if posted_ts else "N/A"
                except Exception:
                    date_posted = "N/A"

                remote = raw.get("workLocationOption", "")
                if remote not in ("remote", "hybrid", "onsite"):
                    remote = infer_remote(location)

                jobs.append(Job(
                    company                = name,
                    title                  = title,
                    job_id                 = job_id,
                    location               = location,
                    country                = _infer_country(location),
                    salary                 = "",
                    remote                 = remote,
                    seniority              = infer_seniority(title),
                    us_citizenship_required= "unknown",
                    security_clearance     = "unknown",
                    relocation_assistance  = "unknown",
                    source_platform        = "eightfold",
                    date_posted            = date_posted,
                    apply_url              = apply_url,
                    description_text       = "",
                ))

            start += len(positions)
            print(f"  [{name}] {start}/{total or '?'} jobs listed...")

            if total and start >= total:
                break

        await browser.close()

    return jobs


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(company_keys: list[str], output_dir: Path) -> None:
    limiter = RateLimiter(calls_per_minute=20)
    total   = 0

    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown company: {key}. Available: {', '.join(COMPANIES)}")
            continue

        print(f"\nScraping {config['name']}...")
        if config.get("note"):
            print(f"  Note: {config['note']}")

        jobs = await scrape_company(config, limiter)

        if not jobs:
            print(f"  No jobs found")
            continue

        if not sample_check(jobs[:20], config["name"], "eightfold"):
            continue

        output_path = output_dir / f"eightfold_{key}.csv"
        save_jobs(jobs, output_path)
        total += len(jobs)
        print(f"  Done: {len(jobs)} jobs → {output_path}")

    print(f"\nTotal: {total} jobs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Eightfold AI ATS job boards")
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()),
                        help=f"Companies to scrape. Options: {', '.join(COMPANIES.keys())}")
    parser.add_argument("--output-dir", type=Path, default=Path("data"),
                        help="Directory for output files (one CSV per company)")
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output_dir))
