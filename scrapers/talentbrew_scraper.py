"""
talentbrew_scraper.py — Scrapes L3Harris via TalentBrew/IBM Kenexa ATS.

ARCHITECTURE (reverse-engineered 2026-06-06):
  The /results endpoint returns JSON with a 'results' key containing an HTML
  fragment. Pagination metadata (total pages, total jobs) is embedded as
  data-* attributes on the <section id="search-results"> element.
  Job cards are <li> elements inside that section.

  The endpoint requires:
    1. Correct query params (IsPagination, SearchType=5, SortDirection=1 etc.)
    2. A PersonalizationCookie with geolocation to trigger hasContent=true
       NOTE: hasContent can be False even when results HTML is present and
       contains real job data — ignore hasContent, parse results HTML directly.

  Strategy:
    - Playwright loads the search page to establish the full session
      (SearchSessionId, PersonalizationCookie set by JS geolocation detection)
    - We then paginate via browser fetch() which inherits all cookies
    - Parse job cards from HTML in the results field

  Result: 1,673 jobs across 112 pages confirmed.

Usage:
    python3 -m playwright install chromium  (first time only)
    python3 scrapers/talentbrew_scraper.py --output data/l3harris_jobs.csv
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from base import (
    Job, RateLimiter, infer_seniority, infer_remote, save_jobs, sample_check,
    extract_salary, extract_citizenship, extract_clearance, extract_relocation,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Company registry
# ---------------------------------------------------------------------------

COMPANIES = {
    "l3harris": {
        "name":       "L3Harris",
        "base_url":   "https://careers.l3harris.com",
        "search_url": "https://careers.l3harris.com/en/search-jobs",
        "list_url":   "https://careers.l3harris.com/en/search-jobs/results",
        "params": (
            "ActiveFacetID=0"
            "&RecordsPerPage=15"
            "&TotalContentResults="
            "&Distance=50"
            "&RadiusUnitType=0"
            "&Keywords="
            "&Location="
            "&ShowRadius=False"
            "&IsPagination=False"
            "&CustomFacetName="
            "&FacetTerm="
            "&FacetType=0"
            "&SearchResultsModuleName=Search+Results"
            "&SearchFiltersModuleName=Search+Filters"
            "&SortCriteria=0"
            "&SortDirection=1"
            "&SearchType=5"
            "&PostalCode="
            "&ResultsType=0"
            "&fc=&fl=&fcf=&afc=&afl=&afcf="
            "&TotalContentPages=NaN"
        ),
        "note": "Requires Playwright session with geolocation cookie. 1,673 jobs confirmed.",
    },
    "boeing": {
        "name":       "The Boeing Company",
        "base_url":   "https://jobs.boeing.com",
        "search_url": "https://jobs.boeing.com/search-jobs",
        "list_url":   "https://jobs.boeing.com/search-jobs/results",
        "params": (
            "ActiveFacetID=0"
            "&RecordsPerPage=15"
            "&TotalContentResults="
            "&Distance=50"
            "&RadiusUnitType=0"
            "&Keywords="
            "&Location="
            "&ShowRadius=False"
            "&IsPagination=False"
            "&CustomFacetName="
            "&FacetTerm="
            "&FacetType=0"
            "&SearchResultsModuleName=Search+Results"
            "&SearchFiltersModuleName=Search+Filters"
            "&SortCriteria=0"
            "&SortDirection=1"
            "&SearchType=5"
            "&PostalCode="
            "&ResultsType=0"
            "&fc=&fl=&fcf=&afc=&afl=&afcf="
            "&TotalContentPages=NaN"
        ),
        "note": "Boeing TalentBrew instance. Confirmed via detect_ats.py 2026-06-06.",
    },
}

# Legacy single-company constants — kept for backward compat with _list_url
COMPANY    = "L3Harris"
BASE_URL   = "https://careers.l3harris.com"
SEARCH_URL = BASE_URL + "/en/search-jobs"
LIST_URL   = BASE_URL + "/en/search-jobs/results"
LIST_PARAMS = COMPANIES["l3harris"]["params"]

def _list_url(page: int, config: dict = None) -> str:
    if config:
        return f"{config['list_url']}?CurrentPage={page}&{config['params']}"
    return f"{LIST_URL}?CurrentPage={page}&{LIST_PARAMS}"


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------

def _parse_total_pages(html: str) -> tuple[int, int]:
    """
    Extract total pages and total jobs from the <section> data attributes.
    <section data-total-results="1673" data-total-pages="112" ...>
    """
    pages_match = re.search(r'data-total-pages=["\'](\d+)["\']', html)
    count_match = re.search(r'data-total-(?:results|job-results)=["\'](\d+)["\']', html)
    total_pages = int(pages_match.group(1)) if pages_match else 0
    total_count = int(count_match.group(1)) if count_match else 0
    return total_pages, total_count


def _parse_job_cards(html: str) -> list[dict]:
    """Parse job cards. Handles L3Harris and Boeing TalentBrew HTML."""
    import re as _re
    jobs = []
    card_pattern = _re.compile(r"<li[^>]*>(.*?)</li>", _re.DOTALL | _re.IGNORECASE)
    for card_match in card_pattern.finditer(html):
        card = card_match.group(1)
        # Find link with data-job-id
        m = _re.search(r'<a[^>]+href=[\'"]([^\'"]+)[\'"][^>]*data-job-id=[\'"]([^\'"]+)[\'"]', card, _re.DOTALL | _re.IGNORECASE)
        if not m:
            m = _re.search(r'<a[^>]+data-job-id=[\'"]([^\'"]+)[\'"][^>]*href=[\'"]([^\'"]+)[\'"]', card, _re.DOTALL | _re.IGNORECASE)
            if not m: continue
            job_id, raw_url = m.group(1).strip(), m.group(2).strip()
        else:
            raw_url, job_id = m.group(1).strip(), m.group(2).strip()
        apply_url = raw_url if raw_url.startswith("http") else BASE_URL + raw_url
        # Title
        title = ""
        for pat in [r'<span[^>]*class="[^"]*job-title[^"]*"[^>]*>(.*?)</span>', r"<h2[^>]*>(.*?)</h2>"]:
            t = _re.search(pat, card, _re.DOTALL | _re.IGNORECASE)
            if t:
                title = _re.sub(r"<[^>]+>", " ", t.group(1)).strip()
                if title: break
        if not title: continue
        # Location
        location = ""
        for pat in [r'<span[^>]*class="[^"]*location[^"]*"[^>]*>(.*?)</span>', r'<span[^>]*class="[^"]*job-location[^"]*"[^>]*>(.*?)</span>']:
            l = _re.search(pat, card, _re.DOTALL | _re.IGNORECASE)
            if l:
                location = _re.sub(r"<[^>]+>", " ", l.group(1)).strip()
                if location: break
        jobs.append({"job_id": job_id, "title": title, "apply_url": apply_url, "location": location, "desc": ""})
    return jobs

def _infer_country(location: str) -> str:
    loc = location.lower()
    if any(k in loc for k in ["canada", "ontario", "toronto", "quebec", "british columbia"]):
        return "Canada"
    if any(k in loc for k in ["uk", "united kingdom", "england"]):
        return "United Kingdom"
    return "United States of America"


def _parse_date(raw: dict) -> str:
    for key in ["PostedDate", "DatePosted", "PostingDate"]:
        val = raw.get(key, "")
        if val:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", str(val))
            if m:
                return m.group(1)
    return "N/A"


# ---------------------------------------------------------------------------
# Main Playwright scraper
# ---------------------------------------------------------------------------

async def scrape(output_dir: Path, config: dict = None) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed. Run:")
        print("  python3 -m playwright install chromium")
        return

    limiter = RateLimiter(calls_per_minute=20)
    all_jobs: list[Job] = []
    total_pages = None
    total_count = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = await context.new_page()

        # Load search page — JS runs, geolocation detected, cookies set
        _search_url = config.get("search_url", SEARCH_URL)
        print(f"Loading {_search_url} (establishing session)...")
        await page.goto(_search_url, wait_until="networkidle", timeout=60000)
        # Give geolocation JS time to fire and set PersonalizationCookie
        await page.wait_for_timeout(3000)
        print("Session established. Starting paginated scrape...")

        page_num = 1
        while True:
            await limiter.wait()
            url = _list_url(page_num, config)

            # Fetch via browser context — inherits all session cookies
            result = await page.evaluate("""
                async (url) => {
                    const resp = await fetch(url, {
                        headers: {
                            'Accept': '*/*',
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        credentials: 'include'
                    });
                    return { status: resp.status, body: await resp.text() };
                }
            """, url)

            if result["status"] != 200:
                print(f"  Page {page_num}: HTTP {result['status']} — stopping")
                break

            try:
                data = json.loads(result["body"])
            except Exception as e:
                print(f"  Page {page_num}: JSON parse error — {e}")
                break

            results_html = data.get("results", "")

            # Parse total pages from first response
            if total_pages is None:
                total_pages, total_count = _parse_total_pages(results_html)
                if total_pages:
                    print(f"  {total_count} jobs across {total_pages} pages")
                else:
                    print(f"  Warning: could not parse total pages from page 1 HTML")
                    print(f"  results HTML length: {len(results_html)}")
                    if not results_html:
                        print("  Empty results — session may not have geolocation. Retrying after longer wait...")
                        await page.wait_for_timeout(5000)
                        continue

            if not results_html:
                print(f"  Page {page_num}: empty results — stopping")
                break

            cards = _parse_job_cards(results_html)
            if not cards:
                print(f"  Page {page_num}: no job cards parsed — stopping")
                if page_num == 1:
                    print(f"  First 500 chars of results HTML: {results_html[:500]}")
                break

            for card in cards:
                desc = card["desc"]
                all_jobs.append(Job(
                    company                = COMPANY,
                    title                  = card["title"],
                    job_id                 = card["job_id"],
                    location               = card["location"],
                    country                = _infer_country(card["location"]),
                    salary                 = extract_salary(desc),
                    remote                 = infer_remote(card["location"], desc),
                    seniority              = infer_seniority(card["title"]),
                    us_citizenship_required= extract_citizenship(desc),
                    security_clearance     = extract_clearance(desc),
                    relocation_assistance  = extract_relocation(desc),
                    source_platform        = "talentbrew",
                    date_posted            = "N/A",
                    apply_url              = card["apply_url"],
                    description_text       = desc,
                ))

            print(f"  Page {page_num}/{total_pages or '?'} — {len(all_jobs)} jobs collected")

            if total_pages and page_num >= total_pages:
                break

            page_num += 1

        await browser.close()

    # Save per company - iterate jobs by company
    from collections import defaultdict
    jobs_by_company = defaultdict(list)
    for job in all_jobs:
        jobs_by_company[job.company].append(job)
    total = 0
    for company_jobs in jobs_by_company.values():
        if not company_jobs:
            continue
        if not sample_check(company_jobs[:20], company_jobs[0].company, "talentbrew"):
            continue
        key = company_jobs[0].company.lower().replace(" ", "_").replace("(", "").replace(")", "")
        output_path = output_dir / f"talentbrew_{key}.csv"
        save_jobs(company_jobs, output_path)
        total += len(company_jobs)
    print(f"\nL3Harris: {len(all_jobs)} jobs saved → {output}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape TalentBrew ATS companies")
    parser.add_argument("--companies", nargs="*", default=list(COMPANIES.keys()),
                        help=f"Companies to scrape. Options: {', '.join(COMPANIES.keys())}")
    parser.add_argument("--output-dir", type=Path, default=Path("data"),
                        help="Directory for output files (one CSV per company)")
    args = parser.parse_args()

    # Run the selected company config
    config = None
    if len(args.companies) == 1:
        config = COMPANIES.get(args.companies[0])
        if not config:
            print(f"Unknown company: {args.companies[0]}. Available: {', '.join(COMPANIES.keys())}")
            import sys; sys.exit(1)
    else:
        # Default to l3harris for backward compat
        config = COMPANIES.get("l3harris")

    asyncio.run(scrape(args.output_dir, config=config))
