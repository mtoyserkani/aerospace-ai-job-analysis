"""
successfactors_scraper.py — Scrapes companies using SAP SuccessFactors ATS.

SuccessFactors exposes a REST API:
  GET https://{instance}.successfactors.com/odata/v2/JobRequisitionLocale
  Or via the job board API endpoint (varies by instance config)

Companies:
  - Bombardier → bombardier.jobs / jobs.bombardier.com
  - Safran     → careers.safran-group.com

Detection: both confirmed via rmkcdn.successfactors.com CSS interception
in detect_ats.py run 2026-06-06.

Usage:
    python3 scrapers/successfactors_scraper.py --output data/sf_jobs.csv
    python3 scrapers/successfactors_scraper.py --companies bombardier
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
    "bombardier": {
        "name":        "Bombardier",
        "search_url":  "https://jobs.bombardier.com/search/?q=&searchResultView=LIST&pageNumber=1",
        "api_url":     "https://jobs.bombardier.com/services/recruiting/v1/jobs",
        "api_base":    "https://jobs.bombardier.com",
        "page_field":  "pageNumber",
        "page_size":   10,
        "payload": {
            "locale":        "en_US",
            "pageNumber":    1,
            "sortBy":        "",
            "keywords":      "",
            "location":      "",
            "facetFilters":  {},
            "brand":         "",
            "alertId":       "",
            "categoryId":    0,
            "rcmCandidateId":"",
            "skills":        [],
        },
        "note": "774 jobs, 78 pages. Confirmed via DevTools 2026-06-11. Requires X-Csrf-Token + session cookie — use Playwright browser fetch.",
    },
    "safran": {
        "name":       "Safran (North America)",
        "search_url": "https://careers.safran-group.com/accueil.aspx",
        "api_url":    None,
        "api_base":   "https://careers.safran-group.com",
        "note":       "Safran uses French-language SuccessFactors. Needs dom_inspector diagnosis.",
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["canada", "ontario", "toronto", "montreal", "quebec", "dorval"]):
        return "Canada"
    if any(k in loc for k in ["france", "paris", "toulouse", "bordeaux"]):
        return "France"
    if any(k in loc for k in ["uk", "england", "london"]):
        return "United Kingdom"
    if any(k in loc for k in ["germany", "berlin"]):
        return "Germany"
    return "United States of America"


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Playwright scraper (SuccessFactors requires session)
# ---------------------------------------------------------------------------

async def scrape_company(config: dict, limiter: RateLimiter) -> list[Job]:
    """
    SuccessFactors scraper using Playwright browser fetch.
    Bombardier: POST https://jobs.bombardier.com/services/recruiting/v1/jobs
    Requires X-Csrf-Token and session cookies — established by loading the search page.
    Payload confirmed via DevTools 2026-06-11.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not installed: python3 -m playwright install chromium")
        return []

    import json as _json
    import re as _re

    name       = config["name"]
    search_url = config["search_url"]
    api_url    = config.get("api_url")
    api_base   = config["api_base"]
    jobs       = []

    if not api_url:
        print(f"  [{name}] No API URL configured — skipping")
        return []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = await context.new_page()

        # Load search page to get CSRF token and session cookies
        print(f"  Loading {search_url}...")
        await page.goto(search_url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)

        # Extract CSRF token from cookies or meta tag
        csrf_token = await page.evaluate("""
            () => {
                // Try meta tag
                const meta = document.querySelector('meta[name="csrf-token"], meta[name="_csrf"]');
                if (meta) return meta.getAttribute('content');
                // Try cookie
                const cookies = document.cookie.split(';');
                for (const c of cookies) {
                    const [k, v] = c.trim().split('=');
                    if (k.toLowerCase().includes('csrf') || k.toLowerCase().includes('xsrf')) return v;
                }
                return null;
            }
        """)
        print(f"  CSRF token: {'found' if csrf_token else 'not found — will try without'}")

        # Get total pages from first response
        total_pages = None
        page_num = 1
        all_raw = []

        while True:
            await limiter.wait()
            payload = {**config["payload"], "pageNumber": page_num}

            result = await page.evaluate("""
                async ([url, payload, csrfToken]) => {
                    const headers = {
                        "Content-Type": "application/json",
                        "Accept": "*/*",
                    };
                    if (csrfToken) headers["X-Csrf-Token"] = csrfToken;
                    try {
                        const resp = await fetch(url, {
                            method: "POST",
                            headers: headers,
                            credentials: "include",
                            body: JSON.stringify(payload)
                        });
                        return {status: resp.status, text: await resp.text()};
                    } catch(e) {
                        return {status: 0, text: e.toString()};
                    }
                }
            """, [api_url, payload, csrf_token])

            if result["status"] != 200:
                print(f"  [{name}] Page {page_num}: HTTP {result['status']}: {result['text'][:100]}")
                break

            try:
                data = _json.loads(result["text"])
            except Exception as e:
                print(f"  [{name}] JSON error: {e}")
                break

            if total_pages is None:
                total_pages = data.get("totalNumberOfPages", data.get("totalPages", 0))
                total_count = data.get("totalCount", data.get("total", 0))
                print(f"  [{name}] {total_count} jobs across {total_pages} pages")

            raw_jobs = (
                data.get("jobSearchResult", []) or
                data.get("jobResults", []) or
                data.get("jobs", []) or
                data.get("positions", []) or
                []
            )

            # Compute totals from response
            total_count = data.get("totalJobs", data.get("totalCount", 0))
            if total_count and (total_pages is None or total_pages == 0):
                page_size = config.get("page_size", 10)
                total_pages = (total_count + page_size - 1) // page_size
                print(f"  [{name}] {total_count} jobs across {total_pages} pages")
            elif total_pages is None:
                total_pages = data.get("totalNumberOfPages", data.get("totalPages", 1))
                print(f"  [{name}] {total_pages} pages")

            if not raw_jobs and page_num == 1:
                print(f"  [{name}] No jobs in response. Keys: {list(data.keys())}")
                print(f"  First 300: {result['text'][:300]}")
                break

            if not raw_jobs:
                break

            all_raw.extend(raw_jobs)
            print(f"  [{name}] Page {page_num}/{total_pages}: +{len(raw_jobs)} jobs ({len(all_raw)} total)")

            if total_pages and page_num >= total_pages:
                break
            page_num += 1

        await browser.close()

    # Build Job objects
    import re as _re2
    for raw in all_raw:
        # SuccessFactors Bombardier wraps fields in response object
        resp = raw.get("response", raw)

        def _first(key):
            val = resp.get(key, raw.get(key, ""))
            if isinstance(val, list): return val[0] if val else ""
            return val or ""

        job_id    = str(_first("jobReqId") or _first("id") or _first("jobId"))
        title     = (_first("jobTitle") or _first("title") or "").strip()
        location  = (_first("jobLocationShort") or _first("city") or "").strip()
        # Clean HTML from location
        location  = _re2.sub(r"<[^>]+>", " ", location).strip()
        country_raw = _first("country") or ""
        apply_url = _first("applyUrl") or f"{api_base}/job/{job_id}"
        date_str  = _first("postingDate") or _first("postedDate") or ""
        m = _re2.search(r"(\d{4}-\d{2}-\d{2})", str(date_str))
        date_posted = m.group(1) if m else "N/A"
        desc = _clean(_first("jobDescription") or _first("description") or "")

        jobs.append(Job(
            company                = name,
            title                  = title,
            job_id                 = job_id,
            location               = location,
            country                = country_raw or _infer_country(location),
            salary                 = extract_salary(desc),
            remote                 = infer_remote(location, desc),
            seniority              = infer_seniority(title),
            us_citizenship_required= extract_citizenship(desc),
            security_clearance     = extract_clearance(desc),
            relocation_assistance  = extract_relocation(desc),
            source_platform        = "successfactors",
            date_posted            = date_posted,
            apply_url              = apply_url,
            description_text       = desc,
        ))

    return jobs



# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main(company_keys: list, output: Path) -> None:
    limiter  = RateLimiter(calls_per_minute=15)
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
    parser = argparse.ArgumentParser(description="Scrape SAP SuccessFactors ATS")
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()))
    parser.add_argument("--output",    type=Path, default=Path("data/sf_jobs.csv"))
    args = parser.parse_args()
    asyncio.run(main(args.companies, args.output))
