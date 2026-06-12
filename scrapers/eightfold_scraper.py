"""
eightfold_scraper.py — Scrapes companies using Eightfold AI ATS.

Eightfold exposes a clean public API used by their career hub.
Confirmed via network capture on jobs.northropgrumman.com 2026-06-07.

API endpoint:
  GET https://jobs.northropgrumman.com/api/apply/v2/jobs?domain=ngc.com&query=*&start=0&num=100&exclude_pid=&include_pid=&pid=

Companies:
  - Northrop Grumman → jobs.northropgrumman.com (domain: ngc.com)

Usage:
    python3 scrapers/eightfold_scraper.py --output data/northrop_jobs.csv
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

COMPANIES = {
    "northrop": {
        "name":       "Northrop Grumman",
        "base_url":   "https://jobs.northropgrumman.com",
        "api_url":    "https://jobs.northropgrumman.com/api/apply/v2/jobs",
        "domain":     "ngc.com",
        "page_size":  100,
    },
}

HEADERS = {
    "Accept":       "application/json, text/plain, */*",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":      "https://jobs.northropgrumman.com/careers",
}


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["canada", "ontario", "australia"]):
        return "Canada" if "canada" in loc or "ontario" in loc else "Australia"
    if any(k in loc for k in ["uk", "england", "united kingdom"]):
        return "United Kingdom"
    return "United States of America"


def _parse_date(raw: dict) -> str:
    for key in ["t_update", "t_create"]:
        val = raw.get(key)
        if val:
            from datetime import datetime
            try:
                return datetime.fromtimestamp(int(val)).strftime("%Y-%m-%d")
            except Exception:
                pass
    return "N/A"


async def scrape_company(config: dict, limiter: RateLimiter) -> list[Job]:
    """
    Eightfold blocks direct API calls — use Playwright browser fetch.
    The careers page loads job data via the same API but with browser context.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not installed: python3 -m playwright install chromium")
        return []

    import json as _json

    name      = config["name"]
    api_url   = config["api_url"]
    domain    = config["domain"]
    base_url  = config["base_url"]
    page_size = config["page_size"]
    careers_url = base_url + "/careers"
    jobs      = []
    start     = 0
    total     = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = await context.new_page()

        print(f"  Loading {careers_url}...")
        await page.goto(careers_url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2000)

        while True:
            await limiter.wait()
            url = f"{api_url}?domain={domain}&query=*&start={start}&num={page_size}&exclude_pid=&include_pid=&pid="

            result = await page.evaluate("""
                async ([url]) => {
                    try {
                        const resp = await fetch(url, {
                            headers: {
                                "Accept": "application/json",
                                "Referer": window.location.href
                            },
                            credentials: "include"
                        });
                        return {status: resp.status, text: await resp.text()};
                    } catch(e) { return {status: 0, text: e.toString()}; }
                }
            """, [url])

            if result.get("status") != 200:
                print(f"  [{name}] API returned {result.get('status')}: {result.get('text','')[:100]}")
                break

            try:
                data = _json.loads(result["text"])
            except Exception as e:
                print(f"  [{name}] JSON error: {e}")
                break

            if total is None:
                total = data.get("count", data.get("total", 0))
                print(f"  [{name}] Total jobs: {total}")

            positions = data.get("positions", data.get("jobs", []))
            if not positions:
                break

            for raw in positions:
                job_id    = str(raw.get("id", raw.get("ats_job_id", "")))
                title     = (raw.get("name", raw.get("posting_name", "")) or "").strip()
                location  = (raw.get("location", "") or "").strip()
                apply_url = raw.get("canonicalPositionUrl", f"{base_url}/careers/job/{job_id}")
                desc      = _clean(raw.get("job_description", "") or "")
                date_posted = _parse_date(raw)

                jobs.append(Job(
                    company                = name,
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
                    source_platform        = "eightfold",
                    date_posted            = date_posted,
                    apply_url              = apply_url,
                    description_text       = desc,
                ))

            start += len(positions)
            print(f"  [{name}] {start}/{total or '?'} jobs listed...")

            if total and start >= total:
                break

        await browser.close()

    return jobs


async def main(company_keys: list, output: Path) -> None:
    limiter  = RateLimiter(calls_per_minute=20)
    all_jobs = []
    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown: {key}. Available: {', '.join(COMPANIES)}")
            continue
        print(f"\nScraping {config['name']}...")
        jobs = await scrape_company(config, limiter)
        print(f"  Done: {len(jobs)} jobs")
        all_jobs.extend(jobs)
    save_jobs(all_jobs, output)
    print(f"\nTotal: {len(all_jobs)} jobs → {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Eightfold AI ATS")
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()))
    parser.add_argument("--output", type=Path, default=Path("data/eightfold_jobs.csv"))
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output))
