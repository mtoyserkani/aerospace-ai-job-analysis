"""
phenom_scraper.py — Scrapes companies using the Phenom People ATS.

Phenom is a React SPA with a GraphQL API. The API endpoint and schema
are consistent across tenants but require reverse-engineering from
browser network traffic.

Companies in this dataset using Phenom:
  - Northrop Grumman  → ngccareers.northropgrumman.com
  - GE Aerospace       → careers.geaerospace.com

KNOWN ISSUE — GE Aerospace selector mismatch:
  The GE Aerospace Phenom instance paginates differently from Northrop.
  Run dom_inspector.py against careers.geaerospace.com to diagnose.
  Current dataset likely undercounts GE Aerospace postings.

Usage:
    pip install playwright && playwright install chromium
    python phenom_scraper.py --output data/phenom_jobs.csv
    python phenom_scraper.py --companies ge --output data/ge_jobs.csv
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from base import Job, RateLimiter, infer_seniority, infer_remote, save_jobs

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "northrop": {
        "name":        "Northrop Grumman",
        "base_url":    "https://ngccareers.northropgrumman.com",
        "api_path":    "/api/apply/v2/jobs",
        "tenant":      "ngccareers",
        "known_issue": None,
    },
    "ge": {
        "name":        "GE Aerospace",
        "base_url":    "https://careers.geaerospace.com",
        "api_path":    "/api/apply/v2/jobs",
        "tenant":      "geaerospace",
        "known_issue": "Pagination selector mismatch — run dom_inspector.py to diagnose",
    },
}

# ---------------------------------------------------------------------------
# Phenom API — reverse-engineered from browser traffic
# ---------------------------------------------------------------------------

PHENOM_API_HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Content-Type":    "application/json",
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

PHENOM_PAYLOAD_TEMPLATE = {
    "lang":   "en_US",
    "offset": 0,
    "limit":  20,
    "sortBy": "relevance",
    "facets": {},
    "text":   "",
}


async def scrape_via_api(config: dict, limiter: RateLimiter) -> list[Job]:
    """
    Query Phenom's internal /api/apply/v2/jobs endpoint.
    This avoids Playwright for the listing phase (though descriptions
    still require page visits if the API doesn't return full text).
    """
    try:
        import httpx
    except ImportError:
        print("  httpx not installed: pip install httpx")
        return []

    base_url = config["base_url"]
    api_url  = base_url + config["api_path"]
    company  = config["name"]

    jobs     = []
    offset   = 0
    total    = None

    async with httpx.AsyncClient(
        headers=PHENOM_API_HEADERS,
        follow_redirects=True,
        verify=False,  # Some Phenom instances have cert issues
    ) as client:
        while True:
            await limiter.wait()
            payload = {**PHENOM_PAYLOAD_TEMPLATE, "offset": offset}

            try:
                resp = await client.post(api_url, json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  [{company}] API error at offset {offset}: {e}")
                print(f"  Falling back to Playwright...")
                return await scrape_via_playwright(config, limiter)

            if total is None:
                total = data.get("total", data.get("count", 0))

            raw_jobs = (
                data.get("positions", []) or
                data.get("jobs", []) or
                data.get("requisitions", []) or
                []
            )

            if not raw_jobs:
                break

            for raw in raw_jobs:
                job_id    = str(raw.get("id", raw.get("reqId", Job.stable_id(str(raw)))))
                title     = (raw.get("title", "") or raw.get("jobTitle", "")).strip()
                location  = _extract_location(raw)
                apply_url = _build_apply_url(base_url, raw)
                desc      = _extract_description(raw)

                jobs.append(Job(
                    job_id          = job_id,
                    title           = title,
                    company         = company,
                    location        = location,
                    country         = _infer_country(location),
                    remote          = infer_remote(location, desc),
                    apply_url       = apply_url,
                    description_text= desc,
                    seniority       = infer_seniority(title),
                    salary          = "",
                    source_platform = "phenom",
                ))

            offset += len(raw_jobs)
            print(f"  [{company}] {offset}/{total or '?'} jobs...")

            if total and offset >= total:
                break

    return jobs


async def scrape_via_playwright(config: dict, limiter: RateLimiter) -> list[Job]:
    """
    Playwright fallback for Phenom instances where the API returns errors
    or where the API schema differs (known issue: GE Aerospace pagination).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not installed: pip install playwright && playwright install chromium")
        return []

    company  = config["name"]
    base_url = config["base_url"]
    jobs     = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = await context.new_page()

        print(f"  Loading {base_url}...")
        await page.goto(base_url, wait_until="networkidle", timeout=60000)

        page_num = 0
        while True:
            await limiter.wait()
            page_num += 1

            # Wait for job cards
            try:
                await page.wait_for_selector(
                    "[class*='job-card'], [class*='JobCard'], [data-ph-at-id*='job']",
                    timeout=15000
                )
            except Exception:
                print(f"  Page {page_num}: no job cards found — may be end of results")
                break

            # Extract job cards
            cards = await page.query_selector_all(
                "[class*='job-card'], [class*='JobCard'], [data-ph-at-id*='job']"
            )
            print(f"  Page {page_num}: {len(cards)} cards")

            for card in cards:
                try:
                    title_el  = await card.query_selector("h2, h3, [class*='title']")
                    title     = (await title_el.inner_text()).strip() if title_el else ""
                    link_el   = await card.query_selector("a[href]")
                    href      = await link_el.get_attribute("href") if link_el else ""
                    apply_url = href if href.startswith("http") else base_url + href
                    loc_el    = await card.query_selector("[class*='location'], [class*='Location']")
                    location  = (await loc_el.inner_text()).strip() if loc_el else ""

                    jobs.append(Job(
                        job_id          = Job.stable_id(apply_url),
                        title           = title,
                        company         = company,
                        location        = location,
                        country         = _infer_country(location),
                        remote          = infer_remote(location),
                        apply_url       = apply_url,
                        description_text= "",  # Fetched separately
                        seniority       = infer_seniority(title),
                        salary          = "",
                        source_platform = "phenom",
                    ))
                except Exception as e:
                    print(f"  Card parse error: {e}")

            # Pagination — try "Next" button
            next_btn = await page.query_selector(
                "button[aria-label*='Next'], [class*='pagination'] button:last-child, "
                "button:has-text('Next'), [data-ph-at-id*='pagination-next']"
            )
            if not next_btn:
                break
            is_disabled = await next_btn.get_attribute("disabled")
            if is_disabled is not None:
                break
            await next_btn.click()
            await page.wait_for_load_state("networkidle")

        await browser.close()

    return jobs


def _extract_location(raw: dict) -> str:
    for key in ["location", "city", "locationName", "primaryLocation"]:
        if raw.get(key):
            return str(raw[key])
    return ""


def _build_apply_url(base_url: str, raw: dict) -> str:
    job_id = raw.get("id", raw.get("reqId", ""))
    if not job_id:
        return ""
    return f"{base_url}/global/en/job/{job_id}/"


def _extract_description(raw: dict) -> str:
    for key in ["description", "jobDescription", "summary", "jobSummary"]:
        val = raw.get(key, "")
        if val:
            return re.sub(r"<[^>]+>", " ", str(val)).strip()
    return ""


def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["canada", "ontario", "toronto"]):
        return "CA"
    return "US"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(company_keys: list[str], output: Path) -> None:
    limiter  = RateLimiter(calls_per_minute=20)
    all_jobs = []

    for key in company_keys:
        config = COMPANIES.get(key)
        if not config:
            print(f"Unknown: {key}. Available: {', '.join(COMPANIES)}")
            continue

        if config.get("known_issue"):
            print(f"\n⚠️  Known issue for {config['name']}: {config['known_issue']}")

        print(f"\nScraping {config['name']}...")
        jobs = await scrape_via_api(config, limiter)
        print(f"  Total: {len(jobs)} jobs")
        all_jobs.extend(jobs)

    save_jobs(all_jobs, output)
    print(f"\nTotal: {len(all_jobs)} jobs from {len(company_keys)} companies")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Phenom People ATS job boards")
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()))
    parser.add_argument("--output", type=Path, default=Path("data/phenom_jobs.csv"))
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output))
